"""Хранилище SQLite (слой L1, docs/06_DB_SCHEMA.md).

Разрешено: открыть соединение с нужными `PRAGMA`, применить пронумерованные
миграции из `migrations/` идемпотентно (таблица `schema_version`). Запрещено:
бизнес-логика учёта здесь не живёт (это `portfolio.py`); ручных `ALTER TABLE`
в рантайме нет — только новые файлы миграций (docs/02_CODING_RULES.md, «БД и код»).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

#: Каталог миграций — на уровне корня репозитория, рядом с `trading_bot/`.
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


@dataclass(frozen=True)
class FilterCategoryRecord:
    """Одна категория docs/03 со статусом и реально выполненными проверками."""

    category_id: int
    name: str
    status: Literal["passed", "failed", "not_evaluated"]
    checks_passed: tuple[str, ...]
    checks_failed: tuple[str, ...]


@dataclass(frozen=True)
class DecisionRecord:
    """Схема записи `decisions` без зависимости L1 от бизнес-логики стратегии."""

    ts: datetime
    ticker: str
    regime: str
    side: str | None
    indicators: dict[str, str]
    filters_passed: tuple[str, ...]
    filters_failed: tuple[str, ...]
    size_lots: int | None
    verdict: str
    reason: str
    mode: Literal["paper", "live"]
    effective_risk_pct: Decimal | None = None
    source_bar_ts: datetime | None = None
    processing_state: Literal["complete", "pending", "failed"] = "complete"
    filter_categories: tuple[FilterCategoryRecord, ...] | None = None


def connect(db_path: Path, *, migrations_dir: Path = MIGRATIONS_DIR) -> sqlite3.Connection:
    """Открывает соединение с нужными `PRAGMA` (docs/06_DB_SCHEMA.md) и применяет миграции.

    Возврат: готовое к работе соединение — таблицы существуют, PRAGMA применены.
    Отказ: не бросает специальных исключений — ошибки `sqlite3` (например, битый
    файл БД) распространяются как есть, вызывающий код решает, что делать
    (в `main.py`/`portfolio.py` это критическая ошибка старта, не тихий пропуск).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    apply_migrations(conn, migrations_dir=migrations_dir)
    return conn


def apply_migrations(conn: sqlite3.Connection, *, migrations_dir: Path = MIGRATIONS_DIR) -> None:
    """Применяет файлы `NNNN_*.sql` из `migrations_dir` по возрастанию номера, один раз.

    Возврат: нет (побочный эффект — изменённая схема + запись в `schema_version`).
    Отказ: `sqlite3.Error` распространяется; DDL и запись версии одного файла
    откатываются вместе. SQL-файлы миграций не содержат BEGIN/COMMIT.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM schema_version")}

    for path in sorted(migrations_dir.glob("*.sql")):
        version = int(path.stem.split("_", 1)[0])
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            # executescript() коммитит предыдущую транзакцию: BEGIN должен быть
            # ВНУТРИ скрипта, а запись schema_version — до общего COMMIT.
            conn.executescript("BEGIN IMMEDIATE;\n" + sql)
            conn.execute(
                "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(tz=UTC).isoformat()),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise


def save_decision(conn: sqlite3.Connection, record: DecisionRecord) -> int:
    """Сохраняет сигнал или отказ в SQLite и возвращает его id для `trades.journal_id`.

    Каждая запись фиксируется до открытия сделки; ошибки валидации и SQLite
    распространяются вызывающему коду, чтобы отсутствие journal не стало незаметным.
    Даты нормализуются в UTC, Decimal сериализуется на уровне стратегии строками.
    """
    if record.ts.tzinfo is None or record.ts.utcoffset() is None:
        raise ValueError("journal timestamp должен иметь timezone")
    if record.source_bar_ts is not None and record.mode != "paper":
        raise ValueError("source_bar_ts допускается только для paper-решений")
    if record.source_bar_ts is not None and (
        record.source_bar_ts.tzinfo is None or record.source_bar_ts.utcoffset() is None
    ):
        raise ValueError("source_bar_ts должен иметь timezone")
    if record.processing_state not in ("complete", "pending", "failed"):
        raise ValueError("неизвестное состояние обработки решения")
    if record.processing_state != "complete" and (
        record.mode != "paper" or record.source_bar_ts is None
    ):
        raise ValueError("незавершённое решение требует ключ бумажного бара")
    if not record.indicators:
        raise ValueError("journal snapshot не может быть пустым")
    if record.mode not in ("paper", "live"):
        raise ValueError("неизвестный режим записи journal")
    if record.size_lots is not None and record.size_lots < 0:
        raise ValueError("size_lots не может быть отрицательным")
    if record.effective_risk_pct is not None and (
        not record.effective_risk_pct.is_finite() or record.effective_risk_pct < 0
    ):
        raise ValueError("effective_risk_pct должен быть неотрицательным конечным Decimal")
    categories = record.filter_categories
    if categories is not None and (
        tuple(item.category_id for item in categories) != tuple(range(1, 11))
        or any(item.status not in ("passed", "failed", "not_evaluated") for item in categories)
    ):
        raise ValueError("journal требует категории 1–10 с допустимым статусом")
    categories_json = (
        None
        if categories is None
        else json.dumps(
            {
                "version": 1,
                "categories": [
                    {
                        "id": item.category_id,
                        "name": item.name,
                        "status": item.status,
                        "checks_passed": item.checks_passed,
                        "checks_failed": item.checks_failed,
                    }
                    for item in categories
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    cursor = conn.execute(
        "INSERT INTO decisions "
        "(ts, ticker, regime, side, indicators, filters_passed, filters_failed, "
        "size_lots, verdict, reason, mode, effective_risk_pct, source_bar_ts, "
        "processing_state, filter_categories) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            record.ts.astimezone(UTC).isoformat(),
            record.ticker,
            record.regime,
            record.side,
            json.dumps(record.indicators, ensure_ascii=False, sort_keys=True),
            json.dumps(record.filters_passed, ensure_ascii=False),
            json.dumps(record.filters_failed, ensure_ascii=False),
            record.size_lots,
            record.verdict,
            record.reason,
            record.mode,
            str(record.effective_risk_pct) if record.effective_risk_pct is not None else None,
            (
                record.source_bar_ts.astimezone(UTC).isoformat()
                if record.source_bar_ts is not None
                else None
            ),
            record.processing_state,
            categories_json,
        ),
    )
    conn.commit()
    decision_id = cursor.lastrowid
    assert decision_id is not None
    return decision_id
