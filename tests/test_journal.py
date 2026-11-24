"""Сквозные SQLite-тесты журнала сигналов и отказов (M7)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pytest

from tests.test_strategy import _config, _long_snapshot
from trading_bot import database, strategy
from trading_bot.journal import DecisionJournal
from trading_bot.portfolio import Portfolio
from trading_bot.risk_manager import CheckResult, RiskVerdict

if TYPE_CHECKING:
    from trading_bot.source_events import SourceRefusal

_MSK = timezone(timedelta(hours=3))
_NOW = datetime(2026, 3, 2, 12, 0, tzinfo=_MSK)


def _evaluate(*, session_open: bool) -> strategy.Decision:
    return strategy.evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=_NOW,
        is_session_open=session_open,
        session_reason="ok" if session_open else "вне сессии",
        short_enabled=True,
        session_guard_active=False,
        spread_bps=Decimal("12"),
    )


def test_journal_saves_signal_and_every_early_refusal_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "decisions.db"
    conn = database.connect(db_path)
    journal = DecisionJournal(conn, mode="paper")
    with monkeypatch.context() as patch:
        patch.setattr(strategy, "compute_snapshot", lambda **_kwargs: _long_snapshot())
        signal = _evaluate(session_open=True)
    assert signal.verdict == "signal"
    closed_session = _evaluate(session_open=False)
    missing_history = _evaluate(session_open=True)
    ids = [journal.record(decision) for decision in (signal, closed_session, missing_history)]
    conn.close()

    conn = database.connect(db_path)
    rows = conn.execute(
        "SELECT id, ts, ticker, regime, side, indicators, filters_passed, "
        "filters_failed, size_lots, verdict, reason, mode FROM decisions ORDER BY id"
    ).fetchall()
    assert [row[0] for row in rows] == ids
    assert [row[9] for row in rows] == ["signal", "no_signal", "no_signal"]
    assert all(row[1] == _NOW.astimezone(UTC).isoformat() for row in rows)
    assert all(row[2] == "SBER" and row[11] == "paper" for row in rows)
    assert rows[0][4] == "long"
    assert rows[0][8] is None  # размер появляется только после риск-проверки
    assert conn.execute("SELECT effective_risk_pct FROM decisions ORDER BY id").fetchall() == [
        (None,),
        (None,),
        (None,),
    ]
    for row in rows:
        indicators = json.loads(row[5])
        assert indicators.keys() >= strategy.INDICATOR_SNAPSHOT_KEYS
        assert indicators  # не пустой JSON даже при отказе до индикаторов
        assert isinstance(json.loads(row[6]), list)
        assert isinstance(json.loads(row[7]), list)
    assert json.loads(rows[0][5])["volume_avg20"] == "500"
    assert json.loads(rows[1][5])["ema9"] == "n/a"
    assert json.loads(rows[2][5])["adx"] == "n/a"
    assert json.loads(rows[1][7]) == ["session_open"]
    assert json.loads(rows[2][7]) == ["insufficient_history"]
    conn.close()


def test_journal_merges_risk_checks_and_links_approved_trade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = database.connect(tmp_path / "linked.db")
    with monkeypatch.context() as patch:
        patch.setattr(strategy, "compute_snapshot", lambda **_kwargs: _long_snapshot())
        decision = _evaluate(session_open=True)
    journal = DecisionJournal(conn, mode="paper")
    risk = RiskVerdict(
        allowed=True,
        reason="все проверки пройдены; связывающий потолок: cap",
        max_lots=2,
        checks=(CheckResult("instrument_status_normal", True, "NORMAL_TRADING"),),
        effective_risk_pct=Decimal("0.25"),
    )
    journal_id = journal.record(decision, risk_verdict=risk)
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("10000"))
    trade_id = portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=2,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("1"),
        slippage=Decimal("0"),
        journal_id=journal_id,
        opened_at=_NOW.astimezone(UTC),
    )
    row = conn.execute("SELECT journal_id FROM trades WHERE id = ?", (trade_id,)).fetchone()
    recorded = conn.execute(
        "SELECT size_lots, filters_passed, verdict FROM decisions WHERE id = ?", (journal_id,)
    ).fetchone()
    assert row == (journal_id,)
    assert recorded[0] == 2
    assert "risk:instrument_status_normal" in json.loads(recorded[1])
    assert recorded[2] == "signal"
    effective = conn.execute(
        "SELECT effective_risk_pct FROM decisions WHERE id = ?", (journal_id,)
    ).fetchone()
    assert effective == ("0.25",)
    saved_reason = conn.execute(
        "SELECT reason FROM decisions WHERE id = ?", (journal_id,)
    ).fetchone()
    assert "связывающий потолок: cap" in saved_reason[0]
    portfolio.close_position(
        ticker="SBER",
        lots=2,
        exit_price=Decimal("110"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="take",
        closed_at=(_NOW + timedelta(minutes=5)).astimezone(UTC),
    )
    linked = conn.execute(
        "SELECT trades.id, trades.pnl_net FROM decisions "
        "JOIN trades ON trades.journal_id = decisions.id "
        "WHERE decisions.id = ?",
        (journal_id,),
    ).fetchall()
    assert linked == [(trade_id, "19")]
    conn.close()


def test_journal_records_risk_refusal_once_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    conn = database.connect(tmp_path / "denied.db")
    monkeypatch.setattr(strategy, "compute_snapshot", lambda **_kwargs: _long_snapshot())
    decision = _evaluate(session_open=True)
    risk = RiskVerdict(
        allowed=False,
        reason="статус инструмента не NORMAL_TRADING",
        max_lots=0,
        checks=(CheckResult("instrument_status_normal", False, "статус HALTED"),),
    )
    decision_id = DecisionJournal(conn, mode="paper").record(decision, risk_verdict=risk)
    rows = conn.execute(
        "SELECT id, side, size_lots, filters_failed, verdict, reason FROM decisions"
    ).fetchall()
    assert len(rows) == 1
    assert rows[0][0:3] == (decision_id, "long", 0)
    assert json.loads(rows[0][3]) == ["risk:instrument_status_normal: статус HALTED"]
    assert rows[0][4:] == ("no_signal", risk.reason)
    assert conn.execute("SELECT effective_risk_pct FROM decisions").fetchone() == ("0",)
    conn.close()


def test_journal_rejects_missing_snapshot_naive_timestamp_and_risk_for_no_signal(
    tmp_path: Path,
) -> None:
    conn = database.connect(tmp_path / "invalid.db")
    journal = DecisionJournal(conn, mode="paper")
    refusal = _evaluate(session_open=False)
    with pytest.raises(ValueError, match="snapshot"):
        journal.record(replace(refusal, indicator_snapshot={}))
    with pytest.raises(ValueError, match="timezone"):
        journal.record(replace(refusal, ts=_NOW.replace(tzinfo=None)))
    with pytest.raises(ValueError, match="риск"):
        journal.record(refusal, risk_verdict=RiskVerdict(True, "ok", 1, ()))
    assert conn.execute("SELECT count(*) FROM decisions").fetchone()[0] == 0
    conn.close()


def test_journal_does_not_hide_sqlite_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = database.connect(tmp_path / "broken.db")
    conn.execute("DROP TABLE decisions")
    conn.commit()
    with pytest.raises(sqlite3.OperationalError, match="decisions"):
        DecisionJournal(conn, mode="paper").record(_evaluate(session_open=False))
    assert "journal_write_failed" in caplog.text
    assert "SBER" in caplog.text
    assert "paper" in caplog.text
    conn.close()


_BAR_OPEN = datetime(2026, 3, 2, 8, 55, tzinfo=_MSK)


def _refusal(
    *,
    ticker: str = "SBER",
    stage: str = "candles_5m",
    error_type: str = "empty_history",
    observed_at: datetime = _NOW,
    source_bar_ts: datetime | None = None,
    action: Literal["skip", "stop"] = "skip",
) -> SourceRefusal:
    """Строит отказ источника с ленивым импортом нового контракта."""
    from trading_bot import source_events

    return source_events.SourceRefusal(
        ticker=ticker,
        stage=stage,
        observed_at=observed_at,
        source_bar_ts=source_bar_ts,
        action=action,
        error_type=error_type,
    )


def _episodes(conn: sqlite3.Connection) -> list[dict[str, object]]:
    """Читает все эпизоды отказа в порядке создания как словари по именам колонок."""
    cursor = conn.execute("SELECT * FROM paper_source_episodes ORDER BY id")
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]


def test_first_source_refusal_opens_one_episode_in_utc(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "episodes.db")
    journal = DecisionJournal(conn, mode="paper")
    episode_id = journal.record_source_refusal(_refusal(source_bar_ts=_BAR_OPEN))
    rows = _episodes(conn)
    assert len(rows) == 1
    assert rows[0]["id"] == episode_id
    assert rows[0]["mode"] == "paper"
    assert (rows[0]["ticker"], rows[0]["stage"], rows[0]["error_type"]) == (
        "SBER",
        "candles_5m",
        "empty_history",
    )
    assert rows[0]["attempt_count"] == 1
    assert rows[0]["action"] == "skip"
    assert rows[0]["status"] == "open"
    assert rows[0]["recovered_at"] is None
    assert rows[0]["first_seen"] == rows[0]["last_seen"]
    assert datetime.fromisoformat(str(rows[0]["first_source_bar_ts"])) == _BAR_OPEN
    first_seen = datetime.fromisoformat(str(rows[0]["first_seen"]))
    assert first_seen == _NOW
    assert first_seen.utcoffset() == timedelta(0)
    assert datetime.fromisoformat(str(rows[0]["last_source_bar_ts"])) == _BAR_OPEN
    indicators = json.loads(str(rows[0]["indicators"]))
    assert indicators.keys() >= strategy.INDICATOR_SNAPSHOT_KEYS
    conn.close()


def test_repeated_open_episode_increments_attempts_without_new_row(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "repeat.db")
    journal = DecisionJournal(conn, mode="paper")
    first_id = journal.record_source_refusal(_refusal(source_bar_ts=_BAR_OPEN))
    later = _NOW + timedelta(minutes=5)
    later_bar = _BAR_OPEN + timedelta(minutes=5)
    second_id = journal.record_source_refusal(_refusal(observed_at=later, source_bar_ts=later_bar))
    rows = _episodes(conn)
    assert second_id == first_id
    assert len(rows) == 1
    assert rows[0]["attempt_count"] == 2
    assert datetime.fromisoformat(str(rows[0]["first_seen"])) == _NOW
    assert datetime.fromisoformat(str(rows[0]["last_seen"])) == later
    assert datetime.fromisoformat(str(rows[0]["last_source_bar_ts"])) == later_bar
    assert datetime.fromisoformat(str(rows[0]["first_source_bar_ts"])) == _BAR_OPEN
    conn.close()


def test_source_refusals_differing_in_ticker_stage_error_or_mode_open_separate_rows(
    tmp_path: Path,
) -> None:
    conn = database.connect(tmp_path / "distinct.db")
    journal = DecisionJournal(conn, mode="paper")
    base_id = journal.record_source_refusal(_refusal())
    other_ids = [
        journal.record_source_refusal(_refusal(stage="candles_15m")),
        journal.record_source_refusal(_refusal(error_type="stale_bar")),
        journal.record_source_refusal(_refusal(ticker="GAZP")),
        DecisionJournal(conn, mode="live").record_source_refusal(_refusal()),
    ]
    assert len({base_id, *other_ids}) == 5
    rows = _episodes(conn)
    keys = [(row["mode"], row["ticker"], row["stage"], row["error_type"]) for row in rows]
    assert keys == [
        ("paper", "SBER", "candles_5m", "empty_history"),
        ("paper", "SBER", "candles_15m", "empty_history"),
        ("paper", "SBER", "candles_5m", "stale_bar"),
        ("paper", "GAZP", "candles_5m", "empty_history"),
        ("live", "SBER", "candles_5m", "empty_history"),
    ]
    assert [row["attempt_count"] for row in rows] == [1, 1, 1, 1, 1]
    conn.close()


def test_resolve_closes_only_this_ticker_and_next_refusal_opens_new_row(
    tmp_path: Path,
) -> None:
    conn = database.connect(tmp_path / "resolve.db")
    journal = DecisionJournal(conn, mode="paper")
    journal.record_source_refusal(_refusal(stage="candles_5m"))
    journal.record_source_refusal(_refusal(stage="quote"))
    journal.record_source_refusal(_refusal(ticker="GAZP"))
    recovered_at = _NOW + timedelta(minutes=5)
    assert journal.resolve_source_refusals("SBER", recovered_at) == 2
    journal.record_source_refusal(_refusal(stage="candles_5m", observed_at=recovered_at))
    journal.record_source_refusal(_refusal(stage="quote", observed_at=recovered_at))
    journal.record_source_refusal(_refusal(ticker="GAZP", observed_at=recovered_at))
    rows = _episodes(conn)
    assert [(row["ticker"], row["stage"], row["attempt_count"]) for row in rows] == [
        ("SBER", "candles_5m", 1),
        ("SBER", "quote", 1),
        ("GAZP", "candles_5m", 2),
        ("SBER", "candles_5m", 1),
        ("SBER", "quote", 1),
    ]
    assert [row["status"] for row in rows] == [
        "recovered",
        "recovered",
        "open",
        "open",
        "open",
    ]
    assert all(row["recovered_at"] is not None for row in rows[:2])
    conn.close()


def test_source_episodes_survive_sqlite_close_and_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "reopen.db"
    conn = database.connect(db_path)
    DecisionJournal(conn, mode="paper").record_source_refusal(_refusal())
    conn.close()

    conn = database.connect(db_path)
    journal = DecisionJournal(conn, mode="paper")
    journal.record_source_refusal(_refusal(observed_at=_NOW + timedelta(minutes=1)))
    conn.close()

    conn = database.connect(db_path)
    assert [row["attempt_count"] for row in _episodes(conn)] == [2]
    journal = DecisionJournal(conn, mode="paper")
    journal.resolve_source_refusals("SBER", _NOW + timedelta(minutes=2))
    conn.close()

    conn = database.connect(db_path)
    journal = DecisionJournal(conn, mode="paper")
    journal.record_source_refusal(_refusal(observed_at=_NOW + timedelta(minutes=3)))
    rows = _episodes(conn)
    assert [row["attempt_count"] for row in rows] == [2, 1]
    assert datetime.fromisoformat(str(rows[0]["last_seen"])) == _NOW + timedelta(minutes=1)
    conn.close()


def test_source_episode_snapshot_is_json_with_all_indicator_keys_as_na(
    tmp_path: Path,
) -> None:
    conn = database.connect(tmp_path / "snapshot.db")
    DecisionJournal(conn, mode="paper").record_source_refusal(_refusal())
    rows = _episodes(conn)
    snapshot = json.loads(str(rows[0]["indicators"]))
    assert snapshot.keys() == strategy.INDICATOR_SNAPSHOT_KEYS
    assert all(snapshot[key] == "n/a" for key in strategy.INDICATOR_SNAPSHOT_KEYS)
    assert all("Traceback" not in str(value) for value in rows[0].values())
    conn.close()


def test_naive_observed_at_is_rejected_and_writes_no_episode(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "naive.db")
    journal = DecisionJournal(conn, mode="paper")
    with pytest.raises(ValueError, match="timezone"):
        journal.record_source_refusal(_refusal(observed_at=_NOW.replace(tzinfo=None)))
    assert _episodes(conn) == []
    conn.close()


def test_live_decision_cannot_claim_paper_bar(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "live_key.db")
    with pytest.raises(ValueError, match="paper"):
        DecisionJournal(conn, mode="live").record(
            _evaluate(session_open=False), source_bar_ts=_BAR_OPEN
        )
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (0,)
    conn.close()


def test_source_episode_escalates_to_stop_and_does_not_downgrade(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "escalation.db")
    journal = DecisionJournal(conn, mode="paper")
    episode_id = journal.record_source_refusal(_refusal(action="skip"))
    journal.record_source_refusal(_refusal(action="stop", observed_at=_NOW + timedelta(minutes=1)))
    journal.record_source_refusal(_refusal(action="skip", observed_at=_NOW + timedelta(minutes=2)))
    assert _episodes(conn)[0]["id"] == episode_id
    assert (_episodes(conn)[0]["action"], _episodes(conn)[0]["attempt_count"]) == ("stop", 3)
    conn.close()


def test_source_refusal_write_failure_is_visible_and_never_counts_as_success(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = database.connect(tmp_path / "write_failure.db")
    conn.execute(
        "CREATE TRIGGER reject_episode BEFORE INSERT ON paper_source_episodes "
        "BEGIN SELECT RAISE(ABORT, 'episode unavailable'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="episode unavailable"):
        DecisionJournal(conn, mode="paper").record_source_refusal(_refusal())
    assert _episodes(conn) == []
    assert "source_refusal_journal_failed" in caplog.text
    conn.close()


def test_source_refusal_rejects_raw_exception_text_in_keys(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "safe.db")
    journal = DecisionJournal(conn, mode="paper")
    with pytest.raises(ValueError, match="error_type"):
        journal.record_source_refusal(_refusal(error_type="GatewayError: token=secret"))
    assert _episodes(conn) == []
    conn.close()
