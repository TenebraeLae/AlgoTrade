"""Offline manual paper reconciliation: real SQLite and real PaperCycle restarts."""

from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_paper_cycle_integration import _cycle, _inputs, _market, _tick
from trading_bot import database, paper_recovery
from trading_bot.executor import PaperRecoveryRequired
from trading_bot.portfolio import Portfolio


def _claim(path: Path, *, filled: bool = False, state: str = "failed") -> int:
    conn = database.connect(path)
    result = _cycle(conn).run_tick(_tick() if filled else _tick(inputs=_inputs(session_open=False)))
    assert result.decision_id is not None
    conn.execute("UPDATE decisions SET processing_state=? WHERE id=?", (state, result.decision_id))
    conn.commit()
    conn.close()
    return result.decision_id


def _request(path: Path, decision_id: int, *, filled: bool = False) -> paper_recovery.Confirmation:
    return paper_recovery.Confirmation(
        decision_id=decision_id,
        operator="operator_17",
        expected_fingerprint=paper_recovery.inspect_database(path, decision_id).fingerprint,
        outcome="accepted_existing_fill" if filled else "no_fill",
        checks=paper_recovery.ManualChecks(
            runtime_stopped=True,
            trades_reviewed=True,
            positions_reviewed=True,
            balance_reviewed=True,
        ),
    )


def test_inspect_is_read_only_complete_and_does_not_create_database(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(paper_recovery.RecoveryError):
        paper_recovery.inspect_database(missing, 1)
    assert not missing.exists()
    path = tmp_path / "paper.db"
    decision_id = _claim(path, filled=True)
    before = path.read_bytes()
    snapshot = paper_recovery.inspect_database(path, decision_id)
    data = json.loads(snapshot.snapshot_json)
    assert data["decision"]["processing_state"] == "failed"
    assert len(data["linked_trades"]) == len(data["paper_positions"]) == 1
    assert len(data["paper_balances"]) == 1
    assert "equity_anchors" in data and "daily_stop_latches" in data
    assert len(snapshot.fingerprint) == 64
    assert snapshot == paper_recovery.inspect_database(path, decision_id)
    assert path.read_bytes() == before


@pytest.mark.parametrize("filled", [False, True])
@pytest.mark.parametrize("state", ["pending", "failed"])
def test_confirmation_persists_audit_and_restart_never_duplicates_fill(
    tmp_path: Path, filled: bool, state: str
) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path, filled=filled, state=state)
    request = _request(path, decision_id, filled=filled)
    conn = database.connect(path)
    with pytest.raises(PaperRecoveryRequired):
        _cycle(conn)
    conn.close()
    paper_recovery.confirm_database(path, request)
    conn = database.connect(path)
    audit = conn.execute(
        "SELECT previous_state, ts, operator, fingerprint, confirmations, outcome, snapshot "
        "FROM paper_manual_reconciliations WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    assert audit is not None and audit[0] == state and audit[2] == "operator_17"
    assert datetime.fromisoformat(audit[1]).utcoffset().total_seconds() == 0
    assert audit[3] == request.expected_fingerprint
    assert all(json.loads(audit[4]).values())
    assert audit[5] == request.outcome
    assert json.loads(audit[6])["decision"]["processing_state"] == state
    result = _cycle(conn).run_tick(_tick())
    assert result.already_processed and result.decision_id == decision_id
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (int(filled),)
    conn.close()
    with pytest.raises(paper_recovery.RecoveryError):
        paper_recovery.confirm_database(path, request)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE portfolio_balances SET balance='90000' WHERE mode='paper'",
        "UPDATE positions SET stop='1' WHERE mode='paper'",
        "UPDATE trades SET fee='42' WHERE mode='paper'",
        "INSERT INTO paper_equity_anchors VALUES "
        "('starting','global','100000','2026-03-02T09:00:00+00:00')",
        "INSERT INTO paper_daily_stop_latches VALUES "
        "('2026-03-02','2026-03-02T09:00:00+00:00','96000','97000')",
        "INSERT INTO decisions (ts,ticker,mode,source_bar_ts,processing_state) VALUES "
        "('2026-03-02T09:00:00+00:00','GAZP','paper','2026-03-02T08:55:00+00:00','pending')",
    ],
)
def test_another_connection_invalidates_fingerprint(tmp_path: Path, statement: str) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path, filled=True)
    request = _request(path, decision_id, filled=True)
    writer = sqlite3.connect(path)
    writer.execute("BEGIN IMMEDIATE")
    writer.execute(statement)
    # Confirmation must acquire the lock before reading its current snapshot.
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(paper_recovery.confirm_database, path, request)
        writer.commit()
        with pytest.raises(paper_recovery.RecoveryError, match="fingerprint"):
            future.result(timeout=10)
    assert writer.execute(
        "SELECT processing_state FROM decisions WHERE id=?", (decision_id,)
    ).fetchone() == ("failed",)
    assert writer.execute("SELECT COUNT(*) FROM paper_manual_reconciliations").fetchone() == (0,)
    writer.close()


