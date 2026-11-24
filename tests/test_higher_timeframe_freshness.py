"""Expected closed regular-session buckets, exercised through the public paper feed."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from tests.test_paper_feed import (
    FakeGateway,
    _account_source,
    _candles_at,
    _classifier,
    _config,
    _quote,
    _session_grid,
    _share,
)
from trading_bot import database
from trading_bot.api_client import Candle, Timeframe
from trading_bot.executor import PaperTick
from trading_bot.market_regime import RegimeBar
from trading_bot.paper_account import PaperAccountSource
from trading_bot.paper_feed import PaperTickFeed, _higher_timeframe_freshness
from trading_bot.source_events import SourceRefusal
from trading_bot.utils import MSK, load_known_holidays

_SEED = Path(__file__).resolve().parent.parent / "data_seed/holidays_ru.md"


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Use only a fresh pytest temporary database, never a runtime database."""
    conn = database.connect(tmp_path / "freshness.db")
    yield conn
    conn.close()


def _setup(
    connection: sqlite3.Connection, now: datetime, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeGateway, PaperTickFeed, list[list[Candle]]]:
    gateway = FakeGateway()
    share = _share("VTBR")
    gateway.shares["VTBR"] = share
    local = now.astimezone(MSK)
    for timeframe, minutes in (("5m", 5), ("15m", 15), ("1h", 60)):
        grid = _session_grid(local.date(), end=local.time(), minutes=minutes)
        gateway.candles[(share.instrument_id, timeframe)] = _candles_at(
            grid, start_price=Decimal("100"), step=Decimal("0.05")
        )
    gateway.candles[("IMOEX_ID", "15m")] = list(gateway.candles[(share.instrument_id, "15m")])
    gateway.quotes[share.instrument_id] = _quote(Decimal("99.95"), Decimal("100.05"), now)
    classifier = _classifier()
    classified: list[list[Candle]] = []

    def classify(candles: list[Candle]) -> RegimeBar:
        classified.append(list(candles))
        return RegimeBar(Decimal("0.2"), Decimal("0.5"), "normal")

    monkeypatch.setattr(classifier, "evaluate", classify)
    feed = PaperTickFeed(
        gateway=gateway,  # type: ignore[arg-type]
        account_source=_account_source(connection),
        regime_classifier=classifier,
        strategy_config=_config(),  # type: ignore[arg-type]
        approved_tickers=["VTBR"],
        index_instrument_id="IMOEX_ID",
        known_holidays=frozenset(load_known_holidays(_SEED)),
        clock=lambda: now,
        correlation_window=3,
        volume_avg_window=3,
        max_bar_staleness=timedelta(minutes=10),
        max_quote_staleness=timedelta(minutes=5),
    )
    return gateway, feed, classified


@pytest.mark.parametrize("minute", [27, 44])
def test_current_closed_hour_and_quarter_survive_open_bar_phase(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, minute: int
) -> None:
    now = datetime(2026, 10, 16, 15, minute, tzinfo=UTC)
    _gateway, feed, classified = _setup(connection, now, monkeypatch)

    tick = feed.next_paper_tick()

    assert isinstance(tick, PaperTick)
    assert tick.strategy.candles_1h[-1].ts_open == now.replace(hour=14, minute=0)
    assert classified and classified[-1][-1].ts_open == now.replace(
        minute=0 if minute == 27 else 15
    )
    market = tick.strategy.market_filter
    assert market is not None and market.market_regime == "normal"


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("boundary_minute", [0, 15])
def test_exact_bucket_boundary_uses_previous_fully_closed_bar(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    offset: int,
    boundary_minute: int,
) -> None:
    boundary = datetime(2026, 10, 16, 16, boundary_minute, tzinfo=UTC)
    now = boundary + timedelta(microseconds=offset)
    _gateway, feed, classified = _setup(connection, now, monkeypatch)

    tick = feed.next_paper_tick()

    assert isinstance(tick, PaperTick)
    quarter_close = boundary if offset >= 0 else boundary - timedelta(minutes=15)
    assert tick.strategy.candles_15m[-1].ts_open == quarter_close - timedelta(minutes=15)
    hour_close = now.replace(minute=0, second=0, microsecond=0)
    assert tick.strategy.candles_1h[-1].ts_open == hour_close - timedelta(hours=1)
    assert classified[-1] == tick.strategy.candles_15m


