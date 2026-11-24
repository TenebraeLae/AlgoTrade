"""Liquidity workers read only; owner polls and publishes complete histories."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event, Lock, get_ident
from time import monotonic, sleep

import pytest

from tests.test_paper_feed import _candles_at, _session_grid
from tests.test_pool_refresh import finish, setup_refresh
from trading_bot.api_client import Candle
from trading_bot.config import Settings
from trading_bot.pool_refresh import completed_dates
from trading_bot.utils import SESSION_CLOSING_AUCTION_START


def test_refresh_history_progresses_between_owner_heartbeats(tmp_path: Path) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    refresh.deps = replace(refresh.deps, workers=4)
    try:
        refresh.step()  # catalog
        refresh.step()  # index
        refresh.step()  # dispatch at most four candidate histories
        deadline = monotonic() + 1
        while gateway.pages < 6 and monotonic() < deadline:
            sleep(0.001)  # No owner poll: page throughput must not be gated by heartbeat5s.
        assert gateway.pages == 6
        assert not published
        assert not refresh.gate.check(now[0]).allowed
        finish(refresh)
        assert published[-1].selected == ("C",)
    finally:
        refresh.close()
        conn.close()


def test_default_liquidity_workers_is_four() -> None:
    assert Settings(_env_file=None).liquidity_workers == 4


def test_parallel_refresh_failure_keeps_prior_pool_and_waits_for_next_slot(tmp_path: Path) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    refresh.deps = replace(refresh.deps, workers=4)
    try:
        finish(refresh)
        old = published[-1]
        gateway.catalog["A"] = replace(gateway.catalog["A"], lot_size=20)
        gateway.error = True
        now[0] += timedelta(minutes=15)
        finish(refresh)
        assert refresh.universe is old
        assert published == [old]
        assert not refresh.gate.check(now[0]).allowed
        assert (
            conn.execute(
                "select count(*) from event_log where event='pool_refresh_failed'"
            ).fetchone()[0]
            == 1
        )
        before = gateway.pages
        gateway.error = False
        for _ in range(5):
            refresh.step()
        assert gateway.pages == before
        now[0] += timedelta(minutes=15)
        finish(refresh)
        assert len(published) == 2
    finally:
        refresh.close()
        conn.close()


def test_expired_inflight_epoch_is_drained_without_partial_or_extra_workers(tmp_path: Path) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    release, entered = Event(), Event()
    original = gateway.iter_candle_pages
    lock = Lock()
    active = peak = 0

    def pages(**kwargs: object) -> Iterator[list[Candle]]:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        entered.set()
        try:
            assert release.wait(2)
            yield from original(**kwargs)
        finally:
            with lock:
                active -= 1

    gateway.iter_candle_pages = pages
    refresh.deps = replace(refresh.deps, workers=4)
    try:
        refresh.step()
        refresh.step()
        refresh.step()
        assert entered.wait(1)
        now[0] += timedelta(minutes=15)
        for _ in range(4):
            refresh.step()
        assert not published
        assert not refresh.gate.check(now[0]).allowed
        release.set()
        finish(refresh)
        assert len(published) == 1
        assert published[0].slot == now[0].replace(minute=15, second=0, microsecond=0)
        assert peak <= 4
    finally:
        release.set()
        refresh.close()
        conn.close()


def test_refresh_poll_returns_while_rpc_inflight_and_never_touches_sqlite(tmp_path: Path) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    release = Event()
    entered = Event()
    owner = get_ident()
    workers: set[int] = set()
    original = gateway.iter_candle_pages

    def pages(**kwargs: object) -> Iterator[list[Candle]]:
        workers.add(get_ident())
        entered.set()
        assert release.wait(2)
        yield from original(**kwargs)

    gateway.iter_candle_pages = pages
    refresh.deps = replace(refresh.deps, workers=4)
    try:
        refresh.step()  # catalog
        refresh.step()  # index
        before = monotonic()
        refresh.step()  # submit, not block waiting for slow RPC
        assert monotonic() - before < 0.5
        assert entered.wait(1)
        assert not published
        assert not refresh.gate.check(now[0]).allowed
        assert workers and owner not in workers
        assert conn.execute("select count(*) from event_log").fetchone()[0] == 0
    finally:
        release.set()
        refresh.close()
        conn.close()


@pytest.mark.parametrize("sessions", [1, 10])
def test_incomplete_history_is_reloaded_next_slot(tmp_path: Path, sessions: int) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    calls: list[str] = []

    def pages(**kwargs: object) -> Iterator[list[Candle]]:
        instrument_id = str(kwargs["instrument_id"])
        calls.append(instrument_id)
        end = kwargs["to"]
        assert isinstance(end, datetime)
        dates = completed_dates(end, sessions, frozenset())
        for day in dates:
            if instrument_id.startswith("A") and day == dates[-1]:
                continue  # Nine complete sessions do not become a completed ten-session cache.
            yield _candles_at(
                _session_grid(day, end=SESSION_CLOSING_AUCTION_START),
                start_price=Decimal(100),
                step=Decimal(0),
                base_volume={"A": 1, "B": 2, "C": 3}[instrument_id[0]],
            )

    gateway.iter_candle_pages = pages
    refresh.deps = replace(
        refresh.deps, workers=4, rules=replace(refresh.deps.rules, sessions=sessions)
    )
    try:
        for epoch in range(2):
            for _ in range(500):
                refresh.step()
                if len(published) > epoch:
                    break
                sleep(0.001)
            assert len(published) == epoch + 1
            now[0] += timedelta(minutes=15)
        assert sum(x.startswith("A") for x in calls) == 2
        assert sum(x.startswith("B") for x in calls) == 1
    finally:
        refresh.close()
        conn.close()
