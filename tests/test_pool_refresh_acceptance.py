"""Independent hostile-journal and publication-boundary acceptance for M7 pools."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from trading_bot import database
from trading_bot.api_client import InvestGateway, ShareMetadata
from trading_bot.pool import LiquidityRules
from trading_bot.pool_refresh import PoolJournal, PoolRefresh, RefreshDependencies, Universe

_NOW = datetime(2026, 10, 16, 9, tzinfo=UTC)


def _universe() -> Universe:
    return Universe(
        generation=1,
        slot=_NOW,
        selected=("SBER",),
        monitoring=(),
        metadata={
            "SBER": ShareMetadata(
                ticker="SBER",
                instrument_id="pinned-id",
                lot_size=10,
                min_price_increment=Decimal("0.01"),
                currency="RUB",
                sector="financials",
                short_enabled=True,
                trading_status="SECURITY_TRADING_STATUS_NORMAL_TRADING",
            )
        },
        index_id="index-id",
    )


def _write_payload(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    with conn:
        conn.execute(
            "INSERT INTO event_log(ts,level,module,event,ctx,notify_vk) VALUES(?,?,?,?,?,?)",
            (
                _NOW.isoformat(),
                "INFO",
                "pool_refresh",
                "pool_refresh_applied",
                json.dumps(payload),
                0,
            ),
        )


def _valid_payload(conn: sqlite3.Connection) -> dict[str, Any]:
    PoolJournal(conn).applied(_universe(), _NOW, {})
    raw = conn.execute("SELECT ctx FROM event_log ORDER BY rowid DESC LIMIT 1").fetchone()[0]
    result: dict[str, Any] = json.loads(raw)
    return result


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("version", True),
        ("version", 2),
        ("outcome", "failed"),
        ("generation", True),
        ("generation", 0),
        ("generation", "1"),
        ("generation", 1.5),
        ("slot", "2026-10-16T09:00:00"),
        ("slot", "2026-10-16T09:07:00+00:00"),
        ("selected", ["SBER", "SBER"]),
        ("selected", ["UNKNOWN"]),
        ("index_id", ""),
    ],
)
def test_recovery_never_accepts_invalid_latest_pool_envelope(
    tmp_path: Path, field: str, bad: object
) -> None:
    conn = database.connect(tmp_path / "envelope.db")
    try:
        payload = _valid_payload(conn)
        payload[field] = bad
        _write_payload(conn, payload)
        with pytest.raises(ValueError):
            PoolJournal(conn).recover(frozenset({"SBER"}))
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("field", "bad"),
    [
        ("ticker", "ANOTHER"),
        ("instrument_id", ""),
        ("lot_size", 0),
        ("lot_size", True),
        ("lot_size", "10"),
        ("min_price_increment", "NaN"),
        ("min_price_increment", "0"),
        ("min_price_increment", 0.01),
        ("currency", ""),
        ("sector", "  "),
        ("short_enabled", "false"),
    ],
)
def test_recovery_never_remaps_or_coerces_hostile_open_identity(
    tmp_path: Path, field: str, bad: object
) -> None:
    conn = database.connect(tmp_path / "identity.db")
    try:
        payload = _valid_payload(conn)
        payload["metadata"]["SBER"][field] = bad
        _write_payload(conn, payload)
        with pytest.raises(ValueError):
            PoolJournal(conn).recover(frozenset({"SBER"}))
    finally:
        conn.close()


def test_unlisted_open_ticker_is_not_proven_by_extra_catalog_metadata(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "unlisted.db")
    try:
        payload = _valid_payload(conn)
        payload["selected"] = []
        _write_payload(conn, payload)
        with pytest.raises(ValueError):
            PoolJournal(conn).recover(frozenset({"SBER"}))
    finally:
        conn.close()


def test_pool_applied_sql_failure_rolls_back_without_touching_account(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "sql_failure.db")
    try:
        before = conn.iterdump()
        snapshot = tuple(before)
        conn.execute(
            "CREATE TRIGGER reject_pool BEFORE INSERT ON event_log "
            "BEGIN SELECT RAISE(ABORT, 'pool journal unavailable'); END"
        )
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError, match="pool journal unavailable"):
            PoolJournal(conn).applied(_universe(), _NOW, {})
        assert not conn.in_transaction
        assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone() == (0,)
        conn.execute("DROP TRIGGER reject_pool")
        conn.commit()
        assert tuple(conn.iterdump()) == snapshot
    finally:
        conn.close()


def test_recovery_uses_latest_proven_snapshot_without_requerying_ticker(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "recover.db")
    try:
        PoolJournal(conn).applied(_universe(), _NOW, {})
        restored = PoolJournal(conn).recover(frozenset({"SBER"}))
        assert restored == dict(_universe().metadata)
        assert restored["SBER"].instrument_id == "pinned-id"
        assert PoolJournal(conn).generation() == 1
    finally:
        conn.close()


def test_clock_rollback_is_durably_reported_once_without_reopening_gate(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "rollback.db")
    clock = [_NOW]
    published: list[Universe] = []
    try:
        refresh = PoolRefresh(
            RefreshDependencies(
                gateway=InvestGateway("synthetic-unused", target="unused-no-network"),
                journal=PoolJournal(conn),
                clock=lambda: clock[0],
                open_tickers=frozenset,
                publish=published.append,
                rules=LiquidityRules(1, Decimal("0.4"), 1, 3),
                holidays=frozenset(),
            )
        )
        universe = _universe()
        refresh.seed(universe, {"SBER": [100]})
        clock[0] -= timedelta(seconds=1)
        refresh.check_due()
        refresh.check_due()
        refresh.step()
        assert not refresh.gate.check(clock[0]).allowed
        clock[0] = _NOW + timedelta(minutes=30)
        refresh.step()
        assert not refresh.gate.check(clock[0]).allowed
        assert published == [universe]
        failed = conn.execute(
            "SELECT ctx FROM event_log WHERE event='pool_refresh_failed'"
        ).fetchall()
        assert len(failed) == 1
        assert json.loads(failed[0][0])["error_type"] == "PoolClockRollback"
    finally:
        conn.close()
