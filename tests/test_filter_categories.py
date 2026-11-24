"""Числовые категории M7: SQLite, ранние отказы и непроверенные категории."""

import json
import sqlite3
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_paper_cycle_integration import _cycle, _tick
from tests.test_strategy import _config, _long_snapshot
from tests.test_strategy_filters_integration import _evaluate_real
from trading_bot import database, strategy
from trading_bot.executor import PaperRecoveryRequired
from trading_bot.journal import DecisionJournal
from trading_bot.risk_manager import CHECK_NAMES, CheckResult, RiskVerdict


def _categories(conn: sqlite3.Connection, decision_id: int) -> list[dict[str, object]]:
    row = conn.execute(
        "SELECT filter_categories FROM decisions WHERE id=?", (decision_id,)
    ).fetchone()
    payload = json.loads(row[0])
    assert payload["version"] == 1
    categories: list[dict[str, object]] = payload["categories"]
    assert [item["id"] for item in categories] == list(range(1, 11))
    return categories


def test_all_categories_pass_only_with_full_evidence(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "full.db")
    decision = _evaluate_real(gap_result=(False, Decimal(0)))
    risk = RiskVerdict(True, "ok", 1, tuple(CheckResult(name, True, "ok") for name in CHECK_NAMES))
    row_id = DecisionJournal(conn, mode="paper").record(decision, risk_verdict=risk)
    assert [item["status"] for item in _categories(conn, row_id)] == ["passed"] * 10
    conn.close()


@pytest.mark.parametrize(
    "name, category",
    [
        ("session_open", 1),
        ("instrument_status_normal", 3),
        ("volatility_within_limit", 6),
        ("spread_within_limit", 7),
        ("correlation_within_limit", 8),
        ("open_positions_within_limit", 9),
        ("sector_and_ticker_limits", 9),
        ("daily_loss_limit_not_reached", 10),
        ("trades_per_day_within_limit", 10),
        ("free_balance_sufficient", 10),
    ],
)
def test_risk_failures_use_strategy_numbers(tmp_path: Path, name: str, category: int) -> None:
    conn = database.connect(tmp_path / "risk.db")
    risk = RiskVerdict(
        False,
        "refused",
        0,
        tuple(CheckResult(check, check != name, "test") for check in CHECK_NAMES),
    )
    row_id = DecisionJournal(conn, mode="paper").record(
        _evaluate_real(gap_result=(False, Decimal(0))), risk_verdict=risk
    )
    categories = _categories(conn, row_id)
    assert [item["id"] for item in categories if item["status"] == "failed"] == [category]
    assert categories[category - 1]["checks_failed"] == [f"risk:{name}"]
    conn.close()


def test_partial_diversification_is_not_pass_and_any_failure_wins(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "partial.db")
    journal = DecisionJournal(conn, mode="paper")
    decision = _evaluate_real()
    for passed in (True, False):
        risk = RiskVerdict(
            passed,
            "partial unit verdict",
            1 if passed else 0,
            (CheckResult("open_positions_within_limit", passed, "test"),),
        )
        row_id = journal.record(decision, risk_verdict=risk)
        category = _categories(conn, row_id)[8]
        assert category["status"] == ("not_evaluated" if passed else "failed")
    conn.close()


@pytest.mark.parametrize(
    "failure, expected",
    [
        ("session_open", 1),
        ("session_guard", 2),
        ("regime_volatile", 4),
        ("regime_chaos", 4),
        ("regime_transitional", 4),
        ("local_regime_unavailable", 4),
        ("gap_pause", 5),
    ],
)
def test_early_refusals_keep_unexecuted_filters_unknown(
    tmp_path: Path, failure: str, expected: int
) -> None:
    conn = database.connect(tmp_path / "early.db")
    decision = replace(
        _evaluate_real(),
        verdict="no_signal",
        signal=None,
        filters_passed=(),
        filters_failed=(failure,),
    )
    row_id = DecisionJournal(conn, mode="paper").record(decision)
    categories = _categories(conn, row_id)
    assert [item["id"] for item in categories if item["status"] == "failed"] == [expected]
    assert all(item["status"] == "not_evaluated" for item in categories if item["id"] != expected)
    conn.close()