def test_old_unrelated_closed_trade_change_invalidates_fingerprint(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    writer = sqlite3.connect(path)
    for ticker in ("OLD", "LATEST"):
        writer.execute(
            "INSERT INTO trades (ticker,side,lots,lot_size,price,fee,slippage,mode,ts_open,"
            "ts_close,exit_reason,status,balance_after) VALUES "
            "(?,'long',1,1,'100','1','0','paper','2026-03-01T08:00:00+00:00',"
            "'2026-03-01T09:00:00+00:00','manual','closed','100000')",
            (ticker,),
        )
    writer.commit()
    request = _request(path, decision_id)
    writer.execute("UPDATE trades SET fee='2' WHERE ticker='OLD'")
    writer.commit()
    with pytest.raises(paper_recovery.RecoveryError, match="fingerprint"):
        paper_recovery.confirm_database(path, request)
    assert writer.execute("SELECT processing_state FROM decisions").fetchone() == ("failed",)
    assert writer.execute("SELECT COUNT(*) FROM paper_manual_reconciliations").fetchone() == (0,)
    writer.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE decisions SET mode='live'",
        "UPDATE decisions SET processing_state='complete'",
        "UPDATE decisions SET source_bar_ts=NULL",
        "UPDATE decisions SET source_bar_ts='2026-03-02T08:55:00'",
        "UPDATE decisions SET source_bar_ts='2026-03-02T08:56:00+00:00'",
        "UPDATE decisions SET source_bar_ts='2026-03-02T11:55:00+03:00'",
        "DELETE FROM portfolio_balances WHERE mode='paper'",
    ],
)
def test_wrong_mode_state_or_bar_cannot_be_confirmed(tmp_path: Path, statement: str) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    request = _request(path, decision_id)
    conn = database.connect(path)
    conn.execute(statement)
    conn.commit()
    if "mode=" not in statement:
        request = replace(
            request,
            expected_fingerprint=paper_recovery.inspect_database(path, decision_id).fingerprint,
        )
    with pytest.raises(paper_recovery.RecoveryError):
        paper_recovery.confirm_database(path, request)
    assert conn.execute("SELECT COUNT(*) FROM paper_manual_reconciliations").fetchone() == (0,)
    conn.close()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE trades SET mode='live'",
        "UPDATE trades SET ticker='OTHER'",
        "UPDATE trades SET side='short'",
        "UPDATE trades SET lots=1000000",
        "UPDATE trades SET lot_size=NULL",
        "UPDATE trades SET price='NaN'",
        "UPDATE trades SET fee='-1'",
        "UPDATE positions SET lots=99999",
        "DELETE FROM positions",
        "UPDATE portfolio_balances SET balance='1' WHERE mode='paper'",
    ],
)
def test_existing_fill_requires_basic_consistency(tmp_path: Path, statement: str) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path, filled=True)
    conn = database.connect(path)
    conn.execute(statement)
    conn.commit()
    with pytest.raises(paper_recovery.RecoveryError):
        paper_recovery.confirm_database(path, _request(path, decision_id, filled=True))
    assert conn.execute("SELECT processing_state FROM decisions").fetchone() == ("failed",)
    assert conn.execute("SELECT COUNT(*) FROM paper_manual_reconciliations").fetchone() == (0,)
    conn.close()


@pytest.mark.parametrize("filled", [False, True])
def test_wrong_outcome_is_rejected(tmp_path: Path, filled: bool) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path, filled=filled)
    request = _request(path, decision_id, filled=not filled)
    with pytest.raises(paper_recovery.RecoveryError):
        paper_recovery.confirm_database(path, request)


