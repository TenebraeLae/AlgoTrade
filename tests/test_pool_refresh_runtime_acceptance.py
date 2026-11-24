"""Independent runtime acceptance through public pool refresh boundaries, without I/O."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest

from tests.test_paper_feed import _candles_at, _session_grid, _share
from trading_bot.api_client import (
    Candle,
    GatewayError,
    InvestGateway,
    PaperShareCatalog,
    ShareMetadata,
)
from trading_bot.cancellation import CancellationToken, OperationCancelled
from trading_bot.pool import LiquidityRules
from trading_bot.pool_refresh import PoolJournal, PoolRefresh, RefreshDependencies, Universe
from trading_bot.utils import MSK, SESSION_CLOSING_AUCTION_START


@dataclass
class _Market:
    """Read-only broker boundary with independently generated regular session pages."""

    catalog: dict[str, ShareMetadata] = field(
        default_factory=lambda: {ticker: _share(ticker) for ticker in ("A", "B", "C")}
    )
    token: CancellationToken = field(default_factory=CancellationToken)
    index_id: str = "verified-imoex-1"
    calls: list[tuple[str, str]] = field(default_factory=list)
    requests: list[tuple[str, datetime, datetime]] = field(default_factory=list)
    fail_page: tuple[str, int] | None = None
    cancel_page: tuple[str, int] | None = None

    def check_cancelled(self) -> None:
        """Use the real irreversible token at the external operation boundary."""
        self.token.check()

    def discover_paper_shares(self, *, pin: bool = True) -> PaperShareCatalog:
        """Return a fresh mutable catalogue without pinning candidate identities."""
        self.token.check()
        assert pin is False, "staging must not mutate the broker's pinned identities"
        self.calls.append(("catalog", ""))
        return PaperShareCatalog(dict(self.catalog), {})

    def resolve_imoex_id(self) -> str:
        """Resolve a separately observable verified index on each attempt."""
        self.token.check()
        self.calls.append(("index", self.index_id))
        return self.index_id

    def iter_candle_pages(
        self, *, instrument_id: str, timeframe: str, from_: datetime, to: datetime
    ) -> Iterator[list[Candle]]:
        """Lazily return two complete pages per requested ordinary session."""
        assert timeframe == "5m"
        self.requests.append((instrument_id, from_, to))
        ticker = next(
            ticker for ticker, share in self.catalog.items() if share.instrument_id == instrument_id
        )
        day = from_.astimezone(MSK).date()
        final_day = to.astimezone(MSK).date()
        page_number = 0
        while day <= final_day:
            if day.weekday() < 5:
                candles = _candles_at(
                    _session_grid(day, end=SESSION_CLOSING_AUCTION_START),
                    start_price=Decimal("100"),
                    step=Decimal("0"),
                    base_volume={"A": 1000, "B": 2000, "C": 3000}[ticker],
                )
                for page in (candles[:80], candles[80:]):
                    self.token.check()
                    page_number += 1
                    self.calls.append(("page", instrument_id))
                    if self.fail_page == (instrument_id, page_number):
                        raise GatewayError("quota exhausted while reading the next page")
                    if self.cancel_page == (instrument_id, page_number):
                        self.token.request_stop()
                    yield page
            day += timedelta(days=1)


@dataclass
class _Runtime:
    """Clock, real journal and externally visible published universe snapshots."""

    clock: list[datetime]
    market: _Market
    connection: sqlite3.Connection
    journal: PoolJournal
    published: list[Universe]
    opens: set[str]
    refresh: PoolRefresh


@pytest.fixture
def runtime() -> Iterator[_Runtime]:
    """Create an isolated in-memory SQL boundary, never the operator's database."""
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE event_log "
        "(ts TEXT, level TEXT, module TEXT, event TEXT, ctx TEXT, notify_vk INTEGER)"
    )
    connection.commit()
    journal = PoolJournal(connection)
    market = _Market()
    clock = [datetime(2026, 10, 16, 12, 3, tzinfo=UTC)]
    published: list[Universe] = []
    opens: set[str] = set()

    def publish(universe: Universe) -> None:
        """Observe publication only after its complete SQL snapshot is durable."""
        assert not connection.in_transaction
        assert journal.generation() == universe.generation
        published.append(universe)

    refresh = PoolRefresh(
        RefreshDependencies(
            gateway=cast(InvestGateway, market),
            journal=journal,
            clock=lambda: clock[0],
            open_tickers=lambda: frozenset(opens),
            publish=publish,
            rules=LiquidityRules(1, Decimal("0.4"), 1, 3),
            holidays=frozenset(),
            workers=1,  # Preserve independent deterministic serial-page acceptance.
        )
    )
    try:
        yield _Runtime(clock, market, connection, journal, published, opens, refresh)
    finally:
        refresh.close()
        connection.close()


