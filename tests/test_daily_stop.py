"""Персистентный дневной paper-стоп на капитале после исполнения стопов."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_paper_cycle_integration import _cycle, _inputs, _market, _tick
from tests.test_paper_feed import FakeGateway, _feed, _populate_ticker
from tests.test_risk_manager import _account as risk_account
from tests.test_risk_manager import _config, _instrument
from trading_bot import database
from trading_bot.broker.paper import PaperBroker
from trading_bot.executor import PaperTick
from trading_bot.paper_account import PaperAccountSource
from trading_bot.portfolio import Portfolio
from trading_bot.risk_manager import OpenPositionState, capital_at_stops, check
from trading_bot.utils import MSK, SESSION_TRADING_START


def _round_trip(
    portfolio: Portfolio, *, entry: Decimal, exit_price: Decimal, lots: int, at: datetime
) -> None:
    """Создаёт реальный FIFO-результат, а не меняет cash вручную в SQLite."""
    portfolio.open_position(
        ticker="OTHER",
        side="long",
        lots=lots,
        lot_size=1,
        price=entry,
        fee=Decimal(0),
        slippage=Decimal(0),
        stop=entry - Decimal(1),
        opened_at=at,
    )
    portfolio.close_position(
        ticker="OTHER",
        lots=lots,
        exit_price=exit_price,
        fee=Decimal(0),
        slippage=Decimal(0),
        exit_reason="stop",
        closed_at=at,
    )


def test_daily_stop_survives_restart_and_resets_only_after_confirmed_new_day(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "daily_stop.db"
    conn = database.connect(db_path)
    inputs = _inputs()
    now = inputs.now
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(now, {}, {}, {})  # E_day0 сохранён до первой сделки
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    _round_trip(portfolio, entry=Decimal("200"), exit_price=Decimal("100"), lots=30, at=now)
    account = source.snapshot(now, {}, {}, {})
    assert account.balance_free == Decimal("97000")

    refused = _cycle(conn).run_tick(_tick(account=account))
    assert refused.risk_verdict is not None and not refused.risk_verdict.allowed
    assert "daily_loss_limit_not_reached" in [
        item.name for item in refused.risk_verdict.checks if not item.passed
    ]
    assert conn.execute(
        "SELECT trading_day, capital_at_stops, threshold_equity FROM paper_daily_stop_latches"
    ).fetchone() == (now.date().isoformat(), "97000", "97000.00")
    conn.close()

    conn = database.connect(db_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    _round_trip(portfolio, entry=Decimal("100"), exit_price=Decimal("200"), lots=20, at=now)
    recovered = source.snapshot(now, {}, {}, {})
    assert recovered.balance_free > Decimal("97000")
    assert recovered.daily_stop_active
    repeat = _cycle(conn).run_tick(_tick(inputs=replace(inputs, ticker="GAZP"), account=recovered))
    assert repeat.risk_verdict is not None and not repeat.risk_verdict.allowed
    assert not repeat.fill

    tomorrow = now + timedelta(days=1)
    unconfirmed = source.snapshot(tomorrow, {}, {}, {}, trading_day_confirmed=False)
    assert unconfirmed.daily_anchor_day == now.date()
    assert unconfirmed.daily_stop_active and not unconfirmed.trading_day_confirmed
    assert not check(
        side="long", account=unconfirmed, instrument=_instrument(), config=_config()
    ).allowed
    assert conn.execute("SELECT COUNT(*) FROM paper_daily_stop_latches").fetchone() == (1,)

    confirmed = source.snapshot(tomorrow, {}, {}, {}, trading_day_confirmed=True)
    assert confirmed.daily_anchor_day == tomorrow.date()
    assert confirmed.daily_start_balance == recovered.balance_free
    assert not confirmed.daily_stop_active and confirmed.trading_day_confirmed
    next_inputs = replace(
        inputs,
        now=tomorrow,
        candles_5m=[replace(c, ts_open=c.ts_open + timedelta(days=1)) for c in inputs.candles_5m],
        candles_15m=[replace(c, ts_open=c.ts_open + timedelta(days=1)) for c in inputs.candles_15m],
        candles_1h=[replace(c, ts_open=c.ts_open + timedelta(days=1)) for c in inputs.candles_1h],
    )
    resumed = _cycle(conn).run_tick(_tick(inputs=next_inputs, account=confirmed, market=_market()))
    assert resumed.risk_verdict is not None and resumed.risk_verdict.allowed
    assert resumed.trade_id is not None
    assert conn.execute("SELECT COUNT(*) FROM paper_daily_stop_latches").fetchone() == (1,)
    conn.close()


def test_evaluate_failure_keeps_daily_stop_after_transaction_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from trading_bot import executor

    conn = database.connect(tmp_path / "evaluate_and_stop.db")
    now = _inputs().now
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(now, {}, {}, {})
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    _round_trip(portfolio, entry=Decimal("200"), exit_price=Decimal("100"), lots=30, at=now)

    def fail_evaluate(**_kwargs: object) -> object:
        raise AssertionError("private evaluation detail")

    monkeypatch.setattr(executor, "evaluate", fail_evaluate)
    with pytest.raises(AssertionError, match="private evaluation detail"):
        _cycle(conn).run_tick(_tick(account=source.snapshot(now, {}, {}, {})))
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM paper_daily_stop_latches").fetchone() == (1,)
    assert conn.execute("SELECT stage, error_type FROM paper_source_episodes").fetchone() == (
        "strategy_evaluate",
        "AssertionError",
    )
    assert "private evaluation detail" not in caplog.text
    conn.close()


def test_feed_does_not_reset_stop_without_closed_opening_bar(tmp_path: Path) -> None:
    from tests.test_paper_feed import _NOW as feed_now

    conn = database.connect(tmp_path / "feed_daily.db")
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(feed_now, {}, {}, {})
    conn.execute(
        "INSERT INTO paper_daily_stop_latches VALUES (?, ?, ?, ?)",
        (feed_now.astimezone(MSK).date().isoformat(), feed_now.isoformat(), "97000", "97000"),
    )
    conn.commit()
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    tomorrow = feed_now + timedelta(days=1)
    for timeframe in ("5m", "15m", "1h"):
        key = ("SBER_ID", timeframe)
        gw.candles[key] = [
            replace(candle, ts_open=candle.ts_open + timedelta(days=1))
            for candle in gw.candles[key]
        ]
    gw.quotes["SBER_ID"] = replace(gw.quotes["SBER_ID"], observed_at=tomorrow)
    key = ("SBER_ID", "5m")
    complete_history = gw.candles[key]
    gw.candles[key] = [
        candle
        for candle in complete_history
        if candle.ts_open.astimezone(MSK).time() != SESSION_TRADING_START
    ]
    incomplete = _feed(gw, source, approved=["SBER"], clock=tomorrow).next_paper_tick()
    assert isinstance(incomplete, PaperTick)
    assert incomplete.account.daily_stop_active
    assert not incomplete.account.trading_day_confirmed
    assert incomplete.account.daily_anchor_day == feed_now.astimezone(MSK).date()
    assert conn.execute(
        "SELECT COUNT(*) FROM paper_equity_anchors WHERE anchor_type='daily'"
    ).fetchone() == (1,)

    gw.candles[key] = complete_history
    confirmed = _feed(gw, source, approved=["SBER"], clock=tomorrow).next_paper_tick()
    assert isinstance(confirmed, PaperTick)
    assert confirmed.account.trading_day_confirmed
    assert confirmed.account.daily_anchor_day == tomorrow.astimezone(MSK).date()
    assert not confirmed.account.daily_stop_active
    assert conn.execute("SELECT COUNT(*) FROM paper_daily_stop_latches").fetchone() == (1,)
    conn.close()


@pytest.mark.parametrize("exit_price", [Decimal("90"), Decimal("75")])
def test_paper_stop_budget_holds_at_stop_but_gap_can_exceed_it(
    tmp_path: Path, exit_price: Decimal
) -> None:
    config = _config(max_position_pct=Decimal("1"))
    account = risk_account(balance_free=Decimal("98000"), daily_start_balance=Decimal("100000"))
    instrument = _instrument(price=Decimal("100"), stop_price=Decimal("90"), lot_size=1)
    verdict = check(side="long", account=account, instrument=instrument, config=config)
    assert verdict.allowed and verdict.max_lots > 0
    broker = PaperBroker(fee_rate=config.fee_rate, slippage_assumed=config.slippage_assumed)
    entry = broker.fill_market_order(
        side="long", requested_lots=verdict.max_lots, lot_size=1, reference_price=Decimal("100")
    )
    exit_fill = broker.fill_market_order(
        side="short", requested_lots=verdict.max_lots, lot_size=1, reference_price=exit_price
    )
    conn = database.connect(tmp_path / f"gap_{exit_price}.db")
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("98000"))
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=verdict.max_lots,
        lot_size=1,
        price=Decimal("100"),
        fee=entry.fee,
        slippage=entry.slippage_cost,
        stop=Decimal("90"),
        opened_at=_inputs().now,
    )
    held = OpenPositionState("SBER", "long", verdict.max_lots, 1, Decimal("90"), Decimal("100"))
    stop_capital = capital_at_stops(
        risk_account(
            balance_free=portfolio.balance,
            open_positions=(held,),
            open_positions_count=1,
            open_tickers=frozenset({"SBER"}),
        ),
        config,
    )
    assert stop_capital is not None and stop_capital >= Decimal("97000")
    portfolio.close_position(
        ticker="SBER",
        lots=verdict.max_lots,
        exit_price=exit_price,
        fee=exit_fill.fee,
        slippage=exit_fill.slippage_cost,
        exit_reason="stop",
        closed_at=_inputs().now,
    )
    assert (portfolio.balance >= Decimal("97000")) == (exit_price == Decimal("90"))
    conn.close()
