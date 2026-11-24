"""Scheduled local regime integration without network or working databases."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import time, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from tests.test_paper_cycle_integration import _cycle, _inputs, _tick
from tests.test_paper_feed import (
    _YESTERDAY,
    FakeGateway,
    _account_source,
    _candles_at,
    _classifier,
    _db,
    _feed,
    _populate_ticker,
    _session_grid,
)
from tests.test_regime_schedule import _at, _observation
from tests.test_strategy import _config
from trading_bot.executor import PaperTick
from trading_bot.market_regime import RegimeBar
from trading_bot.portfolio import Portfolio
from trading_bot.regime_schedule import LocalRegimeSchedule


def test_paper_cycle_journals_scheduled_local_chaos_and_unavailable(tmp_path: Path) -> None:
    for missing in (False, True):
        conn = _db(tmp_path, f"{missing}.db")
        inputs = _inputs()
        schedule = LocalRegimeSchedule(_config())
        context = schedule.update(
            "SBER", inputs.now, None if missing else _observation(inputs.now, chaos=True)
        )
        inputs = replace(inputs, local_regime=context)
        result = _cycle(conn).run_tick(_tick(inputs=inputs))
        assert result.decision is not None
        assert result.decision.verdict == "no_signal"
        assert result.fill is None
        expected = "local_regime_unavailable" if missing else "regime_chaos"
        assert expected in result.decision.filters_failed
        row = conn.execute("SELECT indicators FROM decisions").fetchone()
        snapshot = json.loads(row[0])
        assert snapshot["local_regime_slot"] == context.slot.isoformat()
        assert snapshot["local_regime_source"] == "scheduled"
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
        conn.close()


def test_expired_wrong_ticker_and_stale_slot_context_cannot_authorize(tmp_path: Path) -> None:
    inputs = _inputs()
    schedule = LocalRegimeSchedule(_config())
    good = schedule.update("SBER", inputs.now, _observation(inputs.now))
    contexts = (
        replace(good, valid_until=inputs.now),
        replace(good, ticker="GAZP"),
        replace(good, slot=good.slot - timedelta(minutes=30)),
    )
    for i, context in enumerate(contexts):
        conn = _db(tmp_path, f"expired-{i}.db")
        result = _cycle(conn).run_tick(_tick(inputs=replace(inputs, local_regime=context)))
        assert "local_regime_unavailable" in result.decision.filters_failed
        assert result.fill is None
        conn.close()


def test_heartbeat_pre_session_two_tickers_without_5m_or_quote(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    gw = FakeGateway()
    for ticker in ("SBER", "GAZP"):
        share = _populate_ticker(gw, ticker)
        # Previous session's last fully closed 15m candle is 23:15 Moscow.
        gw.candles[(share.instrument_id, "15m")] = _candles_at(
            _session_grid(_YESTERDAY, end=time(23, 40), minutes=15),
            start_price=Decimal(100),
            step=Decimal(1),
        )
        gw.candles[(share.instrument_id, "5m")] = []
    gw.quotes.clear()
    feed = _feed(gw, _account_source(conn), approved=["SBER", "GAZP"], clock=_at(9, 50))
    assert feed.poll_regimes() is None
    for ticker in ("SBER", "GAZP"):
        context = feed.local_regime(ticker, _at(9, 50))
        assert context.regime == "trending"
        assert context.slot == _at(9, 50)
    assert feed.next_paper_tick() is None
    assert gw.status_calls == []
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    conn.close()


def test_heartbeat_failure_revokes_cached_permission_even_in_same_bucket(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    feed = _feed(gw, _account_source(conn), approved=["SBER"])
    with patch.object(gw, "get_candles_history", wraps=gw.get_candles_history) as history:
        feed.poll_regimes()
        first_count = history.call_count
        feed.poll_regimes()
        assert history.call_count == first_count
    now = _at(22, 5)
    assert feed.local_regime("SBER", now).regime == "trending"
    gw.fail_candles.add("SBER_ID")
    # A real candidate history read fails, even though heartbeat bucket is unchanged.
    assert feed.next_paper_tick() is not None
    assert feed.local_regime("SBER", now).regime is None
    conn.close()


def test_local_cache_does_not_cache_global_index_permission(tmp_path: Path) -> None:
    conn = _db(tmp_path)
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    gw.candles[("IMOEX_ID", "15m")] = gw.candles[("SBER_ID", "15m")]
    classifier = _classifier()
    feed = _feed(gw, _account_source(conn), approved=["SBER"], classifier=classifier)
    with patch.object(
        classifier, "evaluate", return_value=RegimeBar(Decimal("0.2"), Decimal("0.5"), "normal")
    ):
        feed.poll_regimes()
        first = feed.next_paper_tick()
        assert isinstance(first, PaperTick)
        assert first.strategy.market_filter is not None
        assert not first.strategy.market_filter.paused
        assert first.strategy.local_regime.regime == "trending"
        gw.fail_candles.add("IMOEX_ID")
        # Same local slot and even same 5m bar: index outage must be observed again.
        second = feed.next_paper_tick()
        assert isinstance(second, PaperTick)
        assert second.strategy.market_filter.paused
        assert second.strategy.market_filter.market_regime == "chaos"
        assert second.strategy.local_regime.regime == "trending"
    conn.close()


def test_regime_changes_and_timer_leave_existing_position_and_risk_unchanged(
    tmp_path: Path,
) -> None:
    conn = _db(tmp_path)
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal(100),
        fee=Decimal(0),
        slippage=Decimal(0),
        opened_at=_at(12),
        stop=Decimal(90),
        take=Decimal(120),
    )
    before = conn.execute("SELECT * FROM positions").fetchall()
    config = _config()
    schedule = LocalRegimeSchedule(config)
    schedule.update("SBER", _at(12), _observation(_at(12)))
    schedule.update("SBER", _at(12, 30), _observation(_at(12, 30), chaos=True))
    schedule.update("SBER", _at(13), None)
    assert conn.execute("SELECT * FROM positions").fetchall() == before
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    assert config == _config()
    conn.close()