@pytest.mark.parametrize(
    "failure_table,operation", [("paper_manual_reconciliations", "INSERT"), ("decisions", "UPDATE")]
)
def test_sql_failure_rolls_back_both_audit_and_completion(
    tmp_path: Path, failure_table: str, operation: str
) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    request = _request(path, decision_id)
    conn = database.connect(path)
    conn.execute(
        f"CREATE TRIGGER fail_confirmation BEFORE {operation} ON {failure_table} "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        paper_recovery.confirm_database(path, request)
    assert conn.execute("SELECT processing_state FROM decisions").fetchone() == ("failed",)
    assert conn.execute("SELECT COUNT(*) FROM paper_manual_reconciliations").fetchone() == (0,)
    conn.close()


@pytest.mark.parametrize(
    "field", ["runtime_stopped", "trades_reviewed", "positions_reviewed", "balance_reviewed"]
)
def test_each_manual_attestation_is_mandatory(tmp_path: Path, field: str) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    request = _request(path, decision_id)
    checks = replace(request.checks, **{field: False})
    with pytest.raises(paper_recovery.RecoveryError):
        paper_recovery.confirm_database(path, replace(request, checks=checks))


@pytest.mark.parametrize(
    "operator",
    ["", "name with spaces", "operator@example.org", "t.secret", "a" * 33, "admin\npassword"],
)
def test_unsafe_operator_is_not_persisted(tmp_path: Path, operator: str) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    with pytest.raises(paper_recovery.RecoveryError):
        paper_recovery.confirm_database(
            path, replace(_request(path, decision_id), operator=operator)
        )


def test_other_pending_claim_still_blocks_executor(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    conn = database.connect(path)
    conn.execute(
        "INSERT INTO decisions (ts,ticker,mode,source_bar_ts,processing_state) VALUES "
        "('2026-03-02T09:00:00+00:00','GAZP','paper','2026-03-02T08:55:00+00:00','pending')"
    )
    conn.commit()
    paper_recovery.confirm_database(path, _request(path, decision_id))
    with pytest.raises(PaperRecoveryRequired):
        _cycle(conn)
    assert conn.execute("SELECT processing_state FROM decisions ORDER BY id").fetchall() == [
        ("complete",),
        ("pending",),
    ]
    conn.close()


def test_confirmation_does_not_modify_risk_accounting_or_other_decision_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path, filled=True)
    conn = database.connect(path)
    conn.execute(
        "INSERT INTO paper_equity_anchors VALUES "
        "('starting','global','100000','2026-03-02T09:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO paper_daily_stop_latches VALUES "
        "('2026-03-02','2026-03-02T09:00:00+00:00','96000','97000')"
    )
    conn.commit()
    tables = (
        "positions",
        "trades",
        "portfolio_balances",
        "paper_equity_anchors",
        "paper_daily_stop_latches",
        "paper_recovery_meta",
        "paper_source_episodes",
        "schema_version",
    )
    before = {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    decision_before = json.loads(paper_recovery.inspect_database(path, decision_id).snapshot_json)[
        "decision"
    ]
    paper_recovery.confirm_database(path, _request(path, decision_id, filled=True))
    assert before == {table: conn.execute(f"SELECT * FROM {table}").fetchall() for table in tables}
    decision_after = json.loads(paper_recovery.inspect_database(path, decision_id).snapshot_json)[
        "decision"
    ]
    assert decision_after == {**decision_before, "processing_state": "complete"}
    for operation in (
        "DELETE FROM paper_manual_reconciliations",
        "UPDATE paper_manual_reconciliations SET operator='other'",
    ):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(operation)
        conn.rollback()
    conn.close()


@pytest.mark.parametrize("side", ["long", "short"])
def test_real_failure_after_partial_fill_can_be_accepted_without_another_fill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, side: str
) -> None:
    path = tmp_path / "paper.db"
    conn = database.connect(path)
    cycle = _cycle(conn)
    mark_state = cycle._mark_state

    def fail_completion(decision_id: int, state: str) -> None:
        if state == "complete":
            raise sqlite3.OperationalError("injected completion failure")
        mark_state(decision_id, state)

    monkeypatch.setattr(cycle, "_mark_state", fail_completion)
    tick = _tick(inputs=_inputs(ranging=True, side=side), market=_market(available_lots=1))
    with pytest.raises(sqlite3.OperationalError, match="completion failure"):
        cycle.run_tick(tick)
    decision_id = conn.execute("SELECT id FROM decisions").fetchone()[0]
    assert conn.execute("SELECT lots FROM trades").fetchone() == (1,)
    conn.close()
    paper_recovery.confirm_database(path, _request(path, decision_id, filled=True))
    conn = database.connect(path)
    assert _cycle(conn).run_tick(tick).already_processed
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (1,)
    conn.close()


@pytest.mark.parametrize(
    "extra",
    [[], ["--notes", "sensitive-value"], ["--decision-id", "oops"], ["--decision-id", "-1"]],
)
def test_cli_invalid_arguments_do_not_mutate_or_echo_raw_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    args = ["confirm", "--db", str(path), "--decision-id", str(decision_id), *extra]
    with pytest.raises(SystemExit) as exc:
        paper_recovery.main(args)
    assert exc.value.code == 2
    assert "sensitive-value" not in capsys.readouterr().err
    conn = database.connect(path)
    assert conn.execute("SELECT processing_state FROM decisions").fetchone() == ("failed",)
    conn.close()


def test_cli_inspect_and_confirm_roundtrip(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    base = ["--db", str(path), "--decision-id", str(decision_id)]
    assert paper_recovery.main(["inspect", *base]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["snapshot"]["decision"]["id"] == decision_id
    assert (
        paper_recovery.main(
            [
                "confirm",
                *base,
                "--operator",
                "operator_17",
                "--expected-fingerprint",
                inspected["fingerprint"],
                "--outcome",
                "no_fill",
                "--runtime-stopped",
                "--trades-reviewed",
                "--positions-reviewed",
                "--balance-reviewed",
            ]
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["processing_state"] == "complete"
    assert (
        paper_recovery.main(
            [
                "confirm",
                *base,
                "--operator",
                "operator_17",
                "--expected-fingerprint",
                inspected["fingerprint"],
                "--outcome",
                "no_fill",
                "--runtime-stopped",
                "--trades-reviewed",
                "--positions-reviewed",
                "--balance-reviewed",
            ]
        )
        == 1
    )
    assert "action=stop" in capsys.readouterr().err


def test_closed_existing_fill_is_manual_acceptance_not_a_fifo_repair(tmp_path: Path) -> None:
    from decimal import Decimal

    path = tmp_path / "paper.db"
    decision_id = _claim(path, filled=True)
    conn = database.connect(path)
    lots = conn.execute("SELECT lots FROM trades").fetchone()[0]
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("1"))
    portfolio.close_position(
        ticker="SBER",
        lots=lots,
        exit_price=Decimal("100"),
        fee=Decimal("1"),
        slippage=Decimal("1"),
        exit_reason="manual",
    )
    conn.close()
    paper_recovery.confirm_database(path, _request(path, decision_id, filled=True))
    conn = database.connect(path)
    assert _cycle(conn).run_tick(_tick()).already_processed
    assert conn.execute("SELECT status FROM trades").fetchall() == [("closed",)]
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone() == (0,)
    conn.close()


def test_invalid_fingerprint_and_unknown_outcome_are_not_accepted(tmp_path: Path) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    request = _request(path, decision_id)
    for fingerprint in ("", "X" * 64, "0" * 63):
        with pytest.raises(paper_recovery.RecoveryError, match="fingerprint"):
            paper_recovery.confirm_database(
                path, replace(request, expected_fingerprint=fingerprint)
            )
    with pytest.raises(paper_recovery.RecoveryError, match="outcome"):
        paper_recovery.confirm_database(path, replace(request, outcome="retry_fill"))  # type: ignore[arg-type]


def test_new_migration_and_missing_schema_never_silently_repair_accounting(tmp_path: Path) -> None:
    old_migrations = tmp_path / "old_migrations"
    old_migrations.mkdir()
    for migration in database.MIGRATIONS_DIR.glob("*.sql"):
        if int(migration.name[:4]) < 10:
            (old_migrations / migration.name).write_text(
                migration.read_text(encoding="utf-8"), encoding="utf-8"
            )
    path = tmp_path / "old.db"
    conn = database.connect(path, migrations_dir=old_migrations)
    result = _cycle(conn).run_tick(_tick(inputs=_inputs(session_open=False)))
    assert result.decision_id is not None
    conn.execute("UPDATE decisions SET processing_state='failed'")
    conn.commit()
    before = conn.execute("SELECT * FROM decisions").fetchall()
    request = _request(path, result.decision_id)
    with pytest.raises(sqlite3.OperationalError):
        paper_recovery.confirm_database(path, request)
    assert conn.execute("SELECT * FROM decisions").fetchall() == before
    assert conn.execute("SELECT COUNT(*) FROM schema_version WHERE version=10").fetchone() == (0,)
    database.apply_migrations(conn)
    assert conn.execute("SELECT * FROM decisions").fetchall() == before
    conn.close()
    paper_recovery.confirm_database(path, _request(path, result.decision_id))


def test_complete_cli_rejects_notes_without_echo_and_never_creates_db(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "paper.db"
    decision_id = _claim(path)
    request = _request(path, decision_id)
    args = [
        "confirm",
        "--db",
        str(path),
        "--decision-id",
        str(decision_id),
        "--operator",
        request.operator,
        "--expected-fingerprint",
        request.expected_fingerprint,
        "--outcome",
        "no_fill",
        "--runtime-stopped",
        "--trades-reviewed",
        "--positions-reviewed",
        "--balance-reviewed",
        "--notes",
        "sensitive-value",
    ]
    with pytest.raises(SystemExit) as exc:
        paper_recovery.main(args)
    assert exc.value.code == 2
    assert "sensitive-value" not in capsys.readouterr().err
    missing = tmp_path / "missing.db"
    with pytest.raises(paper_recovery.RecoveryError):
        paper_recovery.confirm_database(missing, request)
    assert paper_recovery.main(["inspect", "--db", str(missing), "--decision-id", "1"]) == 1
    assert not missing.exists()