def _snapshot(runtime: _Runtime, generation: int = 1) -> Universe:
    now = runtime.clock[0]
    return Universe(
        generation=generation,
        slot=now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0),
        selected=("C",),
        monitoring=tuple(sorted(runtime.opens - {"C"})),
        metadata=dict(runtime.market.catalog),
        index_id=runtime.market.index_id,
    )


def _seed(runtime: _Runtime) -> Universe:
    universe = _snapshot(runtime)
    runtime.refresh.seed(universe, {"A": [100], "B": [200], "C": [300]})
    assert runtime.published == [universe]
    assert runtime.refresh.gate.check(runtime.clock[0]).allowed
    return universe


def _finish(runtime: _Runtime) -> None:
    for _ in range(200):
        runtime.refresh.step()
        if runtime.refresh.gate.reason in {
            "pool_ready",
            "pool_refresh_failed",
        }:
            return
    raise AssertionError("the cooperative attempt did not reach a terminal result")


def _applied_count(runtime: _Runtime) -> int:
    row = runtime.connection.execute(
        "SELECT COUNT(*) FROM event_log WHERE event='pool_refresh_applied'"
    ).fetchone()
    return int(row[0])


def test_clock_rollback_revokes_seeded_permission_permanently(runtime: _Runtime) -> None:
    """Returning to the old wall time or a newer slot cannot resurrect entry permission."""
    old = _seed(runtime)
    original = runtime.clock[0]
    runtime.clock[0] -= timedelta(seconds=1)
    runtime.refresh.check_due()
    assert not runtime.refresh.gate.check(runtime.clock[0]).allowed
    for now in (original, original + timedelta(minutes=15), original + timedelta(days=1)):
        runtime.clock[0] = now
        runtime.refresh.check_due()
        runtime.refresh.step()
        assert not runtime.refresh.gate.check(now).allowed
    assert runtime.refresh.universe is old
    assert runtime.published == [old]
    assert runtime.market.calls == []
    assert _applied_count(runtime) == 1


@pytest.mark.parametrize(
    "start",
    [
        datetime(2026, 10, 16, 23, 46, tzinfo=MSK),
        datetime(2026, 10, 18, 0, 14, 59, tzinfo=MSK),
    ],
    ids=["friday-night", "sunday-midnight"],
)
def test_night_and_weekend_refresh_latest_quarter_hour_without_replay(
    runtime: _Runtime, start: datetime
) -> None:
    """24x7 refresh is independent of the trading window and skips missed slots."""
    runtime.clock[0] = start.astimezone(UTC)
    _seed(runtime)
    runtime.clock[0] = _snapshot(runtime).slot + timedelta(minutes=15)
    runtime.refresh.check_due()
    assert not runtime.refresh.gate.check(runtime.clock[0]).allowed
    _finish(runtime)
    assert runtime.published[-1].generation == 2
    assert runtime.refresh.gate.check(runtime.clock[0]).allowed
    runtime.clock[0] += timedelta(hours=3, minutes=7)
    runtime.refresh.check_due()
    _finish(runtime)
    assert [universe.generation for universe in runtime.published] == [1, 2, 3]
    assert runtime.published[-1].slot == _snapshot(runtime).slot
    assert [kind for kind, _ in runtime.market.calls] == ["catalog", "index"] * 2
    assert runtime.market.requests == []


