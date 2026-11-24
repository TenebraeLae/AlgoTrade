"""24x7 pool epochs fail closed independently of the trading calendar."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event
from time import sleep

import pytest

from tests.test_paper_feed import _candles_at, _db, _session_grid, _share
from trading_bot.api_client import Candle, GatewayError, PaperShareCatalog
from trading_bot.pool import LiquidityRules
from trading_bot.pool_refresh import (
    PoolGate,
    PoolJournal,
    PoolRefresh,
    RefreshDependencies,
    Universe,
    completed_dates,
    utc_slot,
)
from trading_bot.utils import SESSION_CLOSING_AUCTION_START


class Gateway:
    def __init__(self) -> None:
        self.catalog = {name: _share(name) for name in ("A", "B", "C")}
        self.catalog_calls = self.index_calls = self.pages = 0
        self.error = False
        self.cancel = False

    def check_cancelled(self) -> None:
        from trading_bot.cancellation import OperationCancelled

        if self.cancel:
            raise OperationCancelled()

    @contextmanager
    def history_worker(self, stop: Event) -> Iterator[None]:
        self.check_cancelled()
        yield
        self.check_cancelled()

    def discover_paper_shares(self, *, pin: bool = True) -> PaperShareCatalog:
        assert not pin
        self.catalog_calls += 1
        return PaperShareCatalog(dict(self.catalog), {})

    def resolve_imoex_id(self) -> str:
        self.index_calls += 1
        return "IMOEX"

    def get_candles_history(
        self, *, instrument_id: str, timeframe: str, from_: datetime, to: datetime
    ) -> list[Candle]:
        return [
            candle
            for page in self.iter_candle_pages(
                instrument_id=instrument_id, timeframe=timeframe, from_=from_, to=to
            )
            for candle in page
        ]

    def iter_candle_pages(
        self, *, instrument_id: str, timeframe: str, from_: datetime, to: datetime
    ) -> Iterator[list[Candle]]:
        self.pages += 1
        if self.error:
            raise GatewayError("quota")
        day = completed_dates(to, 1, frozenset())[0]
        candles = _candles_at(
            _session_grid(day, end=SESSION_CLOSING_AUCTION_START),
            start_price=Decimal(100),
            step=Decimal(0),
            base_volume={"A": 1, "B": 2, "C": 3}[instrument_id[0]],
        )
        yield candles[:80]
        self.pages += 1
        yield candles[80:]


def setup_refresh(
    tmp_path: Path,
) -> tuple[list[datetime], Gateway, sqlite3.Connection, list[Universe], PoolRefresh]:
    now = [datetime(2026, 10, 2, 12, 3, tzinfo=UTC)]
    gateway = Gateway()
    conn = _db(tmp_path)
    published: list[Universe] = []
    deps = RefreshDependencies(
        gateway=gateway,
        journal=PoolJournal(conn),
        clock=lambda: now[0],
        open_tickers=lambda: frozenset(),
        publish=published.append,
        rules=LiquidityRules(1, Decimal(".4"), 1, 3),
        holidays=frozenset(),
        workers=1,  # Existing deterministic one-page fixtures exercise the serial mode.
    )
    refresh = PoolRefresh(deps)
    return now, gateway, conn, published, refresh


def finish(refresh: PoolRefresh) -> None:
    for _ in range(500):
        refresh.step()
        if refresh.gate.reason in ("pool_ready", "pool_refresh_failed"):
            return
        sleep(0.001)
    raise AssertionError("refresh did not finish")


def test_streaming_warm_cache_and_catalog_index_refresh(tmp_path: Path) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    refresh.check_due()
    assert not refresh.gate.check(now[0]).allowed
    for _ in range(3):
        before = gateway.pages
        refresh.step()
        assert gateway.pages - before <= 1
    assert not published
    finish(refresh)
    assert published[-1].selected == ("C",)
    first_pages = gateway.pages
    now[0] += timedelta(minutes=15)
    refresh.check_due()
    finish(refresh)
    assert gateway.pages == first_pages
    assert gateway.catalog_calls == gateway.index_calls == 2
    assert published[-1].generation == 2
    assert (
        conn.execute(
            "SELECT count(*) FROM event_log WHERE event='pool_refresh_applied'"
        ).fetchone()[0]
        == 2
    )
    conn.close()


def test_gateway_failure_no_partial_pool_and_retry_only_next_slot(tmp_path: Path) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    gateway.error = True
    refresh.check_due()
    finish(refresh)
    assert not published
    assert not refresh.gate.check(now[0]).allowed
    gateway.error = False
    refresh.check_due()
    refresh.step()
    assert gateway.catalog_calls == 1
    now[0] += timedelta(minutes=15)
    refresh.check_due()
    finish(refresh)
    assert published
    conn.close()


def test_unknown_calendar_does_not_become_trade_permission(tmp_path: Path) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    now[0] = datetime(2099, 10, 2, tzinfo=UTC)
    refresh.check_due()
    finish(refresh)
    assert not published
    assert not refresh.gate.check(now[0]).allowed
    conn.close()


def test_wall_slots_include_weekends_midnight_and_late_start() -> None:
    now = datetime(2026, 10, 18, 0, 14, 59, tzinfo=UTC)
    assert utc_slot(now) == now.replace(minute=0, second=0)
    assert utc_slot(now + timedelta(seconds=1)).minute == 15
    gate = PoolGate()
    assert not gate.check(now).allowed
    gate.publish(utc_slot(now), 1)
    assert gate.check(now).allowed
    assert not gate.check(now + timedelta(seconds=1)).allowed


def test_clock_rollback_cannot_resurrect_permission() -> None:
    now = datetime(2026, 10, 17, 12, 3, tzinfo=UTC)
    gate = PoolGate()
    gate.publish(utc_slot(now), 1)
    assert gate.check(now).allowed
    assert not gate.check(now - timedelta(seconds=1)).allowed
    assert not gate.check(now).allowed


@pytest.mark.parametrize("change", ["id", "lot", "dates", "calendar"])
def test_completed_volume_cache_key_invalidation(tmp_path: Path, change: str) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    finish(refresh)
    before = gateway.pages
    now[0] += timedelta(minutes=15)
    if change == "id":
        gateway.catalog["C"] = replace(gateway.catalog["C"], instrument_id="C_NEW")
    elif change == "lot":
        gateway.catalog["C"] = replace(gateway.catalog["C"], lot_size=20)
    elif change == "dates":
        now[0] += timedelta(days=1)
    else:
        refresh.deps = replace(refresh.deps, holidays=frozenset({date(2026, 1, 1)}))
    refresh.check_due()
    finish(refresh)
    assert gateway.pages > before
    assert len(published) == 2
    conn.close()


@pytest.mark.parametrize("change", ["remap", "remove"])
def test_open_identity_conflict_keeps_old_epoch_and_blocks_every_entry(
    tmp_path: Path, change: str
) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    finish(refresh)
    old = published[-1]
    refresh.deps = replace(refresh.deps, open_tickers=lambda: frozenset({"C"}))
    now[0] += timedelta(minutes=15)
    if change == "remap":
        gateway.catalog["C"] = replace(gateway.catalog["C"], instrument_id="C_NEW")
    else:
        del gateway.catalog["C"]
    refresh.check_due()
    finish(refresh)
    assert published == [old]
    assert refresh.universe.metadata["C"].instrument_id == "C_ID"
    assert not refresh.gate.check(now[0]).allowed
    assert (
        conn.execute("SELECT count(*) FROM event_log WHERE event='pool_refresh_failed'").fetchone()[
            0
        ]
        == 1
    )
    conn.close()


def test_legacy_open_without_journal_is_not_guessed(tmp_path: Path) -> None:
    _, _, conn, _, _ = setup_refresh(tmp_path)
    with pytest.raises(ValueError, match="reconciliation"):
        PoolJournal(conn).recover(frozenset({"C"}))
    conn.close()


def test_cancel_and_sql_failure_never_publish(tmp_path: Path) -> None:
    from trading_bot.cancellation import OperationCancelled

    _, gateway, conn, published, refresh = setup_refresh(tmp_path)
    gateway.cancel = True
    with pytest.raises(OperationCancelled):
        refresh.step()
    assert not published
    assert conn.execute("SELECT count(*) FROM event_log").fetchone()[0] == 0
    gateway.cancel = False
    conn.execute(
        "CREATE TRIGGER reject_pool BEFORE INSERT ON event_log "
        "BEGIN SELECT RAISE(ABORT, 'rejected'); END"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        finish(refresh)
    assert not published
    assert not refresh.gate.check(refresh.deps.clock()).allowed
    conn.close()


def test_long_attempt_is_coalesced_without_applying_expired_slot(tmp_path: Path) -> None:
    now, gateway, conn, published, refresh = setup_refresh(tmp_path)
    refresh.step()  # catalog
    refresh.step()  # index
    refresh.step()  # first history page
    now[0] += timedelta(minutes=45)
    refresh.step()  # latest catalog, not another old history page
    assert gateway.catalog_calls == 2
    assert not published
    finish(refresh)
    assert len(published) == 1
    assert published[0].slot == utc_slot(now[0])
    conn.close()
