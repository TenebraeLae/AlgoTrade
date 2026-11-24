"""Pool removal/re-addition and a real SQLite restart must never replay a fill."""

from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_paper_cycle_integration import _NOW, _cycle, _tick
from tests.test_paper_feed import FakeGateway, _account_source, _feed, _populate_ticker
from trading_bot import database
from trading_bot.executor import PaperRecoveryRequired, PaperTick
from trading_bot.pool_refresh import PoolGate, PoolJournal, Universe, utc_slot


def test_fill_remove_readd_restart_uses_durable_bar_claim_not_a_second_fill(tmp_path: Path) -> None:
    path = tmp_path / "restart_dedup.db"
    gateway = FakeGateway()
    share = _populate_ticker(gateway, "SBER", now=_NOW)
    initial = Universe(1, utc_slot(_NOW), ("SBER",), (), {"SBER": share}, "IMOEX")
    conn = database.connect(path)
    try:
        journal = PoolJournal(conn)
        journal.applied(initial, _NOW, {})
        # Real bootstrap initializes anchors before there is any trade history.
        _account_source(conn).snapshot(_NOW, {}, {}, {})
        first = _cycle(conn).run_tick(_tick())
        assert first.fill is not None and first.trade_id is not None
        feed = _feed(gateway, _account_source(conn), approved=["SBER"], clock=_NOW)
        gate = PoolGate()
        feed.set_pool_gate(gate)
        feed.apply_universe(initial)
        gate.publish(initial.slot, 1)
        # A position removed from selected remains monitoring-only with pinned ID.
        removed = replace(initial, generation=2, selected=(), monitoring=("SBER",))
        journal.applied(removed, _NOW, {})
        feed.apply_universe(removed)
        gate.publish(removed.slot, 2)
        monitoring_tick = feed.next_paper_tick()
        assert isinstance(monitoring_tick, PaperTick)
        assert monitoring_tick.strategy.market_filter is not None
        assert monitoring_tick.strategy.market_filter.paused
        repeated = _cycle(conn).run_tick(monitoring_tick)
        assert repeated.already_processed and repeated.decision_id == first.decision_id
        feed.ack(monitoring_tick)
        readded = replace(initial, generation=3)
        journal.applied(readded, _NOW, {})
        feed.apply_universe(readded)
        gate.publish(readded.slot, 3)
        assert feed.next_paper_tick() is None
        ledger = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        positions = conn.execute("SELECT * FROM positions ORDER BY ticker").fetchall()
        balance = conn.execute("SELECT mode, balance FROM portfolio_balances").fetchall()
        assert len(ledger) == 1
    finally:
        conn.close()

    conn = database.connect(path)
    try:
        pinned = PoolJournal(conn).recover(frozenset({"SBER"}))
        assert pinned == {"SBER": share}
        restarted = _feed(gateway, _account_source(conn), approved=["SBER"], clock=_NOW)
        restarted.apply_universe(readded)
        restarted_gate = PoolGate()
        restarted.set_pool_gate(restarted_gate)
        restarted_gate.publish(readded.slot, 3)
        offered = restarted.next_paper_tick()
        assert isinstance(offered, PaperTick)  # in-memory ack is intentionally cold
        repeated = _cycle(conn).run_tick(offered)
        assert repeated.already_processed and repeated.decision_id == first.decision_id
        assert repeated.fill is None and repeated.trade_id is None
        restarted.ack(offered)
        assert restarted.next_paper_tick() is None
        assert conn.execute("SELECT * FROM trades ORDER BY id").fetchall() == ledger
        assert conn.execute("SELECT * FROM positions ORDER BY ticker").fetchall() == positions
        assert conn.execute("SELECT mode, balance FROM portfolio_balances").fetchall() == balance
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (1,)
        # Refresh/restart never bypasses an existing unresolved-claim barrier.
        conn.execute("UPDATE decisions SET processing_state='failed'")
        conn.commit()
        with pytest.raises(PaperRecoveryRequired):
            _cycle(conn)
        assert conn.execute("SELECT * FROM trades ORDER BY id").fetchall() == ledger
    finally:
        conn.close()