@pytest.mark.parametrize("timeframe", ["15m", "1h"])
@pytest.mark.parametrize("defect", ["missing_one", "incomplete", "unclosed", "future", "off_grid"])
def test_missing_expected_bar_never_authorizes_quotes_or_account(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    timeframe: str,
    defect: str,
) -> None:
    now = datetime(2026, 10, 16, 16, 1, tzinfo=UTC)
    gateway, feed, classified = _setup(connection, now, monkeypatch)
    key = ("VTBR_ID", timeframe)
    history = gateway.candles[key]
    expected = history.pop()
    if defect == "incomplete":
        history.append(replace(expected, is_complete=False))
    elif defect == "unclosed":
        history.append(replace(expected, ts_open=now.replace(minute=0)))
    elif defect == "future":
        history.append(replace(expected, ts_open=now + timedelta(hours=1)))
    elif defect == "off_grid":
        history.append(replace(expected, ts_open=expected.ts_open + timedelta(seconds=1)))
    quote = Mock(side_effect=AssertionError("HTF stop must precede quotes"))
    account = Mock(side_effect=AssertionError("HTF stop must precede account snapshot"))
    monkeypatch.setattr(gateway, "get_order_book_quote", quote)
    monkeypatch.setattr(PaperAccountSource, "snapshot", account)

    for _ in range(2):  # Refusal must not acknowledge/deduplicate this 5m bar.
        result = feed.next_paper_tick()
        assert isinstance(result, SourceRefusal)
        assert result.stage == "higher_timeframe_stale" and result.action == "stop"
        assert result.source_bar_ts == now.replace(minute=55, hour=15)
    quote.assert_not_called()
    account.assert_not_called()
    assert not classified


@pytest.mark.parametrize("hour,minute,timeframe", [(7, 5, "15m"), (7, 27, "1h"), (15, 27, "1h")])
def test_previous_day_never_supplies_missing_current_session_bucket(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    hour: int,
    minute: int,
    timeframe: str,
) -> None:
    now = datetime(2026, 10, 16, hour, minute, tzinfo=UTC)
    gateway, feed, _classified = _setup(connection, now, monkeypatch)
    template = gateway.candles[("VTBR_ID", "5m")][-1]
    previous = now.replace(hour=19, minute=0) - timedelta(days=1)
    gateway.candles[("VTBR_ID", timeframe)] = [replace(template, ts_open=previous)]

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    assert result.stage == "higher_timeframe_stale" and result.action == "stop"


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 10, 17, 15, 27, tzinfo=UTC),  # weekend
        datetime(2026, 11, 4, 15, 27, tzinfo=UTC),  # blocked special session
        datetime(2027, 3, 2, 15, 27, tzinfo=UTC),  # unknown calendar year
    ],
)
def test_calendar_blocked_never_builds_or_classifies_a_tick(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch, now: datetime
) -> None:
    gateway, feed, classified = _setup(connection, now, monkeypatch)
    quote = Mock(side_effect=AssertionError("calendar blocked"))
    monkeypatch.setattr(gateway, "get_order_book_quote", quote)

    assert feed.next_paper_tick() is None
    assert not classified
    quote.assert_not_called()


