"""The same long-lived feed owns acknowledgements and immutable active identities."""

from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from tests.test_paper_feed import _NOW, FakeGateway, _account_source, _db, _feed, _populate_ticker
from trading_bot.executor import PaperTick
from trading_bot.pool_refresh import PoolGate, Universe, utc_slot


def test_atomic_universe_pinned_metadata_and_ack_survive_remove_readd(tmp_path: Path) -> None:
    gateway = FakeGateway()
    _populate_ticker(gateway, "SBER")
    conn = _db(tmp_path)
    feed = _feed(gateway, _account_source(conn), approved=["SBER"])
    share = gateway.shares["SBER"]
    gate = PoolGate()
    universe = Universe(1, utc_slot(_NOW), ("SBER",), (), {"SBER": share}, "IMOEX")
    feed.set_pool_gate(gate)
    feed.apply_universe(universe)
    gate.publish(universe.slot, 1)
    gateway.shares["SBER"] = replace(share, instrument_id="MUTATED")
    tick = feed.next_paper_tick()
    assert isinstance(tick, PaperTick)
    assert "generation=1" in tick.strategy.market_filter.reason
    assert "MUTATED" not in gateway.status_calls
    feed.ack(tick)
    feed.apply_universe(Universe(2, universe.slot, (), (), {}, "IMOEX"))
    feed.apply_universe(replace(universe, generation=3))
    gate.publish(universe.slot, 3)
    assert feed.next_paper_tick() is None
    gate.block("pool_refresh_in_progress")
    assert feed.next_paper_tick() is None
    conn.close()


def test_feed_independently_revokes_stale_wall_slot(tmp_path: Path) -> None:
    gateway = FakeGateway()
    _populate_ticker(gateway, "SBER")
    conn = _db(tmp_path)
    feed = _feed(gateway, _account_source(conn), approved=["SBER"])
    gate = PoolGate()
    gate.publish(utc_slot(_NOW), 1)
    feed.set_pool_gate(gate)
    feed.apply_universe(Universe(1, utc_slot(_NOW), ("SBER",), (), dict(gateway.shares), "IMOEX"))
    feed._clock = lambda: _NOW + timedelta(minutes=15)
    assert feed.next_paper_tick() is None
    assert not gateway.status_calls
    conn.close()


def test_main_checks_due_before_feed_and_steps_after_ack(tmp_path: Path) -> None:
    from tests.test_main import _settings
    from tests.test_paper_cycle_integration import _tick
    from trading_bot.main import RunDependencies, run

    calls = []

    class Cycle:
        def run_tick(self, tick: PaperTick) -> None:
            calls.append("cycle")

        def resolve_source_refusals(self, ticker: str, now: datetime) -> None:
            calls.append("resolve")

    done = [False]

    def feed() -> PaperTick:
        calls.append("feed")
        return _tick()

    def sleep(seconds: float) -> None:
        done[0] = True

    run(
        _settings(),
        heartbeat_path=tmp_path / "heartbeat",
        dependencies=RunDependencies(
            paper_cycle=Cycle(),
            next_paper_tick=feed,
            ack_paper_tick=lambda tick: calls.append("ack"),
            check_pool_due=lambda: calls.append("due"),
            refresh_pool_step=lambda: calls.append("step"),
            sleep=sleep,
            should_stop=lambda: done[0],
        ),
    )
    assert calls == ["due", "feed", "cycle", "resolve", "ack", "step"]