def test_one_page_per_step_yields_to_ordinary_work_until_complete(runtime: _Runtime) -> None:
    """Even a synchronous fake must not let one step consume two broker pages."""
    runtime.refresh.check_due()
    ordinary_work: list[int] = []
    for heartbeat in range(40):
        before = len(runtime.market.calls)
        runtime.refresh.step()
        assert len(runtime.market.calls) - before <= 1
        ordinary_work.append(heartbeat)
        if runtime.refresh.gate.check(runtime.clock[0]).allowed:
            break
        assert runtime.published == [], "no partial universe may escape between pages"
    assert runtime.refresh.gate.check(runtime.clock[0]).allowed
    assert len(ordinary_work) >= 8
    assert [kind for kind, _ in runtime.market.calls].count("page") == 6
    assert len(runtime.published) == _applied_count(runtime) == 1
    assert runtime.published[0].selected == ("C",)
    assert runtime.published[0].index_id == "verified-imoex-1"


def test_warm_cache_rechecks_catalog_and_index_without_history(runtime: _Runtime) -> None:
    """Cached volumes save history RPCs but cannot freeze membership or index identity."""
    old = _seed(runtime)
    del runtime.market.catalog["C"]
    runtime.market.catalog["B"] = replace(runtime.market.catalog["B"], sector="energy")
    runtime.market.index_id = "verified-imoex-2"
    runtime.clock[0] += timedelta(minutes=15)
    runtime.refresh.check_due()
    _finish(runtime)
    new = runtime.published[-1]
    assert new is not old
    assert new.selected == ("B",)
    assert new.index_id == "verified-imoex-2"
    assert new.metadata["B"].sector == "energy"
    assert runtime.market.calls == [("catalog", ""), ("index", "verified-imoex-2")]
    assert runtime.market.requests == []
    assert runtime.refresh.gate.check(runtime.clock[0]).allowed
    assert _applied_count(runtime) == 2


def test_completed_session_rollover_invalidates_seeded_volume_cache(runtime: _Runtime) -> None:
    """A just-closed Friday must replace Thursday's cached liquidity window."""
    runtime.clock[0] = datetime(2026, 10, 16, 20, 29, tzinfo=UTC)
    _seed(runtime)
    runtime.clock[0] += timedelta(minutes=15)
    runtime.refresh.check_due()
    _finish(runtime)
    assert runtime.refresh.gate.check(runtime.clock[0]).allowed
    assert {instrument_id for instrument_id, _, _ in runtime.market.requests} == {
        "A_ID",
        "B_ID",
        "C_ID",
    }
    assert all(
        from_.astimezone(MSK).date() == to.astimezone(MSK).date() == date(2026, 10, 16)
        for _, from_, to in runtime.market.requests
    )
    assert runtime.published[-1].generation == 2
    assert _applied_count(runtime) == 2


def test_midstream_quota_closes_global_gate_without_partial_pool_and_retries_next_slot(
    runtime: _Runtime,
) -> None:
    """A late candidate's quota failure cannot be mistaken for insufficient liquidity."""
    old = _seed(runtime)
    runtime.clock[0] = datetime(2026, 10, 16, 20, 45, tzinfo=UTC)
    runtime.market.fail_page = ("C_ID", 2)
    runtime.refresh.check_due()
    _finish(runtime)
    assert runtime.market.calls[-1] == ("page", "C_ID")
    assert [kind for kind, _ in runtime.market.calls].count("page") == 6
    assert not runtime.refresh.gate.check(runtime.clock[0]).allowed
    assert runtime.refresh.universe is old
    assert runtime.published == [old]
    assert _applied_count(runtime) == 1
    assert runtime.connection.execute(
        "SELECT COUNT(*) FROM event_log WHERE event='pool_refresh_failed'"
    ).fetchone() == (1,)
    calls = list(runtime.market.calls)
    runtime.market.fail_page = None
    for _ in range(5):
        runtime.refresh.check_due()
        runtime.refresh.step()
    assert runtime.market.calls == calls, "no quota retry storm inside the failed slot"
    runtime.clock[0] += timedelta(minutes=15)
    runtime.refresh.check_due()
    _finish(runtime)
    assert runtime.refresh.gate.check(runtime.clock[0]).allowed
    assert runtime.published[-1].selected == ("C",)
    assert runtime.published[-1].generation == 2
    assert _applied_count(runtime) == 2


