"""Интеграционные проверки paper-only цикла от свечей до SQLite-портфеля."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_risk_manager import _account as risk_account
from tests.test_risk_manager import _config as risk_config
from tests.test_strategy import _config as strategy_config
from tests.test_strategy_filters_integration import _ranging_candles, _real_candles
from trading_bot import database
from trading_bot.api_client import NORMAL_TRADING_STATUS
from trading_bot.executor import (
    PaperCycle,
    PaperMarketContext,
    PaperRecoveryRequired,
    PaperTick,
    StrategyInputs,
)
from trading_bot.portfolio import Portfolio
from trading_bot.risk_manager import AccountState, OpenPositionState
from trading_bot.strategy import MarketFilterResult

_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def _market(**overrides: object) -> PaperMarketContext:
    values: dict[str, object] = {
        "sector": "financials",
        "lot_size": 10,
        "min_price_increment": Decimal("0.01"),
        "average_volume_lots": Decimal("10000"),
        "currency": "RUB",
        "spread_bps": Decimal("10"),
        "trading_status": NORMAL_TRADING_STATUS,
        "max_correlation_with_open": Decimal("0.1"),
        "available_lots": None,
    }
    values.update(overrides)
    return PaperMarketContext(**values)  # type: ignore[arg-type]


def _inputs(
    *, ranging: bool = False, side: str = "long", session_open: bool = True
) -> StrategyInputs:
    candles_5m, candles_15m, candles_1h = (
        _ranging_candles(side=side) if ranging else _real_candles()
    )
    return StrategyInputs(
        ticker="SBER",
        candles_5m=candles_5m,
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        config=strategy_config(),
        now=_NOW,
        is_session_open=session_open,
        session_reason="обычная сессия" if session_open else "сессия закрыта",
        short_enabled=True,
        session_guard_active=False,
        gap_result=None,
        market_filter=MarketFilterResult(
            allow_long=True,
            allow_short=True,
            paused=False,
            reason="breadth нейтральный",
            breadth=Decimal("0.5"),
        ),
    )


def _account_snapshot(*, balance: Decimal = Decimal("100000")) -> AccountState:
    return risk_account(
        balance_free=balance,
        daily_start_balance=balance,
        starting_equity=balance,
        daily_anchor_day=_NOW.date(),
    )


def _cycle(
    conn: sqlite3.Connection, *, starting_balance: Decimal = Decimal("100000")
) -> PaperCycle:
    return PaperCycle(
        conn,
        starting_balance=starting_balance,
        strategy_config=strategy_config(),
        risk_config=risk_config(),
    )


def _tick(
    *,
    inputs: StrategyInputs | None = None,
    account: AccountState | None = None,
    market: PaperMarketContext | None = None,
) -> PaperTick:
    return PaperTick(
        strategy=inputs or _inputs(),
        account=account or _account_snapshot(),
        market=market or _market(),
    )


def test_strategy_refusal_is_persisted_without_risk_or_fill(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)

    result = cycle.run_tick(_tick(inputs=_inputs(session_open=False)))

    row = conn.execute(
        "SELECT verdict, reason, indicators, mode FROM decisions WHERE id = ?",
        (result.decision_id,),
    ).fetchone()
    assert result.decision.verdict == "no_signal"
    assert result.risk_verdict is None
    assert result.fill is None
    assert result.trade_id is None
    assert row is not None and row[0] == "no_signal" and row[3] == "paper"
    assert json.loads(row[2])["session_reason"] == "сессия закрыта"
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_short_unavailable_is_journaled_without_fill(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    inputs = replace(_inputs(ranging=True, side="short"), short_enabled=False)

    result = _cycle(conn).run_tick(_tick(inputs=inputs))

    assert result.decision.verdict == "no_signal"
    assert "short_availability" in result.decision.filters_failed
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_market_breadth_short_refusal_is_named_in_journal(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    inputs = _inputs(ranging=True, side="short")
    assert inputs.market_filter is not None
    inputs = replace(
        inputs,
        market_filter=replace(
            inputs.market_filter, allow_short=False, reason="breadth запрещает short"
        ),
    )

    result = _cycle(conn).run_tick(_tick(inputs=inputs))

    assert result.decision.verdict == "no_signal"
    assert any(item.startswith("market_breadth_short:") for item in result.decision.filters_failed)
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_market_chaos_values_survive_paper_cycle_and_sqlite_for_signal_and_refusal(
    tmp_path: Path,
) -> None:
    for regime, score, threshold, paused, expected_verdict in (
        ("normal", Decimal("0.52"), Decimal("0.70"), False, "signal"),
        ("chaos", None, None, True, "no_signal"),
    ):
        conn = database.connect(tmp_path / f"{regime}.db")
        inputs = _inputs()
        assert inputs.market_filter is not None
        inputs = replace(
            inputs,
            market_filter=replace(
                inputs.market_filter,
                allow_long=not paused,
                allow_short=not paused,
                paused=paused,
                reason="недостаточно закрытой истории индекса" if paused else "normal",
                chaos_score=score,
                chaos_threshold=threshold,
                market_regime=regime,
            ),
        )
        result = _cycle(conn).run_tick(_tick(inputs=inputs))
        recorded = conn.execute(
            "SELECT verdict, indicators FROM decisions WHERE id = ?", (result.decision_id,)
        ).fetchone()
        assert recorded is not None and recorded[0] == expected_verdict
        snapshot = json.loads(recorded[1])
        assert snapshot["market_regime"] == regime
        assert snapshot["market_chaos_score"] == (str(score) if score is not None else "n/a")
        assert snapshot["market_chaos_threshold"] == (
            str(threshold) if threshold is not None else "n/a"
        )
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == int(not paused)
        conn.close()


def test_risk_refusal_persists_all_checks_and_never_fills(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)

    result = cycle.run_tick(_tick(market=_market(trading_status="HALTED")))

    row = conn.execute(
        "SELECT verdict, filters_failed, size_lots FROM decisions WHERE id = ?",
        (result.decision_id,),
    ).fetchone()
    assert result.decision.verdict == "signal"
    assert result.risk_verdict is not None and not result.risk_verdict.allowed
    assert len(result.risk_verdict.checks) == 12
    assert row is not None and row[0] == "no_signal" and row[2] == 0
    failures = json.loads(row[1])
    assert any(item.startswith("risk:instrument_status_normal:") for item in failures)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_allowed_signal_is_journaled_before_paper_fill_and_linked(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)

    result = cycle.run_tick(_tick())

    assert result.decision.verdict == "signal"
    assert result.risk_verdict is not None and result.risk_verdict.allowed
    assert result.fill is not None and result.fill.filled_lots > 0
    assert result.trade_id is not None
    trade = conn.execute(
        "SELECT journal_id, mode, price, fee, slippage FROM trades WHERE id = ?",
        (result.trade_id,),
    ).fetchone()
    assert trade is not None
    assert trade[0] == result.decision_id
    assert trade[1] == "paper"
    assert Decimal(trade[2]) == _real_candles()[0][-1].close
    assert Decimal(trade[3]) == result.fill.fee
    assert Decimal(trade[4]) == result.fill.slippage_cost
    units = Decimal(result.fill.filled_lots * 10)
    stored_balance = Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).balance
    assert stored_balance == (
        Decimal("100000") - Decimal(trade[2]) * units - result.fill.fee - result.fill.slippage_cost
    )
    assert result.fill.price > Decimal(trade[2])
    assert conn.execute(
        "SELECT verdict, size_lots FROM decisions WHERE id = ?", (result.decision_id,)
    ).fetchone() == ("signal", result.risk_verdict.max_lots)
    conn.close()


def test_ranging_stop_used_by_risk_is_same_level_saved_on_position(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)

    result = cycle.run_tick(
        _tick(
            inputs=_inputs(ranging=True),
            market=_market(min_price_increment=Decimal("0.1")),
        )
    )

    assert result.decision.regime == "ranging"
    assert result.decision.signal is not None
    assert result.risk_verdict is not None and result.risk_verdict.allowed
    assert result.stop_price == Decimal("94.0")
    assert result.decision.signal.stop_price == Decimal("94.03")
    position = Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).get_position(
        "SBER"
    )
    assert position is not None and position.stop == result.stop_price
    assert result.decision.signal.take_price == Decimal("99.475")
    assert position.take == Decimal("99.4")
    conn.close()


def test_short_ranging_has_outward_stop_and_take_without_live_order(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)

    result = cycle.run_tick(
        _tick(
            inputs=_inputs(ranging=True, side="short"),
            market=_market(min_price_increment=Decimal("0.1")),
        )
    )

    assert result.decision.signal is not None and result.decision.signal.side == "short"
    assert result.risk_verdict is not None and result.risk_verdict.allowed
    assert result.stop_price == Decimal("106.0")
    position = Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).get_position(
        "SBER"
    )
    assert position is not None and position.side == "short"
    assert position.stop == result.stop_price
    assert result.decision.signal.take_price == Decimal("100.525")
    assert position.take == Decimal("100.6")
    assert conn.execute("SELECT mode FROM trades").fetchone()[0] == "paper"
    conn.close()


def test_completed_bar_deduplicates_before_account_validation_and_new_bar_is_checked(
    tmp_path: Path,
) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)
    first = cycle.run_tick(_tick())
    assert first.fill is not None and first.fill.filled_lots > 0
    assert first.stop_price is not None
    stored_balance = Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).balance
    account = replace(
        _account_snapshot(balance=stored_balance),
        daily_start_balance=Decimal("100000"),
        starting_equity=Decimal("100000"),
        open_tickers=frozenset({"SBER"}),
        open_positions_count=1,
        open_positions_by_sector={"financials": 1},
        open_positions=(
            OpenPositionState(
                ticker="SBER",
                side="long",
                lots=first.fill.filled_lots,
                lot_size=10,
                stop_price=first.stop_price,
                market_price=_real_candles()[0][-1].close,
            ),
        ),
    )

    tampered_position = replace(account.open_positions[0], stop_price=Decimal("1"))
    second = cycle.run_tick(_tick(account=replace(account, open_positions=(tampered_position,))))
    assert second.already_processed
    assert second.decision_id == first.decision_id
    assert second.fill is None
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1

    inputs = _inputs()
    new_candle = replace(
        inputs.candles_5m[-1], ts_open=inputs.candles_5m[-1].ts_open + timedelta(minutes=5)
    )
    new_inputs = replace(
        inputs, now=_NOW + timedelta(minutes=5), candles_5m=[*inputs.candles_5m, new_candle]
    )
    with pytest.raises(ValueError, match="позиции снимка"):
        cycle.run_tick(
            _tick(inputs=new_inputs, account=replace(account, open_positions=(tampered_position,)))
        )
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM paper_source_episodes").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    conn.close()


def test_position_write_failure_rolls_back_fill_but_preserves_decision(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)
    conn.execute(
        "CREATE TRIGGER fail_position BEFORE INSERT ON positions "
        "BEGIN SELECT RAISE(ABORT, 'position write failed'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="position write failed"):
        cycle.run_tick(_tick())

    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    assert Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).balance == Decimal(
        "100000"
    )
    conn.execute("DROP TRIGGER fail_position")
    conn.commit()
    assert conn.execute("SELECT processing_state FROM decisions").fetchone() == ("failed",)
    with pytest.raises(PaperRecoveryRequired):
        cycle.run_tick(_tick())
    with pytest.raises(PaperRecoveryRequired):
        _cycle(conn)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_journal_failure_propagates_and_prevents_paper_fill(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)
    conn.execute(
        "CREATE TRIGGER reject_decision BEFORE INSERT ON decisions "
        "BEGIN SELECT RAISE(ABORT, 'journal unavailable'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="journal unavailable"):
        cycle.run_tick(_tick())

    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    conn.close()


def test_invalid_exit_level_after_signal_is_journaled_and_stops(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)

    with pytest.raises(ValueError, match="тейк"):
        cycle.run_tick(_tick(market=_market(min_price_increment=Decimal("1000"))))

    row = conn.execute("SELECT verdict, filters_failed FROM decisions").fetchone()
    assert row is not None and row[0] == "no_signal"
    assert "risk_processing_error" in json.loads(row[1])
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_zero_available_lots_keeps_decision_but_no_trade(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    result = _cycle(conn).run_tick(_tick(market=_market(available_lots=0)))
    assert result.risk_verdict is not None and result.risk_verdict.allowed
    assert result.fill is not None and result.fill.filled_lots == 0
    assert result.trade_id is None
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_partial_paper_fill_keeps_requested_size_and_links_actual_lots(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    result = _cycle(conn).run_tick(_tick(market=_market(available_lots=2)))

    assert result.risk_verdict is not None and result.risk_verdict.allowed
    assert result.risk_verdict.max_lots > 2
    assert result.fill is not None and result.fill.filled_lots == 2
    assert result.fill.remaining_lots == result.risk_verdict.max_lots - 2
    requested_lots = conn.execute("SELECT size_lots FROM decisions").fetchone()[0]
    assert requested_lots == result.risk_verdict.max_lots
    assert conn.execute("SELECT lots, journal_id FROM trades").fetchone() == (
        2,
        result.decision_id,
    )
    conn.close()


def test_future_completed_candle_is_not_used_by_strategy_or_fill(tmp_path: Path) -> None:
    baseline_conn = database.connect(tmp_path / "baseline.db")
    baseline = _cycle(baseline_conn).run_tick(_tick())
    baseline_conn.close()
    inputs = _inputs()
    future = replace(
        inputs.candles_5m[-1],
        ts_open=_NOW,
        close=Decimal("500"),
        is_complete=True,
    )
    future_inputs = replace(inputs, candles_5m=[*reversed(inputs.candles_5m), future])
    conn = database.connect(tmp_path / "future.db")

    result = _cycle(conn).run_tick(_tick(inputs=future_inputs))

    assert result.decision == baseline.decision
    assert result.fill == baseline.fill
    assert conn.execute("SELECT price FROM trades").fetchone()[0] == str(
        inputs.candles_5m[-1].close
    )
    conn.close()


def test_non_utc_tick_time_is_saved_as_utc(tmp_path: Path) -> None:
    from trading_bot.utils import MSK

    conn = database.connect(tmp_path / "paper.db")
    inputs = replace(_inputs(), now=_NOW.astimezone(MSK))
    result = _cycle(conn).run_tick(_tick(inputs=inputs))

    assert result.decision.ts == _NOW
    assert conn.execute("SELECT ts_open FROM trades").fetchone()[0] == _NOW.isoformat()
    conn.close()


def test_paper_cycle_rejects_non_paper_account_before_processing(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    cycle = _cycle(conn)
    live_account = replace(_account_snapshot(), trading_mode="live")

    with pytest.raises(ValueError, match="paper"):
        cycle.run_tick(_tick(account=live_account))

    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()
