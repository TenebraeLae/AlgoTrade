"""Confirmed liquidity sessions and monitoring outside the automatic pool."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_auto_bootstrap import _gateway, _index
from tests.test_auto_bootstrap import _share as _sdk_share
from tests.test_paper_feed import (
    _NOW,
    FakeGateway,
    _account_source,
    _candles_at,
    _classifier,
    _config,
    _db,
    _feed,
    _populate_ticker,
    _session_grid,
    _share,
)
from trading_bot import database
from trading_bot.api_client import Candle, CandleQuotaExhaustedError, GatewayError
from trading_bot.config import Settings
from trading_bot.executor import PaperTick
from trading_bot.main import _paper_dependencies
from trading_bot.paper_feed import PaperTickFeed
from trading_bot.pool_refresh import PoolJournal, Universe, utc_slot
from trading_bot.portfolio import Portfolio
from trading_bot.utils import MSK, SESSION_CLOSING_AUCTION_START


def test_monitoring_only_ticker_gets_ticks_but_never_entry_permission(tmp_path: Path) -> None:
    gateway = FakeGateway()
    _populate_ticker(gateway, "SBER")
    _populate_ticker(gateway, "OLD", step=Decimal("-0.05"))
    conn = _db(tmp_path)
    feed = PaperTickFeed(
        gateway=gateway,
        account_source=_account_source(conn),
        regime_classifier=_classifier(),
        strategy_config=_config(),
        approved_tickers=("SBER",),
        monitoring_tickers=("OLD",),
        index_instrument_id="IMOEX",
        clock=lambda: _NOW,
        max_bar_staleness=timedelta(minutes=15),
        max_quote_staleness=timedelta(minutes=5),
        correlation_window=3,
    )
    first = feed.next_paper_tick()
    second = feed.next_paper_tick()
    assert isinstance(first, PaperTick) and first.strategy.ticker == "SBER"
    assert isinstance(second, PaperTick) and second.strategy.ticker == "OLD"
    assert first.strategy.market_filter is not None
    assert first.strategy.market_filter.breadth == Decimal(1)
    assert second.strategy.market_filter is not None
    assert second.strategy.market_filter.breadth == Decimal(1)
    assert second.strategy.market_filter.paused
    assert not second.strategy.market_filter.allow_long
    assert not second.strategy.market_filter.allow_short
    assert "monitoring_only" in second.strategy.market_filter.reason
    assert feed._pool_tickers == ("SBER",)
    assert "OLD_ID" in gateway.status_calls
    conn.close()


@pytest.mark.parametrize("bad_day", ["2026-02-28", "2026-02-23"])
def test_full_bar_grid_on_weekend_or_blocked_special_day_is_not_a_session(
    tmp_path: Path,
    bad_day: str,
) -> None:
    gateway = FakeGateway()
    share = _share("SBER")
    gateway.shares["SBER"] = share
    grid = _session_grid(datetime.fromisoformat(bad_day).date(), end=SESSION_CLOSING_AUCTION_START)
    gateway.candles[(share.instrument_id, "5m")] = _candles_at(
        grid,
        start_price=Decimal("100"),
        step=Decimal("0"),
    )
    conn = _db(tmp_path)
    clock = datetime.fromisoformat(bad_day).replace(hour=12, tzinfo=MSK) + timedelta(days=1)
    feed = _feed(gateway, _account_source(conn), approved=["SBER"], clock=clock)
    feed._known_holidays = frozenset({datetime.fromisoformat(bad_day).date()})
    assert feed.daily_volume_lots(("SBER",), sessions=1) == {"SBER": []}
    conn.close()


def test_full_bootstrap_uses_ten_real_sessions_and_preserves_positions_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    # Last ten regular completed sessions before Friday 2 October. No synthetic day totals.
    sessions = [date(2026, 9, day) for day in (18, 21, 22, 23, 24, 25, 28, 29, 30)]
    sessions.append(date(2026, 10, 1))
    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    tickers = tuple(f"TEST{i:02d}" for i in range(30))
    gateway = _gateway([_sdk_share(ticker) for ticker in tickers], [_index()])
    history: dict[str, list[Candle]] = {}
    for index, ticker in enumerate(tickers):
        history[f"id-{ticker}"] = [
            candle
            for day in sessions
            for candle in _candles_at(
                _session_grid(day, end=SESSION_CLOSING_AUCTION_START),
                start_price=Decimal("100"),
                step=Decimal("0"),
                base_volume=(index + 1) * 100,
            )
        ]

    def candles(
        *, instrument_id: str, timeframe: str, from_: datetime, to: datetime
    ) -> list[Candle]:
        assert timeframe == "5m"
        return [c for c in history[instrument_id] if from_ <= c.ts_open <= to]

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return now

    monkeypatch.setattr(gateway, "get_candles_history", candles)
    monkeypatch.setattr("trading_bot.main.datetime", Clock)
    settings = Settings(
        db_path=tmp_path / "bootstrap.db",
        paper_starting_balance=Decimal("100000"),
        _env_file=None,
    )
    conn = database.connect(settings.db_path)
    # Explicit prior applied identity evidence: legacy ticker-only positions must not
    # silently resolve today's potentially remapped ticker after restart.
    gateway.discover_paper_shares()
    PoolJournal(conn).applied(
        Universe(
            1,
            utc_slot(now),
            ("TEST00",),
            (),
            {"TEST00": gateway.get_share_metadata(ticker="TEST00")},
            "index-uid",
        ),
        now,
        {},
    )
    Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).open_position(
        ticker="TEST00",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=now,
        stop=Decimal("90"),
    )
    conn.close()
    conn = database.connect(settings.db_path)
    deps = _paper_dependencies(settings, conn=conn, gateway=gateway)
    assert deps.next_paper_tick is not None
    feed = deps.next_paper_tick.__self__
    assert feed._pool_tickers == tuple(reversed(tickers[12:]))
    assert feed._tickers == (*feed._pool_tickers, "TEST00")
    assert feed._account_source.open_tickers() == frozenset({"TEST00"})
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE ts_close IS NULL").fetchone()[0] == 1
    assert "event=paper_pool_applied" in caplog.text
    assert "sessions=10" in caplog.text
    assert "monitoring_only=('TEST00',)" in caplog.text
    conn.close()


@pytest.mark.parametrize("error", [GatewayError("transport"), CandleQuotaExhaustedError("quota")])
def test_liquidity_system_failure_stops_before_next_ticker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: GatewayError
) -> None:
    gateway = FakeGateway()
    gateway.shares = {ticker: _share(ticker) for ticker in ("SBER", "NEXT")}
    calls: list[str] = []

    def history(**kwargs: object) -> list[Candle]:
        calls.append(str(kwargs["instrument_id"]))
        raise error

    monkeypatch.setattr(gateway, "get_candles_history", history)
    conn = _db(tmp_path)
    feed = _feed(gateway, _account_source(conn), approved=["SBER", "NEXT"], liquidity_workers=1)
    with pytest.raises(type(error)):
        feed.daily_volume_lots(("SBER", "NEXT"), sessions=1)
    assert calls == ["SBER_ID"]
    conn.close()


def test_successful_empty_history_is_distinct_and_next_ticker_is_still_checked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    gateway = FakeGateway()
    gateway.shares = {ticker: _share(ticker) for ticker in ("SBER", "NEXT")}
    calls: list[str] = []

    def history(**kwargs: object) -> list[Candle]:
        calls.append(str(kwargs["instrument_id"]))
        return []

    monkeypatch.setattr(gateway, "get_candles_history", history)
    conn = _db(tmp_path)
    feed = _feed(gateway, _account_source(conn), approved=["SBER", "NEXT"], liquidity_workers=1)
    assert feed.daily_volume_lots(("SBER", "NEXT"), sessions=1) == {"SBER": [], "NEXT": []}
    assert calls == ["SBER_ID", "NEXT_ID"]
    conn.close()


def test_old_complete_session_cannot_replace_missing_latest_session(tmp_path: Path) -> None:
    gateway = FakeGateway()
    share = _share("SBER")
    gateway.shares["SBER"] = share
    grid = _session_grid(
        datetime(2026, 2, 26, tzinfo=MSK).date(), end=SESSION_CLOSING_AUCTION_START
    )
    gateway.candles[(share.instrument_id, "5m")] = _candles_at(
        grid,
        start_price=Decimal("100"),
        step=Decimal("0"),
    )
    conn = _db(tmp_path)
    feed = _feed(gateway, _account_source(conn), approved=["SBER"])
    assert feed.daily_volume_lots(("SBER",), sessions=1) == {"SBER": []}
    conn.close()
