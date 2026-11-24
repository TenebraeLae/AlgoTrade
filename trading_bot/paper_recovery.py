"""Offline, operator-attested completion of a single paper claim.

Allowed: read local SQLite and append audit plus pending/failed -> complete.
Forbidden: networking, fills, accounting repair, risk/stop/anchor changes.
The operator must stop every runtime before inspection and confirmation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, NoReturn, TypeAlias, TypedDict

Scalar: TypeAlias = str | int | None
Row: TypeAlias = dict[str, Scalar]
Outcome: TypeAlias = Literal["no_fill", "accepted_existing_fill"]
_OPERATOR = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,31}")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")


class RecoveryError(ValueError):
    """Unsafe or stale reconciliation; stop and inspect again, never auto-retry."""


@dataclass(frozen=True)
class ManualChecks:
    """Explicit human attestations, not machine verification of runtime or FIFO."""

    runtime_stopped: bool
    trades_reviewed: bool
    positions_reviewed: bool
    balance_reviewed: bool


@dataclass(frozen=True)
class Confirmation:
    """Bounded confirmation input; no free-form notes, secrets or repair commands."""

    decision_id: int
    operator: str
    expected_fingerprint: str
    outcome: Outcome
    checks: ManualChecks


@dataclass(frozen=True)
class RecoverySnapshot:
    """Canonical pre-change JSON and its SHA-256, with Decimal values kept as text."""

    snapshot_json: str
    fingerprint: str


class _SnapshotData(TypedDict):
    decision: Row
    linked_trades: list[Row]
    paper_positions: list[Row]
    paper_open_trades: list[Row]
    paper_trade_journal: list[Row]
    latest_paper_trade: list[Row]
    paper_trade_summary: list[Row]
    paper_balances: list[Row]
    equity_anchors: list[Row]
    daily_stop_latches: list[Row]
    recovery_meta: list[Row]
    unresolved_claims: list[Row]
    schema_versions: list[Row]


def _open(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if not path.is_file():
        raise RecoveryError("database must already exist; no database was created")
    mode = "ro" if readonly else "rw"
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode={mode}", uri=True)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params: tuple[Scalar, ...] = ()) -> list[Row]:
    cursor = conn.execute(sql, params)
    assert cursor.description is not None
    names = [column[0] for column in cursor.description]
    result: list[Row] = []
    for values in cursor:
        row: Row = {}
        for name, value in zip(names, values, strict=True):
            if value is not None and not isinstance(value, (str, int)):
                raise RecoveryError(
                    "unsupported database value; manual accounting investigation required"
                )
            row[name] = value
        result.append(row)
    return result


def _read_snapshot(conn: sqlite3.Connection, decision_id: int) -> _SnapshotData:
    if type(decision_id) is not int or decision_id <= 0:
        raise RecoveryError("decision_id must be a positive integer")
    decisions = _rows(conn, "SELECT * FROM decisions WHERE id=?", (decision_id,))
    if not decisions or decisions[0]["mode"] != "paper":
        raise RecoveryError("decision must exist in paper mode")
    return _SnapshotData(
        decision=decisions[0],
        linked_trades=_rows(
            conn, "SELECT * FROM trades WHERE journal_id=? ORDER BY id", (decision_id,)
        ),
        paper_positions=_rows(conn, "SELECT * FROM positions WHERE mode='paper' ORDER BY ticker"),
        paper_open_trades=_rows(
            conn, "SELECT * FROM trades WHERE mode='paper' AND status='open' ORDER BY id"
        ),
        paper_trade_journal=_rows(conn, "SELECT * FROM trades WHERE mode='paper' ORDER BY id"),
        latest_paper_trade=_rows(
            conn, "SELECT * FROM trades WHERE mode='paper' ORDER BY id DESC LIMIT 1"
        ),
        paper_trade_summary=_rows(
            conn,
            "SELECT COUNT(*) AS total, COUNT(CASE WHEN status='closed' THEN 1 END) AS closed "
            "FROM trades WHERE mode='paper'",
        ),
        paper_balances=_rows(conn, "SELECT * FROM portfolio_balances WHERE mode='paper'"),
        equity_anchors=_rows(
            conn, "SELECT * FROM paper_equity_anchors ORDER BY anchor_type, anchor_key"
        ),
        daily_stop_latches=_rows(
            conn, "SELECT * FROM paper_daily_stop_latches ORDER BY trading_day"
        ),
        recovery_meta=_rows(conn, "SELECT * FROM paper_recovery_meta ORDER BY singleton"),
        unresolved_claims=_rows(
            conn,
            "SELECT * FROM decisions WHERE mode='paper' "
            "AND processing_state IN ('pending','failed') ORDER BY id",
        ),
        schema_versions=_rows(conn, "SELECT * FROM schema_version ORDER BY version"),
    )


def _snapshot(data: _SnapshotData) -> RecoverySnapshot:
    encoded = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    return RecoverySnapshot(encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest())


def inspect_database(path: Path, decision_id: int) -> RecoverySnapshot:
    """Read one consistent snapshot without migrations, network or database creation."""
    with closing(_open(path, readonly=True)) as conn, conn:
        conn.execute("BEGIN")
        return _snapshot(_read_snapshot(conn, decision_id))


def _validate_request(request: Confirmation) -> None:
    if not _OPERATOR.fullmatch(request.operator):
        raise RecoveryError("operator must be a non-secret ASCII identifier (1..32 characters)")
    if not _FINGERPRINT.fullmatch(request.expected_fingerprint):
        raise RecoveryError("expected fingerprint must be a lowercase SHA-256")
    if request.outcome not in ("no_fill", "accepted_existing_fill"):
        raise RecoveryError("unknown outcome; only no_fill or accepted_existing_fill allowed")
    if not all(value is True for value in asdict(request.checks).values()):
        raise RecoveryError("runtime stop and manual trade/position/balance checks are required")


def _decimal(value: Scalar) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise RecoveryError("invalid accounting number; no repair attempted") from exc
    if not result.is_finite():
        raise RecoveryError("non-finite accounting number; no repair attempted")
    return result


def _positive_integer(value: Scalar) -> int:
    if type(value) is not int or value <= 0:
        raise RecoveryError("positive integer lots/lot_size required")
    return value


def _validate_outcome(data: _SnapshotData, outcome: Outcome) -> None:
    decision, trades = data["decision"], data["linked_trades"]
    if decision["processing_state"] not in ("pending", "failed"):
        raise RecoveryError("only pending/failed paper claims may be confirmed")
    try:
        bar = datetime.fromisoformat(str(decision["source_bar_ts"]))
    except ValueError as exc:
        raise RecoveryError("claim requires an existing valid source bar key") from exc
    if (
        bar.utcoffset() is None
        or bar.astimezone(UTC).isoformat() != decision["source_bar_ts"]
        or bar.minute % 5
        or bar.second
        or bar.microsecond
    ):
        raise RecoveryError("claim requires the canonical UTC 5m source bar key")
    if len(data["paper_balances"]) != 1:
        raise RecoveryError("paper balance missing; no repair attempted")
    _decimal(data["paper_balances"][0]["balance"])
    if outcome == "no_fill":
        if trades:
            raise RecoveryError("no_fill is forbidden when linked trades exist")
        if any(row["journal_id"] == decision["id"] for row in data["paper_positions"]):
            raise RecoveryError("no_fill is forbidden when a linked position exists")
        return
    if not trades or decision["side"] not in ("long", "short") or decision["verdict"] != "signal":
        raise RecoveryError("accepted_existing_fill requires linked trades and a signal")
    # Only an opening-only journal has an unambiguous last balance by id.
    # FIFO closes update older ids, so do NOT pretend this is a cash/FIFO audit.
    latest = data["latest_paper_trade"]
    if (
        latest
        and data["paper_trade_summary"][0]["closed"] == 0
        and (_decimal(latest[0]["balance_after"]) != _decimal(data["paper_balances"][0]["balance"]))
    ):
        raise RecoveryError("paper balance disagrees with opening-only trade journal")
    total = 0
    for trade in trades:
        if any(trade[key] != decision[key] for key in ("mode", "ticker", "side")):
            raise RecoveryError("linked trade mode/ticker/side disagrees with decision")
        total += _positive_integer(trade["lots"])
        _positive_integer(trade["lot_size"])
        if trade["status"] not in ("open", "closed") or _decimal(trade["price"]) <= 0:
            raise RecoveryError("linked trade status/price is invalid")
        if _decimal(trade["fee"]) < 0 or _decimal(trade["slippage"]) < 0:
            raise RecoveryError("linked trade costs are invalid")
    if total > _positive_integer(decision["size_lots"]):
        raise RecoveryError("linked trade lots exceed permitted size")
    open_trades = [row for row in data["paper_open_trades"] if row["ticker"] == decision["ticker"]]
    positions = [row for row in data["paper_positions"] if row["ticker"] == decision["ticker"]]
    if open_trades:
        if len(positions) != 1 or any(row["side"] != decision["side"] for row in open_trades):
            raise RecoveryError("open trades require one matching paper position")
        position = positions[0]
        if position["side"] != decision["side"] or position["lots"] != sum(
            _positive_integer(row["lots"]) for row in open_trades
        ):
            raise RecoveryError("position side/lots disagree with open trades")
    elif positions:
        raise RecoveryError("position exists without open trades")


def confirm_database(path: Path, request: Confirmation) -> None:
    """Lock, re-read, compare, then atomically append audit and complete one claim.

    Completion acknowledges a manually accepted/skipped bar, not a new fill.
    The lock cannot stop an already-running process; stop-first is mandatory.
    """
    _validate_request(request)
    with closing(_open(path, readonly=False)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        data = _read_snapshot(conn, request.decision_id)
        snapshot = _snapshot(data)
        if snapshot.fingerprint != request.expected_fingerprint:
            raise RecoveryError("stale snapshot fingerprint; inspect and manually review again")
        _validate_outcome(data, request.outcome)
        conn.execute(
            "INSERT INTO paper_manual_reconciliations "
            "(decision_id, previous_state, ts, operator, fingerprint, "
            "confirmations, outcome, snapshot) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                request.decision_id,
                data["decision"]["processing_state"],
                datetime.now(tz=UTC).isoformat(),
                request.operator,
                snapshot.fingerprint,
                json.dumps(asdict(request.checks), sort_keys=True),
                request.outcome,
                snapshot.snapshot_json,
            ),
        )
        updated = conn.execute(
            "UPDATE decisions SET processing_state='complete' "
            "WHERE id=? AND mode='paper' AND processing_state IN ('pending','failed')",
            (request.decision_id,),
        )
        if updated.rowcount != 1:
            raise RecoveryError("claim changed; transaction rolled back")


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        """Reject malformed CLI input without echoing possible secrets."""
        # Do not echo rejected notes or possible secrets supplied as arguments.
        self.exit(2, "invalid recovery arguments; see --help (no change committed)\n")


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(description="Offline paper reconciliation; stop ALL runtimes first.")
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "confirm"):
        child = commands.add_parser(command)
        child.add_argument("--db", required=True, type=Path)
        child.add_argument("--decision-id", required=True, type=int)
        if command == "confirm":
            child.add_argument("--operator", required=True)
            child.add_argument("--expected-fingerprint", required=True)
            child.add_argument(
                "--outcome", required=True, choices=("no_fill", "accepted_existing_fill")
            )
            for flag in (
                "runtime-stopped",
                "trades-reviewed",
                "positions-reviewed",
                "balance-reviewed",
            ):
                child.add_argument(f"--{flag}", required=True, action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline CLI; failures stop with nonzero exit and sanitized diagnostics."""
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            snapshot = inspect_database(args.db, args.decision_id)
            print(
                json.dumps(
                    {
                        "fingerprint": snapshot.fingerprint,
                        "snapshot": json.loads(snapshot.snapshot_json),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
            )
        else:
            confirm_database(
                args.db,
                Confirmation(
                    decision_id=args.decision_id,
                    operator=args.operator,
                    expected_fingerprint=args.expected_fingerprint,
                    outcome=args.outcome,
                    checks=ManualChecks(
                        args.runtime_stopped,
                        args.trades_reviewed,
                        args.positions_reviewed,
                        args.balance_reviewed,
                    ),
                ),
            )
            print(json.dumps({"decision_id": args.decision_id, "processing_state": "complete"}))
    except (RecoveryError, sqlite3.Error, OSError) as exc:
        detail = (
            str(exc)
            if isinstance(exc, RecoveryError)
            else "local database operation failed; verify schema/access/lock and inspect again"
        )
        print(
            f"event=paper_manual_reconciliation_stopped decision_id={args.decision_id} "
            f"ts={datetime.now(tz=UTC).isoformat()} error_type={type(exc).__name__} "
            f"action=stop: {detail}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