@pytest.mark.parametrize("remap", ["instrument_id", "lot_size"])
def test_open_remap_never_replaces_previously_published_monitoring_identity(
    runtime: _Runtime, remap: str
) -> None:
    """Mutable discovery must not redirect an existing open position to another contract."""
    runtime.opens.add("A")
    old = _seed(runtime)
    pinned = old.metadata["A"]
    changed = replace(pinned, sector="changed-sector", min_price_increment=Decimal("0.05"))
    runtime.market.catalog["A"] = (
        replace(changed, instrument_id="replacement-A")
        if remap == "instrument_id"
        else replace(changed, lot_size=pinned.lot_size + 1)
    )
    runtime.clock[0] += timedelta(minutes=15)
    runtime.refresh.check_due()
    _finish(runtime)
    assert not runtime.refresh.gate.check(runtime.clock[0]).allowed
    assert runtime.refresh.universe is old
    assert runtime.refresh.universe.monitoring == ("A",)
    assert runtime.refresh.universe.metadata["A"] == pinned
    assert runtime.journal.recover(frozenset({"A"})) == {"A": pinned}
    assert runtime.published == [old]
    assert _applied_count(runtime) == 1


@pytest.mark.parametrize("entrypoint", ["seed", "step"])
@pytest.mark.parametrize("failure", ["sql", "cancel"])
def test_no_publication_after_sql_failure_or_operator_cancellation(
    runtime: _Runtime, entrypoint: str, failure: str
) -> None:
    """Neither bootstrap seeding nor staged refresh may bypass durable apply or stop."""
    old = _seed(runtime)
    runtime.clock[0] += timedelta(minutes=15)
    runtime.refresh.check_due()
    if failure == "sql":
        runtime.connection.execute(
            "CREATE TRIGGER reject_pool_apply BEFORE INSERT ON event_log "
            "WHEN NEW.event='pool_refresh_applied' "
            "BEGIN SELECT RAISE(ABORT, 'pool persistence unavailable'); END"
        )
        runtime.connection.commit()
    elif entrypoint == "seed":
        runtime.market.token.request_stop()
    else:
        # Make one identity miss the warm cache so cancellation arrives inside a page RPC.
        runtime.market.catalog["A"] = replace(runtime.market.catalog["A"], lot_size=11)
        runtime.market.cancel_page = ("A_ID", 1)
    try:
        if entrypoint == "seed":
            runtime.refresh.seed(_snapshot(runtime, 2), {"A": [100], "B": [200], "C": [300]})
        else:
            _finish(runtime)
    except sqlite3.IntegrityError as error:
        assert failure == "sql"
        assert "pool persistence unavailable" in str(error)
    except OperationCancelled:
        assert failure == "cancel"
    assert runtime.published == [old]
    assert runtime.refresh.universe is old
    assert not runtime.refresh.gate.check(runtime.clock[0]).allowed
    assert _applied_count(runtime) == 1
    assert not runtime.connection.in_transaction
    if failure == "cancel":
        calls = list(runtime.market.calls)
        with pytest.raises(OperationCancelled):
            runtime.refresh.step()
        assert runtime.market.calls == calls
        assert runtime.connection.execute(
            "SELECT COUNT(*) FROM event_log WHERE event='pool_refresh_failed'"
        ).fetchone() == (0,), "operator stop is not a quota/source failure"
