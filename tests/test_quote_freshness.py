"""Post-RPC quote freshness through the public feed, with deterministic wall clocks."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
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
from trading_bot.api_client import GatewayError, OrderBookQuote
from trading_bot.cancellation import CancellationToken, OperationCancelled
from trading_bot.config import Settings
from trading_bot.executor import PaperTick
from trading_bot.paper_account import PaperAccountSource
from trading_bot.paper_feed import PaperTickFeed
from trading_bot.source_events import SourceRefusal
from trading_bot.utils import MSK

_NOW = datetime(2026, 10, 16, 15, 27, tzinfo=UTC)


@dataclass
class Clock:
    """Expose a wall clock which the fake RPC can advance."""

    now: datetime

    def __call__(self) -> datetime:
        """Return the current fake wall time."""
        return self.now


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """Isolate all account state from runtime databases."""
    conn = database.connect(tmp_path / "quote.db")
    yield conn
    conn.close()


def _setup(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    *,
    now: datetime = _NOW,
    delay: timedelta = timedelta(seconds=3),
    age: timedelta = timedelta(0),
    limit: timedelta = timedelta(seconds=30),
) -> tuple[FakeGateway, PaperTickFeed, Clock]:
    clock = Clock(now)
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

    def quote(*, instrument_id: str) -> OrderBookQuote:
        clock.now += delay
        return _quote(Decimal("99.95"), Decimal("100.05"), clock.now - age)

    monkeypatch.setattr(gateway, "get_order_book_quote", quote)
    feed = PaperTickFeed(
        gateway=gateway,  # type: ignore[arg-type]
        account_source=_account_source(connection),
        regime_classifier=_classifier(),
        strategy_config=_config(),  # type: ignore[arg-type]
        approved_tickers=["VTBR"],
        index_instrument_id="IMOEX_ID",
        clock=clock,
        correlation_window=3,
        volume_avg_window=3,
        max_bar_staleness=timedelta(minutes=10),
        max_quote_staleness=limit,
    )
    return gateway, feed, clock


def test_quote_observed_after_tick_start_is_fresh_at_response(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _gateway, feed, _clock = _setup(connection, monkeypatch, age=timedelta(seconds=1))

    tick = feed.next_paper_tick()

    assert isinstance(tick, PaperTick)
    assert tick.strategy.now == _NOW
    assert tick.strategy.candles_5m[-1].ts_open == _NOW.replace(minute=20)


@pytest.mark.parametrize(
    "age,accepted,reason",
    [
        (timedelta(0), True, "fresh"),
        (timedelta(seconds=30), True, "fresh"),
        (timedelta(seconds=30, microseconds=1), False, "stale"),
        (timedelta(microseconds=-1), False, "future"),
    ],
)
def test_response_age_exact_boundaries_and_safe_diagnostics(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    age: timedelta,
    accepted: bool,
    reason: str,
) -> None:
    _gateway, feed, clock = _setup(connection, monkeypatch, age=age)
    caplog.set_level(logging.INFO, logger="trading_bot.paper_feed")

    result = feed.next_paper_tick()

    assert isinstance(result, PaperTick if accepted else SourceRefusal)
    records = [r for r in caplog.records if "event=quote_freshness" in r.message]
    assert len(records) == 1
    record = records[0]
    expected = {
        "tick_started_at": _NOW.isoformat(),
        "request_started_at": _NOW.isoformat(),
        "response_received_at": clock.now.isoformat(),
        "quote_observed_at": (clock.now - age).isoformat(),
        "age_sec": age.total_seconds(),
        "max_age_sec": 30,
        "ticker": "VTBR",
        "instrument_id": "VTBR_ID",
        "source_bar_ts": _NOW.replace(minute=20).isoformat(),
        "reason": reason,
        "action": "continue" if accepted else "stop",
    }
    for key, value in expected.items():
        assert record.__dict__[key] == value
        assert f"{key}=" in record.message
    assert record.levelno == (logging.INFO if accepted else logging.WARNING)
    assert record.exc_info is None


@pytest.mark.parametrize("limit,accepted", [(17, False), (45, True)])
def test_custom_quote_limit_is_respected_without_changing_settings_default(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
    accepted: bool,
) -> None:
    assert Settings.model_fields["paper_max_quote_staleness_sec"].default == 30
    _gateway, feed, _clock = _setup(
        connection, monkeypatch, age=timedelta(seconds=31), limit=timedelta(seconds=limit)
    )
    assert isinstance(feed.next_paper_tick(), PaperTick if accepted else SourceRefusal)


def test_quote_fresh_at_tick_start_but_stale_at_response_is_refused_without_ack(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    gateway, feed, clock = _setup(
        connection, monkeypatch, delay=timedelta(seconds=20), age=timedelta(seconds=40)
    )
    account = Mock(side_effect=AssertionError("no account after stale quote"))
    monkeypatch.setattr(PaperAccountSource, "snapshot", account)
    for _ in range(2):
        clock.now = _NOW
        result = feed.next_paper_tick()
        assert isinstance(result, SourceRefusal)
        assert (result.stage, result.action, result.error_type) == (
            "quote",
            "stop",
            "StaleDataError",
        )
        assert result.source_bar_ts == _NOW.replace(minute=20)
    account.assert_not_called()
    assert gateway.status_calls == ["VTBR_ID", "VTBR_ID"]


def test_request_clock_is_sampled_after_slow_tick_setup(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway, feed, clock = _setup(connection, monkeypatch)
    original_status = gateway.get_trading_status

    def status(*, instrument_id: str) -> str:
        clock.now += timedelta(seconds=12)
        return original_status(instrument_id=instrument_id)

    monkeypatch.setattr(gateway, "get_trading_status", status)
    caplog.set_level(logging.INFO, logger="trading_bot.paper_feed")
    assert isinstance(feed.next_paper_tick(), PaperTick)
    record = next(r for r in caplog.records if "event=quote_freshness" in r.message)
    assert record.__dict__["request_started_at"] == (_NOW + timedelta(seconds=12)).isoformat()
    assert record.__dict__["response_received_at"] == (_NOW + timedelta(seconds=15)).isoformat()


@pytest.mark.parametrize(
    "defect",
    ["request_rollback", "response_rollback", "request_naive", "response_naive", "quote_naive"],
)
def test_invalid_timestamps_and_wall_clock_rollback_fail_closed(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    defect: str,
) -> None:
    gateway, feed, clock = _setup(connection, monkeypatch)
    original_status = gateway.get_trading_status

    def status(*, instrument_id: str) -> str:
        if defect == "request_rollback":
            clock.now -= timedelta(microseconds=1)
        elif defect == "request_naive":
            clock.now = clock.now.replace(tzinfo=None)
        return original_status(instrument_id=instrument_id)

    def quote(*, instrument_id: str) -> OrderBookQuote:
        if defect == "response_rollback":
            clock.now -= timedelta(microseconds=1)
        elif defect == "response_naive":
            clock.now = clock.now.replace(tzinfo=None)
        observed = _NOW.replace(tzinfo=None) if defect == "quote_naive" else _NOW
        return _quote(Decimal("99.95"), Decimal("100.05"), observed)

    monkeypatch.setattr(gateway, "get_trading_status", status)
    monkeypatch.setattr(gateway, "get_order_book_quote", quote)
    account = Mock(side_effect=AssertionError("invalid time must stop"))
    monkeypatch.setattr(PaperAccountSource, "snapshot", account)
    result = feed.next_paper_tick()
    assert isinstance(result, SourceRefusal) and result.stage == "quote"
    assert result.source_bar_ts == _NOW.replace(minute=20)
    account.assert_not_called()
    reason = "clock_rollback" if "rollback" in defect else "invalidtimestamp"
    record = next(r for r in caplog.records if "event=quote_freshness" in r.message)
    assert record.__dict__["reason"] == reason and record.__dict__["action"] == "stop"
    assert record.levelno == logging.WARNING and record.exc_info is None


@pytest.mark.parametrize(
    "when", ["before_tick", "before_quote", "inside_rpc", "rpc_error", "rpc_cancelled"]
)
def test_cancellation_is_unwrapped_and_prevents_next_work(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    when: str,
) -> None:
    gateway, feed, clock = _setup(connection, monkeypatch)
    token = CancellationToken()
    monkeypatch.setattr(gateway, "check_cancelled", token.check)
    original_status = gateway.get_trading_status
    account = Mock(side_effect=AssertionError("no account after cancellation"))
    market_read = Mock(side_effect=AssertionError("no market read after cancellation"))
    quote_calls: list[str] = []

    def status(*, instrument_id: str) -> str:
        if when == "before_quote":
            token.request_stop()
        return original_status(instrument_id=instrument_id)

    def quote(*, instrument_id: str) -> OrderBookQuote:
        quote_calls.append(instrument_id)
        token.request_stop()
        monkeypatch.setattr(gateway, "get_candles_history", market_read)
        if when == "rpc_error":
            raise GatewayError("SECRET_UNSAFE_RPC_TEXT")
        if when == "rpc_cancelled":
            token.check()
        clock.now += timedelta(seconds=3)
        return _quote(Decimal("99.95"), Decimal("100.05"), clock.now)

    monkeypatch.setattr(gateway, "get_trading_status", status)
    monkeypatch.setattr(gateway, "get_order_book_quote", quote)
    monkeypatch.setattr(PaperAccountSource, "snapshot", account)
    if when == "before_tick":
        token.request_stop()
    with pytest.raises(OperationCancelled):
        feed.next_paper_tick()
    assert len(quote_calls) == (0 if when in ("before_tick", "before_quote") else 1)
    account.assert_not_called()
    market_read.assert_not_called()
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)
    assert "SECRET_UNSAFE_RPC_TEXT" not in caplog.text
    assert "event=quote_freshness" not in caplog.text


@pytest.mark.parametrize(
    "now,delay,guard,reason",
    [
        (_NOW, timedelta(minutes=4), 0, "source_bar_stale"),
        (_NOW.replace(minute=29, second=59), timedelta(seconds=1), 0, "higher_timeframe_stale"),
        (_NOW.replace(minute=59, second=59), timedelta(seconds=1), 0, "higher_timeframe_stale"),
        (_NOW.replace(hour=20, minute=39, second=59), timedelta(seconds=1), 0, "session_closed"),
        (_NOW.replace(hour=20, minute=29, second=59), timedelta(seconds=1), 10, "session_guard"),
    ],
)
def test_slow_quote_cannot_reuse_expired_context_permission(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    now: datetime,
    delay: timedelta,
    guard: int,
    reason: str,
) -> None:
    gateway, feed, _clock = _setup(connection, monkeypatch, now=now, delay=delay)
    # Explicit construction normally injects this config; adjust only the test fixture.
    feed._strategy_config = _config(session_guard_min=guard)  # type: ignore[assignment]
    account = Mock(side_effect=AssertionError("expired context must stop before account"))
    monkeypatch.setattr(PaperAccountSource, "snapshot", account)
    result = feed.next_paper_tick()
    assert isinstance(result, SourceRefusal) and result.stage == "quote" and result.action == "stop"
    account.assert_not_called()
    assert len(gateway.status_calls) == 1  # No additional SDK status read.
    record = next(r for r in caplog.records if "event=quote_freshness" in r.message)
    assert record.__dict__["reason"] == reason


def test_crossing_five_minute_boundary_is_not_itself_a_refusal(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = _NOW.replace(minute=24, second=59)
    _gateway, feed, _clock = _setup(connection, monkeypatch, now=now, delay=timedelta(seconds=1))
    result = feed.next_paper_tick()
    assert isinstance(result, PaperTick)
    assert result.strategy.now == now
    assert result.strategy.candles_5m[-1].ts_open == now.replace(minute=15, second=0)