@pytest.mark.parametrize("defect", ["missing_one", "incomplete", "off_grid", "empty", "corrupt"])
def test_index_missing_expected_bucket_is_chaos_not_source_refusal(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    defect: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 10, 16, 16, 1, tzinfo=UTC)
    gateway, feed, classified = _setup(connection, now, monkeypatch)
    history = gateway.candles[("IMOEX_ID", "15m")]
    if defect == "missing_one":
        history.pop()
    elif defect == "incomplete":
        history[-1] = replace(history[-1], is_complete=False)
    elif defect == "off_grid":
        history[-1] = replace(history[-1], ts_open=history[-1].ts_open + timedelta(seconds=1))
    elif defect == "empty":
        history.clear()
    else:
        history.append(history[-1])

    tick = feed.next_paper_tick()

    assert isinstance(tick, PaperTick)
    market = tick.strategy.market_filter
    assert market is not None and market.paused and market.market_regime == "chaos"
    assert not market.allow_long and not market.allow_short
    assert market.chaos_score is None and market.chaos_threshold is None
    assert not classified
    record = next(r for r in caplog.records if "event=higher_timeframe_freshness" in r.message)
    assert record.__dict__["ticker"] == "IMOEX"
    assert record.__dict__["instrument_id"] == "IMOEX_ID"
    assert record.__dict__["expected_open"] == now.replace(hour=15, minute=45).isoformat()
    assert record.__dict__["action"] == "chaos"


def test_index_classifier_receives_only_closed_bars_and_permission_is_not_cached(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime(2026, 10, 16, 15, 44, tzinfo=UTC)
    gateway, feed, classified = _setup(connection, now, monkeypatch)
    history = gateway.candles[("IMOEX_ID", "15m")]
    valid = list(history)
    history.extend(
        [
            replace(history[-1], ts_open=now.replace(minute=30)),
            replace(history[-1], ts_open=now + timedelta(hours=1)),
        ]
    )

    first = feed.next_paper_tick()
    assert isinstance(first, PaperTick)
    assert classified == [valid]
    gateway.candles[("IMOEX_ID", "15m")] = valid[:-1]
    second = feed.next_paper_tick()  # same 5m bucket, deliberately not acked
    assert isinstance(second, PaperTick)
    assert classified == [valid]
    assert second.strategy.market_filter is not None
    assert second.strategy.market_filter.paused
    assert second.strategy.market_filter.market_regime == "chaos"


def test_stale_diagnostic_contains_exact_grid_and_source_context(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    now = datetime(2026, 10, 16, 16, 1, tzinfo=UTC)
    gateway, feed, _classified = _setup(connection, now, monkeypatch)
    gateway.candles[("VTBR_ID", "1h")].pop()

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    record = next(r for r in caplog.records if "event=higher_timeframe_freshness" in r.message)
    fields = record.__dict__
    assert fields["ticker"] == "VTBR" and fields["instrument_id"] == "VTBR_ID"
    assert fields["timeframe"] == "1h" and fields["now"] == now.isoformat()
    assert fields["source_5m_bar"] == now.replace(hour=15, minute=55).isoformat()
    assert fields["latest_open"] == now.replace(hour=14, minute=0).isoformat()
    assert fields["latest_close"] == now.replace(hour=15, minute=0).isoformat()
    assert fields["expected_open"] == now.replace(hour=15, minute=0).isoformat()
    assert fields["expected_close"] == now.replace(hour=16, minute=0).isoformat()
    assert fields["lag_seconds"] == 3600
    assert fields["policy"] == "latest_fully_closed_regular_session_bucket"
    assert fields["reason"] == "not_latest_expected_closed_bar" and fields["action"] == "stop"


@pytest.mark.parametrize(
    "timeframe,expected_hour,expected_minute", [("1h", 19, 0), ("15m", 20, 15)]
)
@pytest.mark.parametrize("hour,minute", [(20, 39), (20, 40), (20, 59)])
def test_expected_bucket_is_capped_at_regular_session_end(
    timeframe: Timeframe, expected_hour: int, expected_minute: int, hour: int, minute: int
) -> None:
    now = datetime(2026, 10, 16, hour, minute, tzinfo=UTC)
    expected = now.replace(hour=expected_hour, minute=expected_minute)
    candles = _candles_at([expected], start_price=Decimal("100"), step=Decimal("1"))

    result = _higher_timeframe_freshness(
        candles, timeframe=timeframe, now=now, known_holidays=frozenset(load_known_holidays(_SEED))
    )

    assert result.fresh and result.expected_open == expected
