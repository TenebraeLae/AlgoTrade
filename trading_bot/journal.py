"""Сохранение решений стратегии (слой L4, docs/03_STRATEGY.md).

Разрешено: объединить Decision и RiskVerdict в одну строку SQLite через L1 database.
Запрещено: рассчитывать сигнал или риск здесь, отправлять заявки, терять отказ
при ошибке БД. Будущий executor вызывает record один раз на каждое решение.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from trading_bot.database import DecisionRecord, save_decision
from trading_bot.decision_filters import categorize_filters
from trading_bot.risk_manager import RiskVerdict
from trading_bot.source_events import SourceRefusal
from trading_bot.strategy import INDICATOR_SNAPSHOT_KEYS, Decision

logger = logging.getLogger(__name__)


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,99}\Z")


class DecisionJournal:
    """Пишет один полный snapshot на сигнал или отказ и отдаёт id для сделки."""

    def __init__(self, conn: sqlite3.Connection, *, mode: Literal["paper", "live"]) -> None:
        """Привязывает journal к соединению и неизменному режиму запуска."""
        if mode not in ("paper", "live"):
            raise ValueError(f"неизвестный режим journal: {mode!r}")
        self._conn = conn
        self._mode = mode

    def record(
        self,
        decision: Decision,
        *,
        risk_verdict: RiskVerdict | None = None,
        source_bar_ts: datetime | None = None,
        processing_state: Literal["complete", "pending", "failed"] = "complete",
    ) -> int:
        """Сохраняет Decision и необязательные 12 проверок риска как одну запись.

        Если риск отказал, итоговый вердикт в БД — отказ с причиной и именами
        проваленных проверок, а не успешный сигнал. Ошибка БД не подавляется.
        """
        missing = INDICATOR_SNAPSHOT_KEYS - decision.indicator_snapshot.keys()
        if missing:
            raise ValueError(f"journal snapshot неполон: {', '.join(sorted(missing))}")
        if risk_verdict is not None and decision.verdict != "signal":
            raise ValueError("риск нельзя применить к решению без сигнала")
        if risk_verdict is not None and risk_verdict.allowed and risk_verdict.max_lots < 1:
            raise ValueError("риск разрешил сделку без положительного размера")

        passed = decision.filters_passed
        failed = decision.filters_failed
        verdict = decision.verdict
        reason = decision.reason
        size_lots = None
        effective_risk_pct: Decimal | None = None
        if risk_verdict is not None:
            passed += tuple(f"risk:{check.name}" for check in risk_verdict.checks if check.passed)
            failed += tuple(
                f"risk:{check.name}: {check.reason}"
                for check in risk_verdict.checks
                if not check.passed
            )
            size_lots = risk_verdict.max_lots if risk_verdict.allowed else 0
            effective_risk_pct = (
                risk_verdict.effective_risk_pct if risk_verdict.allowed else Decimal(0)
            )
            if risk_verdict.allowed:
                reason = f"{decision.reason}; {risk_verdict.reason}"
            else:
                verdict = "no_signal"
                reason = risk_verdict.reason

        record = DecisionRecord(
            ts=decision.ts,
            ticker=decision.ticker,
            regime=decision.regime,
            side=decision.side,
            indicators=decision.indicator_snapshot,
            filters_passed=passed,
            filters_failed=failed,
            size_lots=size_lots,
            verdict=verdict,
            reason=reason,
            mode=self._mode,
            effective_risk_pct=effective_risk_pct,
            source_bar_ts=source_bar_ts,
            processing_state=processing_state,
            filter_categories=categorize_filters(decision, risk_verdict),
        )
        try:
            return save_decision(self._conn, record)
        except sqlite3.Error:
            logger.exception(
                "event=journal_write_failed ticker=%s ts=%s mode=%s",
                decision.ticker,
                decision.ts.isoformat(),
                self._mode,
            )
            raise

    def record_source_refusal(self, refusal: SourceRefusal) -> int:
        """Создаёт или обновляет открытый эпизод отказа источника атомарно.

        Повтор `(mode, ticker, stage, error_type)` увеличивает счётчик, а после
        восстановления новая серия создаёт отдельный эпизод. В БД попадают только
        стабильные имена этапа/типа ошибки, не текст исключения.
        """
        _validate_aware(refusal.observed_at, "observed_at")
        if refusal.source_bar_ts is not None:
            _validate_aware(refusal.source_bar_ts, "source_bar_ts")
        for name, value in (
            ("ticker", refusal.ticker),
            ("stage", refusal.stage),
            ("error_type", refusal.error_type),
        ):
            if not value or not _SAFE_IDENTIFIER.fullmatch(value):
                raise ValueError(f"{name} должен быть безопасным идентификатором")
        if refusal.action not in ("skip", "stop"):
            raise ValueError("неизвестное действие отказа источника")

        observed = refusal.observed_at.astimezone(UTC).isoformat()
        source_bar = (
            refusal.source_bar_ts.astimezone(UTC).isoformat()
            if refusal.source_bar_ts is not None
            else None
        )
        indicators = json.dumps(
            {key: "n/a" for key in sorted(INDICATOR_SNAPSHOT_KEYS)},
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            row = self._conn.execute(
                "SELECT id FROM paper_source_episodes "
                "WHERE mode = ? AND ticker = ? AND stage = ? AND error_type = ? "
                "AND status = 'open'",
                (self._mode, refusal.ticker, refusal.stage, refusal.error_type),
            ).fetchone()
            if row is not None:
                episode_id = int(row[0])
                self._conn.execute(
                    "UPDATE paper_source_episodes SET "
                    "last_seen = CASE WHEN last_seen < ? THEN ? ELSE last_seen END, "
                    "attempt_count = attempt_count + 1, "
                    "first_source_bar_ts = COALESCE(first_source_bar_ts, ?), "
                    "last_source_bar_ts = CASE WHEN ? IS NULL THEN last_source_bar_ts "
                    "WHEN last_source_bar_ts IS NULL OR last_source_bar_ts < ? THEN ? "
                    "ELSE last_source_bar_ts END, "
                    "action = CASE WHEN action = 'stop' OR ? = 'stop' THEN 'stop' ELSE 'skip' END "
                    "WHERE id = ?",
                    (
                        observed,
                        observed,
                        source_bar,
                        source_bar,
                        source_bar,
                        source_bar,
                        refusal.action,
                        episode_id,
                    ),
                )
            else:
                cursor = self._conn.execute(
                    "INSERT INTO paper_source_episodes "
                    "(mode, ticker, stage, error_type, action, first_seen, last_seen, "
                    "attempt_count, first_source_bar_ts, last_source_bar_ts, indicators, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, 'open')",
                    (
                        self._mode,
                        refusal.ticker,
                        refusal.stage,
                        refusal.error_type,
                        refusal.action,
                        observed,
                        observed,
                        source_bar,
                        source_bar,
                        indicators,
                    ),
                )
                assert cursor.lastrowid is not None
                episode_id = int(cursor.lastrowid)
            self._conn.commit()
            return episode_id
        except sqlite3.Error:
            self._conn.rollback()
            logger.exception(
                "event=source_refusal_journal_failed ticker=%s stage=%s error_type=%s mode=%s",
                refusal.ticker,
                refusal.stage,
                refusal.error_type,
                self._mode,
            )
            raise

    def resolve_source_refusals(self, ticker: str, observed_at: datetime) -> int:
        """Закрывает все открытые эпизоды указанного тикера; возвращает число строк."""
        _validate_aware(observed_at, "observed_at")
        if not ticker or not _SAFE_IDENTIFIER.fullmatch(ticker):
            raise ValueError("ticker должен быть безопасным идентификатором")
        recovered = observed_at.astimezone(UTC).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            cursor = self._conn.execute(
                "UPDATE paper_source_episodes SET status = 'recovered', recovered_at = ? "
                "WHERE mode = ? AND ticker = ? AND status = 'open'",
                (recovered, self._mode, ticker),
            )
            count = cursor.rowcount
            self._conn.commit()
            return count
        except sqlite3.Error:
            self._conn.rollback()
            logger.exception(
                "event=source_refusal_recovery_failed ticker=%s ts=%s mode=%s",
                ticker,
                recovered,
                self._mode,
            )
            raise


def _validate_aware(value: datetime, field: str) -> None:
    """Требует временную метку с явной временной зоной."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} должен иметь timezone")