def test_long_bootstrap_revalidates_catalog_index_without_reloading_compatible_volumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC
    from decimal import Decimal
    from unittest.mock import Mock

    from tests.test_auto_bootstrap import _gateway, _index, _share
    from trading_bot import database
    from trading_bot.config import Settings
    from trading_bot.main import _paper_dependencies
    from trading_bot.paper_feed import PaperTickFeed

    now = [datetime(2026, 10, 2, 12, 3, tzinfo=UTC)]
    tickers = tuple(f"TEST{i:02d}" for i in range(30))
    gateway = _gateway([_share(t) for t in tickers], [_index()])
    discover = Mock(wraps=gateway.discover_paper_shares)
    index = Mock(wraps=gateway.resolve_imoex_id)
    monkeypatch.setattr(gateway, "discover_paper_shares", discover)
    monkeypatch.setattr(gateway, "resolve_imoex_id", index)
    history_calls = []

    def history(
        self: PaperTickFeed, candidates: tuple[str, ...], *, sessions: int
    ) -> dict[str, list[int]]:
        history_calls.append(candidates)
        now[0] += timedelta(minutes=15)
        return {t: [i + 1] * sessions for i, t in enumerate(candidates)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return now[0]

    monkeypatch.setattr("trading_bot.main.datetime", Clock)
    monkeypatch.setattr(PaperTickFeed, "daily_volume_lots", history)
    conn = database.connect(tmp_path / "bootstrap.db")
    deps = _paper_dependencies(
        Settings(paper_starting_balance=Decimal(100000), _env_file=None), conn=conn, gateway=gateway
    )
    assert deps.next_paper_tick is not None
    assert discover.call_count == index.call_count == 2
    assert len(history_calls) == 1
    assert deps.next_paper_tick.__self__._universe.slot == utc_slot(now[0])
    conn.close()


@pytest.mark.parametrize("change", ["remap", "remove", "legacy"])
def test_restart_never_guesses_open_identity_from_new_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    from datetime import UTC
    from decimal import Decimal

    from tests.test_auto_bootstrap import _gateway, _index, _share
    from trading_bot import database
    from trading_bot.config import Settings
    from trading_bot.main import _paper_dependencies
    from trading_bot.paper_feed import PaperTickFeed
    from trading_bot.pool_refresh import PoolJournal
    from trading_bot.portfolio import Portfolio

    now = datetime(2026, 10, 2, 12, 3, tzinfo=UTC)
    tickers = tuple(f"TEST{i:02d}" for i in range(30))
    original = _gateway([_share("TEST00")], [_index()])
    original.discover_paper_shares()
    pinned = original.get_share_metadata(ticker="TEST00")
    conn = database.connect(tmp_path / "restart.db")
    if change != "legacy":
        PoolJournal(conn).applied(
            Universe(1, utc_slot(now), ("TEST00",), (), {"TEST00": pinned}, "old-index"), now, {}
        )
    Portfolio(conn, mode="paper", starting_balance=Decimal(100000)).open_position(
        ticker="TEST00",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal(100),
        fee=Decimal(0),
        slippage=Decimal(0),
        opened_at=now,
        stop=Decimal(90),
    )
    catalog = [
        _share(t, figi="replacement") if t == "TEST00" and change == "remap" else _share(t)
        for t in tickers
        if not (t == "TEST00" and change == "remove")
    ]
    gateway = _gateway(catalog, [_index()])

    class Clock(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:
            return now

    def volumes(
        self: PaperTickFeed, candidates: tuple[str, ...], *, sessions: int
    ) -> dict[str, list[int]]:
        return {t: [i + 1] * sessions for i, t in enumerate(candidates)}

    monkeypatch.setattr("trading_bot.main.datetime", Clock)
    monkeypatch.setattr(PaperTickFeed, "daily_volume_lots", volumes)
    settings = Settings(paper_starting_balance=Decimal(100000), _env_file=None)
    if change == "legacy":
        with pytest.raises(ValueError, match="reconciliation"):
            _paper_dependencies(settings, conn=conn, gateway=gateway)
    else:
        deps = _paper_dependencies(settings, conn=conn, gateway=gateway)
        feed = deps.next_paper_tick.__self__
        assert feed._pinned_metadata["TEST00"] == pinned
        assert not feed._pool_gate.check(now).allowed
        assert PoolJournal(conn).recover(frozenset({"TEST00"})) == {"TEST00": pinned}
    assert conn.execute("SELECT count(*) FROM trades WHERE ts_close IS NULL").fetchone()[0] == 1
    conn.close()
