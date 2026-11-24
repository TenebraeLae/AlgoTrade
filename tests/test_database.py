"""Тесты database.py: PRAGMA, идемпотентные миграции (docs/06_DB_SCHEMA.md)."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from trading_bot import database
from trading_bot.portfolio import Portfolio, PortfolioError


def test_connect_creates_all_tables_from_schema(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "tbot.db")
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    expected = {
        "instruments",
        "candles_cache",
        "trades",
        "positions",
        "decisions",
        "day_stats",
        "optimizer_runs",
        "proposals",
        "secrets_meta",
        "event_log",
        "paper_equity_anchors",
        "paper_source_episodes",
        "paper_recovery_meta",
        "paper_daily_stop_latches",
        "schema_version",
    }
    assert expected <= tables


def test_connect_sets_required_pragmas(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "tbot.db")
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert journal_mode.lower() == "wal"
    assert foreign_keys == 1


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "tbot.db"
    conn = database.connect(db_path)
    versions_before = conn.execute("SELECT version FROM schema_version").fetchall()

    database.apply_migrations(conn)  # повторный вызов не должен упасть/задублировать
    versions_after = conn.execute("SELECT version FROM schema_version").fetchall()

    assert versions_before == versions_after
    assert len(versions_after) >= 1


def test_lot_size_and_effective_risk_migrate_existing_database(tmp_path: Path) -> None:
    old_migrations = tmp_path / "old_migrations"
    old_migrations.mkdir()
    for path in database.MIGRATIONS_DIR.glob("000[12]_*.sql"):
        (old_migrations / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    conn = sqlite3.connect(":memory:")
    database.apply_migrations(conn, migrations_dir=old_migrations)
    conn.execute(
        "INSERT INTO trades (ticker,side,lots,price,fee,slippage,mode,ts_open,"
        "status,balance_after) "
        "VALUES ('SBER','long',2,'100','0','0','paper','2026-01-01T10:00:00+00:00','open','9800')"
    )
    conn.commit()
    database.apply_migrations(conn)
    assert conn.execute("SELECT lot_size FROM trades").fetchone() == (None,)
    with pytest.raises(PortfolioError, match="лотност"):
        Portfolio(conn, mode="paper", starting_balance=Decimal("10000")).get_position("SBER")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
    assert {
        "effective_risk_pct",
        "source_bar_ts",
        "processing_state",
        "filter_categories",
    } <= columns
    assert conn.execute("SELECT version FROM schema_version ORDER BY version").fetchall() == [
        (1,),
        (2,),
        (3,),
        (4,),
        (5,),
        (6,),
        (7,),
        (8,),
        (9,),
        (10,),
    ]
    conn.close()


def test_daily_stop_latch_rejects_null_day_key(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "null_daily_stop.db")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO paper_daily_stop_latches "
            "(trading_day, triggered_at, capital_at_stops, threshold_equity) "
            "VALUES (NULL, '2026-03-02T09:00:00+00:00', '97000', '97000')"
        )
    conn.close()


def test_daily_stop_migration_preserves_existing_equity_anchors(tmp_path: Path) -> None:
    old_dir = tmp_path / "before_0008"
    old_dir.mkdir()
    for path in database.MIGRATIONS_DIR.glob("000[1-7]_*.sql"):
        (old_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    db_path = tmp_path / "before_stop.db"
    old = database.connect(db_path, migrations_dir=old_dir)
    old.execute(
        "INSERT INTO paper_equity_anchors VALUES ('daily', '2026-03-02', '100000', ?)",
        ("2026-03-02T07:05:00+00:00",),
    )
    old.commit()
    old.close()

    upgraded = database.connect(db_path)
    assert upgraded.execute(
        "SELECT equity FROM paper_equity_anchors WHERE anchor_key='2026-03-02'"
    ).fetchone() == ("100000",)
    assert upgraded.execute("SELECT version FROM schema_version WHERE version=8").fetchone() == (8,)
    assert upgraded.execute("SELECT COUNT(*) FROM paper_daily_stop_latches").fetchone() == (0,)
    upgraded.close()


def test_legacy_cutoff_marks_only_databases_with_old_paper_decisions(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    for path in database.MIGRATIONS_DIR.glob("000[1-5]_*.sql"):
        (old_dir / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    legacy_path = tmp_path / "legacy.db"
    old_conn = database.connect(legacy_path, migrations_dir=old_dir)
    old_conn.execute(
        "INSERT INTO decisions (ts, ticker, mode) VALUES (?, 'SBER', 'paper')",
        ("2026-03-02T09:00:00+00:00",),
    )
    old_conn.commit()
    old_conn.close()

    legacy = database.connect(legacy_path)
    cutoff, legacy_flag = legacy.execute(
        "SELECT cutoff_at, has_legacy_paper_decisions FROM paper_recovery_meta"
    ).fetchone()
    assert (cutoff, legacy_flag) == (
        legacy.execute("SELECT applied_at FROM schema_version WHERE version=6").fetchone()[0],
        1,
    )
    assert legacy.execute("SELECT source_bar_ts FROM decisions").fetchone() == (None,)
    legacy.close()

    fresh = database.connect(tmp_path / "fresh.db")
    assert fresh.execute(
        "SELECT has_legacy_paper_decisions FROM paper_recovery_meta"
    ).fetchone() == (0,)
    fresh.close()


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path) -> None:
    """Ошибка после DDL не оставляет схему 0006 без записи schema_version."""
    previous = tmp_path / "previous"
    previous.mkdir()
    broken = tmp_path / "broken"
    broken.mkdir()
    for path in database.MIGRATIONS_DIR.glob("000[1-5]_*.sql"):
        contents = path.read_text(encoding="utf-8")
        (previous / path.name).write_text(contents, encoding="utf-8")
        (broken / path.name).write_text(contents, encoding="utf-8")
    (broken / "0006_broken.sql").write_text(
        "ALTER TABLE decisions ADD COLUMN source_bar_ts TEXT;\n"
        "INSERT INTO missing_table VALUES (1);\n",
        encoding="utf-8",
    )
    conn = database.connect(tmp_path / "atomic.db", migrations_dir=previous)
    with pytest.raises(sqlite3.OperationalError, match="missing_table"):
        database.apply_migrations(conn, migrations_dir=broken)
    assert "source_bar_ts" not in {row[1] for row in conn.execute("PRAGMA table_info(decisions)")}
    assert conn.execute("SELECT MAX(version) FROM schema_version").fetchone() == (5,)
    conn.close()


def test_connect_creates_db_file_and_parent_dirs(tmp_path: Path) -> None:
    db_path = tmp_path / "nested" / "dir" / "tbot.db"
    database.connect(db_path)
    assert db_path.is_file()