def test_missing_history_and_gap_are_not_invented_as_passed(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "unknown.db")
    decision = strategy.evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=_evaluate_real().ts,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    row_id = DecisionJournal(conn, mode="paper").record(decision)
    statuses = [item["status"] for item in _categories(conn, row_id)]
    assert statuses[:2] == ["passed", "passed"]
    assert statuses[2:] == ["not_evaluated"] * 8
    row_id = DecisionJournal(conn, mode="paper").record(_evaluate_real())
    assert _categories(conn, row_id)[4]["status"] == "not_evaluated"
    conn.close()


def test_status_refusal_is_not_time_filter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(strategy, "compute_snapshot", lambda **kwargs: _long_snapshot())
    conn = database.connect(tmp_path / "status.db")
    decision = strategy.evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=_evaluate_real().ts,
        is_session_open=False,
        session_reason="статус недоступен",
        short_enabled=True,
        session_guard_active=False,
        session_refusal_filter="instrument_status_normal",
    )
    row_id = DecisionJournal(conn, mode="paper").record(decision)
    categories = _categories(conn, row_id)
    assert [item["status"] for item in categories[:3]] == ["passed", "passed", "failed"]
    conn.close()


def test_paper_cycle_persists_all_categories_and_reuses_them_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "cycle.db"
    tick = _tick()
    tick = replace(tick, strategy=replace(tick.strategy, gap_result=(False, Decimal(0))))
    conn = database.connect(path)
    result = _cycle(conn).run_tick(tick)
    assert result.decision_id is not None and result.trade_id is not None
    expected = _categories(conn, result.decision_id)
    assert [item["status"] for item in expected] == ["passed"] * 10
    conn.close()
    conn = database.connect(path)
    repeated = _cycle(conn).run_tick(tick)
    assert repeated.already_processed and repeated.decision_id == result.decision_id
    assert _categories(conn, result.decision_id) == expected
    assert conn.execute("SELECT count(*) FROM trades").fetchone() == (1,)
    conn.close()


@pytest.mark.parametrize("state", ["pending", "failed"])
def test_upgrade_preserves_unresolved_claim_barrier(tmp_path: Path, state: str) -> None:
    old = tmp_path / "before_categories"
    old.mkdir()
    for path in database.MIGRATIONS_DIR.glob("000[1-8]_*.sql"):
        (old / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    conn = database.connect(tmp_path / "unresolved.db", migrations_dir=old)
    conn.execute(
        "INSERT INTO decisions (ts,ticker,mode,source_bar_ts,processing_state) "
        "VALUES ('2026-03-02T09:00:00+00:00','SBER','paper','2026-03-02T08:55:00+00:00',?)",
        (state,),
    )
    conn.commit()
    database.apply_migrations(conn)
    assert conn.execute("SELECT processing_state,filter_categories FROM decisions").fetchone() == (
        state,
        None,
    )
    assert conn.execute("SELECT count(*) FROM paper_manual_reconciliations").fetchone() == (0,)
    with pytest.raises(PaperRecoveryRequired):
        _cycle(conn)
    conn.close()


def test_upgrade_does_not_backfill_legacy_categories(tmp_path: Path) -> None:
    old = tmp_path / "old"
    old.mkdir()
    for path in database.MIGRATIONS_DIR.glob("000[1-8]_*.sql"):
        (old / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    conn = database.connect(tmp_path / "upgrade.db", migrations_dir=old)
    conn.execute(
        "INSERT INTO decisions (ts,ticker,mode) VALUES ('2026-03-02T09:00:00+00:00','SBER','paper')"
    )
    conn.commit()
    database.apply_migrations(conn)
    assert conn.execute("SELECT filter_categories FROM decisions").fetchone() == (None,)
    assert conn.execute("SELECT processing_state FROM decisions").fetchone() == ("complete",)
    database.apply_migrations(conn)
    assert conn.execute("SELECT count(*) FROM decisions").fetchone() == (1,)
    conn.close()
