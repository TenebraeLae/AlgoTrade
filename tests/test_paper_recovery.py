"""Персистентная защита paper-решения от повтора после рестарта."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest

from tests.test_paper_cycle_integration import _account_snapshot, _cycle, _inputs, _market, _tick
from trading_bot import database
from trading_bot.executor import PaperRecoveryRequired
from trading_bot.utils import MSK


def _migrated_legacy_db(tmp_path: Path) -> sqlite3.Connection:
    """Создаёт БД до 0006 с настоящей старой paper-строкой и применяет новые миграции."""
    old_dir = tmp_path / "old_migrations"
    old_dir.mkdir()
    for path in database.MIGRATIONS_DIR.glob("000[1-5]_*.sql"):
        (old_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    db_path = tmp_path / "legacy.db"
    old_conn = database.connect(db_path, migrations_dir=old_dir)
    old_conn.execute(
        "INSERT INTO decisions (ts, ticker, mode) VALUES (?, 'SBER', 'paper')",
        ("2026-03-02T09:00:00+00:00",),
    )
    old_conn.commit()
    old_conn.close()
    return database.connect(db_path)


def test_old_bar_on_legacy_database_is_skipped_without_reexecution(tmp_path: Path) -> None:
    conn = _migrated_legacy_db(tmp_path)
    cycle = _cycle(conn)
    skipped = cycle.run_tick(_tick())
    assert skipped.already_processed and skipped.decision_id is None
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)

    cutoff = datetime.fromisoformat(
        conn.execute("SELECT cutoff_at FROM paper_recovery_meta").fetchone()[0]
    )
    new_now = cutoff.replace(second=0, microsecond=0) + timedelta(minutes=15)
    new_now += timedelta(minutes=(-new_now.minute % 5))
    inputs = _inputs(session_open=False)
    shift = new_now - inputs.now
    fresh = replace(
        inputs,
        now=new_now,
        candles_5m=[replace(bar, ts_open=bar.ts_open + shift) for bar in inputs.candles_5m],
        candles_15m=[replace(bar, ts_open=bar.ts_open + shift) for bar in inputs.candles_15m],
        candles_1h=[replace(bar, ts_open=bar.ts_open + shift) for bar in inputs.candles_1h],
    )
    processed = cycle.run_tick(
        _tick(
            inputs=fresh,
            account=replace(_account_snapshot(), daily_anchor_day=fresh.now.astimezone(MSK).date()),
        )
    )
    assert not processed.already_processed
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (2,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    conn.close()


def test_corrupt_legacy_cutoff_stops_before_trading(tmp_path: Path) -> None:
    conn = _migrated_legacy_db(tmp_path)
    conn.execute("UPDATE paper_recovery_meta SET cutoff_at = 'not-a-timestamp'")
    conn.commit()
    with pytest.raises(PaperRecoveryRequired, match="cutoff"):
        _cycle(conn)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    conn.close()


def test_closed_bar_is_claimed_once_even_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "recovery.db"
    conn = database.connect(db_path)
    first = _cycle(conn).run_tick(_tick())
    assert first.trade_id is not None
    bar_ts = _tick().strategy.candles_5m[-1].ts_open.astimezone(UTC).isoformat()
    assert conn.execute(
        "SELECT source_bar_ts, processing_state FROM decisions WHERE id = ?", (first.decision_id,)
    ).fetchone() == (bar_ts, "complete")
    conn.close()

    restarted = database.connect(db_path)
    duplicate = _cycle(restarted).run_tick(_tick())
    assert duplicate.already_processed
    assert duplicate.decision_id == first.decision_id
    assert restarted.execute("SELECT COUNT(*) FROM trades").fetchone() == (1,)
    assert restarted.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    restarted.close()


@pytest.mark.parametrize("state", ["pending", "failed"])
def test_incomplete_claim_blocks_start_without_fill(tmp_path: Path, state: str) -> None:
    db_path = tmp_path / "blocked.db"
    conn = database.connect(db_path)
    first = _cycle(conn).run_tick(_tick())
    conn.execute(
        "UPDATE decisions SET processing_state = ? WHERE id = ?", (state, first.decision_id)
    )
    conn.commit()
    conn.close()

    restarted = database.connect(db_path)
    with pytest.raises(PaperRecoveryRequired):
        _cycle(restarted, starting_balance=Decimal("100000"))
    assert restarted.execute("SELECT COUNT(*) FROM trades").fetchone() == (1,)
    restarted.close()


@pytest.mark.parametrize("error_class", [ValueError, ArithmeticError, RuntimeError, AssertionError])
def test_evaluate_failure_is_persisted_before_stopping_without_raw_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_class: type[Exception],
) -> None:
    from trading_bot import executor

    conn = database.connect(tmp_path / "evaluate_failure.db")
    cycle = _cycle(conn)

    def fail_evaluate(**_kwargs: object) -> object:
        raise error_class("sensitive calculation detail")

    monkeypatch.setattr(executor, "evaluate", fail_evaluate)
    with pytest.raises(error_class, match="sensitive calculation detail"):
        cycle.run_tick(_tick())

    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    assert conn.execute(
        "SELECT stage, error_type, action, last_source_bar_ts FROM paper_source_episodes"
    ).fetchone() == (
        "strategy_evaluate",
        error_class.__name__,
        "stop",
        _tick().strategy.candles_5m[-1].ts_open.isoformat(),
    )
    assert "sensitive calculation detail" not in caplog.text
    conn.close()


def test_evaluate_journal_failure_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_bot import executor

    conn = database.connect(tmp_path / "evaluate_journal_failure.db")
    cycle = _cycle(conn)
    conn.execute(
        "CREATE TRIGGER reject_episode BEFORE INSERT ON paper_source_episodes "
        "BEGIN SELECT RAISE(ABORT, 'episode unavailable'); END"
    )
    conn.commit()

    def fail_evaluate(**_kwargs: object) -> object:
        raise ValueError("injected calculation failure")

    monkeypatch.setattr(executor, "evaluate", fail_evaluate)
    with pytest.raises(sqlite3.IntegrityError, match="episode unavailable"):
        cycle.run_tick(_tick())
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    conn.close()


def test_evaluate_recovers_same_bar_only_after_source_is_fixed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_bot import executor

    conn = database.connect(tmp_path / "evaluate_recovery.db")
    cycle = _cycle(conn)

    def fail_evaluate(**_kwargs: object) -> object:
        raise ValueError("injected calculation failure")

    with monkeypatch.context() as patch:
        patch.setattr(executor, "evaluate", fail_evaluate)
        with pytest.raises(ValueError, match="calculation failure"):
            cycle.run_tick(_tick())
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (0,)
    result = cycle.run_tick(_tick())
    assert not result.already_processed and result.trade_id is not None
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM paper_source_episodes").fetchone() == (1,)
    conn.close()


def test_invalid_5m_timestamp_is_persisted_as_source_refusal(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "invalid_bar.db")
    cycle = _cycle(conn)
    inputs = _inputs()
    bad = replace(inputs.candles_5m[-1], ts_open=inputs.candles_5m[-1].ts_open.replace(tzinfo=None))
    with pytest.raises(ValueError, match="tz-aware"):
        cycle.run_tick(_tick(inputs=replace(inputs, candles_5m=[*inputs.candles_5m[:-1], bad])))
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (0,)
    assert conn.execute(
        "SELECT stage, action, last_source_bar_ts FROM paper_source_episodes"
    ).fetchone() == ("candles_5m", "stop", None)
    conn.close()


def test_two_connections_created_before_fill_cannot_repeat_same_bar(tmp_path: Path) -> None:
    db_path = tmp_path / "parallel.db"
    first_conn = database.connect(db_path)
    second_conn = database.connect(db_path)
    first_cycle = _cycle(first_conn)
    second_cycle = _cycle(second_conn)
    first = first_cycle.run_tick(_tick())
    second = second_cycle.run_tick(_tick())
    assert first.trade_id is not None
    assert second.already_processed and second.decision_id == first.decision_id
    assert first_conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    assert second_conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (1,)
    first_conn.close()
    second_conn.close()


def test_second_process_rejects_stale_balance_on_different_ticker(tmp_path: Path) -> None:
    db_path = tmp_path / "different_tickers.db"
    first_conn = database.connect(db_path)
    second_conn = database.connect(db_path)
    first_cycle = _cycle(first_conn)
    second_cycle = _cycle(second_conn)
    assert first_cycle.run_tick(_tick()).trade_id is not None
    other = replace(_inputs(), ticker="GAZP")
    with pytest.raises(ValueError, match="баланс снимка"):
        second_cycle.run_tick(_tick(inputs=other))
    assert first_conn.execute("SELECT ticker FROM trades").fetchall() == [("SBER",)]
    assert second_conn.execute("SELECT ticker, stage FROM paper_source_episodes").fetchall() == [
        ("GAZP", "tick_validation")
    ]
    first_conn.close()
    second_conn.close()


def test_two_connections_cannot_fill_different_tickers_while_one_claim_is_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "inflight.db"
    first_conn = database.connect(db_path)
    second_conn = database.connect(db_path)
    first_cycle = _cycle(first_conn)
    second_cycle = _cycle(second_conn)
    entered_fill, release_fill = Event(), Event()
    original_fill = first_cycle._broker.fill_market_order

    def paused_fill(**kwargs: object) -> object:
        entered_fill.set()
        assert release_fill.wait(timeout=5)
        return original_fill(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(first_cycle._broker, "fill_market_order", paused_fill)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(first_cycle.run_tick, _tick())
        try:
            assert entered_fill.wait(timeout=5)
            with pytest.raises(PaperRecoveryRequired):
                second_cycle.run_tick(_tick(inputs=replace(_inputs(), ticker="GAZP")))
        finally:
            release_fill.set()
        assert future.result(timeout=5).trade_id is not None
    assert first_conn.execute("SELECT ticker FROM trades").fetchall() == [("SBER",)]
    assert second_conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    first_conn.close()
    second_conn.close()


def test_pending_other_bar_blocks_existing_cycle_globally(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "global.db")
    cycle = _cycle(conn)
    first = cycle.run_tick(_tick(inputs=_inputs(session_open=False)))
    conn.execute(
        "UPDATE decisions SET processing_state = 'pending' WHERE id = ?", (first.decision_id,)
    )
    conn.commit()
    inputs = _inputs()
    newer = replace(
        inputs,
        now=inputs.now + timedelta(minutes=5),
        candles_5m=[
            *inputs.candles_5m,
            replace(
                inputs.candles_5m[-1],
                ts_open=inputs.candles_5m[-1].ts_open + timedelta(minutes=5),
            ),
        ],
    )
    with pytest.raises(PaperRecoveryRequired):
        cycle.run_tick(_tick(inputs=newer))
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    conn.close()


def test_completed_duplicate_does_not_hide_unrelated_pending_claim(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "global_duplicate.db")
    cycle = _cycle(conn)
    complete = cycle.run_tick(_tick(inputs=_inputs(session_open=False)))
    inputs = _inputs()
    newer = replace(
        inputs,
        now=inputs.now + timedelta(minutes=5),
        candles_5m=[
            *inputs.candles_5m,
            replace(
                inputs.candles_5m[-1],
                ts_open=inputs.candles_5m[-1].ts_open + timedelta(minutes=5),
            ),
        ],
    )
    pending = cycle.run_tick(_tick(inputs=replace(newer, is_session_open=False)))
    conn.execute(
        "UPDATE decisions SET processing_state = 'pending' WHERE id = ?", (pending.decision_id,)
    )
    conn.commit()
    with pytest.raises(PaperRecoveryRequired):
        cycle.run_tick(_tick(inputs=_inputs(session_open=False)))
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (2,)
    assert complete.decision_id != pending.decision_id
    conn.close()


def test_failure_after_portfolio_commit_never_repeats_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = database.connect(tmp_path / "after_fill.db")
    cycle = _cycle(conn)
    original = cycle._mark_state

    def fail_complete(decision_id: int, state: str) -> None:
        if state == "complete":
            raise sqlite3.OperationalError("injected completion failure")
        original(decision_id, state)

    monkeypatch.setattr(cycle, "_mark_state", fail_complete)
    with pytest.raises(sqlite3.OperationalError, match="completion failure"):
        cycle.run_tick(_tick())
    assert conn.execute("SELECT processing_state FROM decisions").fetchone() == ("failed",)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (1,)
    with pytest.raises(PaperRecoveryRequired):
        cycle.run_tick(_tick())
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (1,)
    conn.close()


@pytest.mark.parametrize("market_override", [None, "HALTED"])
def test_no_signal_and_risk_refusal_bars_are_deduplicated(
    tmp_path: Path, market_override: str | None
) -> None:
    conn = database.connect(tmp_path / "refusal.db")
    tick = (
        _tick(inputs=_inputs(session_open=False))
        if market_override is None
        else _tick(market=_market(trading_status=market_override))
    )
    cycle = _cycle(conn)
    first = cycle.run_tick(tick)
    second = cycle.run_tick(tick)
    assert not first.already_processed and second.already_processed
    assert first.decision_id == second.decision_id
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    conn.close()
