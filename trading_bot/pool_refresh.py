"""Cooperative, journal-first paper universe refresh (L4).

Allowed: read market data, stage immutable epochs and persist pool-specific audits.
Forbidden: mutate positions, trades, risk or claims; workers may only read history.
SQLite, staging, cache validation and publication stay on the owner thread.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import partial
from types import MappingProxyType

from trading_bot.api_client import Candle, GatewayError, InvestGateway, ShareMetadata
from trading_bot.config import DEFAULT_POOL_REFRESH_MIN
from trading_bot.history_workers import HistoryWorkers
from trading_bot.pool import LiquidityRules, PoolRecommendation, recommend_pool
from trading_bot.utils import (
    MSK,
    SESSION_CLOSING_AUCTION_START,
    SESSION_TRADING_START,
    session_calendar_decision,
)

logger = logging.getLogger(__name__)
_VERSION = 1
_LATEST_SNAPSHOT_SQL = (
    "SELECT ctx FROM event_log WHERE module='pool_refresh' "
    "AND event='pool_refresh_applied' ORDER BY rowid DESC LIMIT 1"
)


def utc_slot(now: datetime) -> datetime:
    """Floor an aware wall clock to a round-the-clock UTC quarter hour."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("pool clock must be timezone-aware")
    value = now.astimezone(UTC)
    return value.replace(
        minute=value.minute // DEFAULT_POOL_REFRESH_MIN * DEFAULT_POOL_REFRESH_MIN,
        second=0,
        microsecond=0,
    )


@dataclass(frozen=True)
class PoolPermission:
    """Auditable current entry permission, never permission to trade by itself."""

    allowed: bool
    generation: int
    slot: datetime
    reason: str


class PoolGate:
    """Recheck wall-slot at every use; rollback permanently fails closed."""

    def __init__(self) -> None:
        """Start without entry permission or any trusted wall-clock observation."""
        self.slot: datetime | None = None
        self.generation = 0
        self.reason = "pool_refresh_due"
        self._last: datetime | None = None
        self._rollback = False

    def check(self, now: datetime) -> PoolPermission:
        """Read permission while detecting backwards time, including within a slot."""
        current = utc_slot(now)
        if self._last is not None and now < self._last:
            self._rollback = True
        self._last = max(now, self._last) if self._last else now
        reason = "pool_clock_rollback" if self._rollback else self.reason
        allowed = not self._rollback and self.slot == current and reason == "pool_ready"
        if not self._rollback and self.slot != current and reason == "pool_ready":
            reason = "pool_refresh_due"
        return PoolPermission(allowed, self.generation, current, reason)

    def block(self, reason: str) -> None:
        """Revoke new entries without changing an existing universe."""
        self.reason = reason

    def publish(self, slot: datetime, generation: int) -> None:
        """Grant only the successfully committed epoch's slot."""
        self.slot, self.generation, self.reason = slot, generation, "pool_ready"


@dataclass(frozen=True)
class Universe:
    """One indivisible pool, monitoring identities and index generation."""

    generation: int
    slot: datetime
    selected: tuple[str, ...]
    monitoring: tuple[str, ...]
    metadata: Mapping[str, ShareMetadata]
    index_id: str

    def __post_init__(self) -> None:
        if (
            type(self.generation) is not int
            or self.generation < 1
            or utc_slot(self.slot) != self.slot
        ):
            raise ValueError("invalid pool epoch")
        if not self.index_id.strip():
            raise ValueError("empty pool index identity")
        active = (*self.selected, *self.monitoring)
        if len(set(active)) != len(active) or any(
            not t.strip() or t not in self.metadata for t in active
        ):
            raise ValueError("invalid active pool membership")
        for ticker, share in self.metadata.items():
            _validate_share(ticker, share)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


def calendar_fingerprint(holidays: frozenset[date]) -> str:
    """Version the calendar policy as well as the actual excluded dates."""
    payload = "regular-grid-10:00-23:35/v1/" + ",".join(str(day) for day in sorted(holidays))
    return hashlib.sha256(payload.encode()).hexdigest()


def completed_dates(now: datetime, sessions: int, holidays: frozenset[date]) -> tuple[date, ...]:
    """Return last N closed regular sessions, refusing unknown calendar coverage."""
    utc_slot(now)
    day = now.astimezone(MSK).date()
    # Current calendar coverage must be proved even on weekends/holidays.
    probe = datetime.combine(day, SESSION_TRADING_START, MSK)
    decision = session_calendar_decision(now=probe, known_holidays=holidays)
    if "год вне" in decision.reason:
        raise ValueError("unknown calendar coverage")
    dates: list[date] = []
    # Bounded search: never borrow arbitrarily ancient sessions through unknown years.
    for _ in range(sessions * 7 + 366):
        opening = datetime.combine(day, SESSION_TRADING_START, MSK)
        closing = datetime.combine(day, SESSION_CLOSING_AUCTION_START, MSK)
        if (
            closing <= now
            and session_calendar_decision(now=opening, known_holidays=holidays).allowed
        ):
            dates.append(day)
            if len(dates) == sessions:
                return tuple(reversed(dates))
        day -= timedelta(days=1)
    raise ValueError("insufficient confirmed calendar sessions")


def session_volumes(candles: list[Candle], dates: tuple[date, ...]) -> tuple[int, ...]:
    """Validate OHLCV and exact regular five-minute grids; incomplete is not cacheable."""
    by_time: dict[datetime, Candle] = {}
    for candle in candles:
        if candle.ts_open.tzinfo is None or candle.ts_open.utcoffset() is None:
            return ()
        local = candle.ts_open.astimezone(MSK)
        if (
            local.date() not in dates
            or not SESSION_TRADING_START <= local.time() < SESSION_CLOSING_AUCTION_START
        ):
            continue
        prices = candle.open, candle.high, candle.low, candle.close
        if (
            candle.is_complete is not True
            or candle.volume < 0
            or any(not p.is_finite() or p <= 0 for p in prices)
            or candle.high < max(prices)
            or candle.low > min(prices)
        ):
            return ()
        if local in by_time and by_time[local] != candle:
            return ()
        by_time[local] = candle
    volumes = []
    for day in dates:
        start = datetime.combine(day, SESSION_TRADING_START, MSK)
        end = datetime.combine(day, SESSION_CLOSING_AUCTION_START, MSK)
        expected = tuple(
            start + i * timedelta(minutes=5) for i in range((end - start) // timedelta(minutes=5))
        )
        actual = {ts for ts in by_time if ts.date() == day}
        if actual != set(expected):
            return ()
        volumes.append(sum(by_time[ts].volume for ts in expected))
    return tuple(volumes)


def _validate_share(ticker: str, share: ShareMetadata) -> None:
    if (
        not ticker.strip()
        or share.ticker != ticker
        or not share.instrument_id.strip()
        or type(share.lot_size) is not int
        or share.lot_size <= 0
        or not isinstance(share.min_price_increment, Decimal)
        or not share.min_price_increment.is_finite()
        or share.min_price_increment <= 0
        or not share.currency.strip()
        or not share.sector.strip()
        or not share.trading_status.strip()
        or type(share.short_enabled) is not bool
    ):
        raise ValueError("invalid pinned share metadata")


def _decode_snapshot(raw: str) -> Universe:
    data = json.loads(raw)
    if (
        not isinstance(data, dict)
        or type(data.get("version")) is not int
        or data["version"] != _VERSION
        or data.get("outcome") != "applied"
    ):
        raise ValueError("invalid pool journal envelope")
    try:
        generation = data["generation"]
        slot_raw = data["slot"]
        index_id = data["index_id"]
        selected, monitoring = data["selected"], data["monitoring"]
        if (
            type(generation) is not int
            or generation < 1
            or not isinstance(slot_raw, str)
            or not isinstance(index_id, str)
            or not isinstance(selected, list)
            or not isinstance(monitoring, list)
            or any(not isinstance(t, str) for t in (*selected, *monitoring))
        ):
            raise ValueError("invalid pool journal epoch")
        slot = datetime.fromisoformat(slot_raw)
        if slot.utcoffset() != timedelta(0):
            raise ValueError("pool journal slot must be UTC")
        metadata_raw = data["metadata"]
        if not isinstance(metadata_raw, dict):
            raise ValueError("invalid pool journal metadata")
        metadata = {}
        for ticker, item in metadata_raw.items():
            if (
                not isinstance(ticker, str)
                or not isinstance(item, dict)
                or any(
                    not isinstance(item.get(key), str)
                    for key in (
                        "ticker",
                        "instrument_id",
                        "min_price_increment",
                        "currency",
                        "sector",
                        "trading_status",
                    )
                )
                or type(item.get("lot_size")) is not int
                or type(item.get("short_enabled")) is not bool
            ):
                raise ValueError("invalid pool journal identity types")
            metadata[ticker] = ShareMetadata(
                ticker=item["ticker"],
                instrument_id=item["instrument_id"],
                lot_size=item["lot_size"],
                min_price_increment=Decimal(item["min_price_increment"]),
                currency=item["currency"],
                sector=item["sector"],
                short_enabled=item["short_enabled"],
                trading_status=item["trading_status"],
            )
        return Universe(generation, slot, tuple(selected), tuple(monitoring), metadata, index_id)
    except (KeyError, TypeError, ArithmeticError) as exc:
        raise ValueError("invalid pool journal snapshot") from exc


def _metadata_json(metadata: Mapping[str, ShareMetadata]) -> dict[str, dict[str, object]]:
    return {
        ticker: {**asdict(share), "min_price_increment": str(share.min_price_increment)}
        for ticker, share in metadata.items()
    }


class PoolJournal:
    """Append-only versioned pool events in existing event_log, committed before publish."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Use the existing runtime SQLite connection, never a background writer."""
        self.conn = conn

    def append(self, outcome: str, now: datetime, context: dict[str, object]) -> None:
        """Commit an auditable outcome; SQL errors propagate and prevent publication."""
        if self.conn.in_transaction:
            raise RuntimeError("pool journal requires idle main-thread connection")
        with self.conn:
            self.conn.execute(
                "INSERT INTO event_log(ts,level,module,event,ctx,notify_vk) VALUES(?,?,?,?,?,?)",
                (
                    now.astimezone(UTC).isoformat(),
                    "ERROR" if outcome == "failed" else "INFO",
                    "pool_refresh",
                    "pool_refresh_" + outcome,
                    json.dumps(
                        {**context, "version": _VERSION, "outcome": outcome}, sort_keys=True
                    ),
                    0,
                ),
            )

    def applied(self, universe: Universe, now: datetime, details: dict[str, object]) -> None:
        """Persist the full immutable snapshot rather than relying on gateway caches."""
        self.append(
            "applied",
            now,
            {
                **details,
                "generation": universe.generation,
                "slot": universe.slot.isoformat(),
                "selected": list(universe.selected),
                "monitoring": list(universe.monitoring),
                "index_id": universe.index_id,
                "metadata": _metadata_json(universe.metadata),
            },
        )

    def recover(self, open_tickers: frozenset[str]) -> dict[str, ShareMetadata]:
        """Recover proven open identities; legacy/malformed evidence requires reconciliation."""
        if not open_tickers:
            return {}
        row = self.conn.execute(_LATEST_SNAPSHOT_SQL).fetchone()
        if row is None:
            raise ValueError(
                "open identity unproven: explicit pool journal reconciliation required"
            )
        universe = _decode_snapshot(row[0])
        if not open_tickers <= set((*universe.selected, *universe.monitoring)):
            raise ValueError("open identity not proven by active journal membership")
        return {ticker: universe.metadata[ticker] for ticker in open_tickers}

    def generation(self) -> int:
        """Read the last committed generation for restart-monotonic audit epochs."""
        row = self.conn.execute(_LATEST_SNAPSHOT_SQL).fetchone()
        return _decode_snapshot(row[0]).generation if row else 0


@dataclass(frozen=True)
class RefreshDependencies:
    """Main-thread data, persistence and publication seams; no order capabilities."""

    gateway: InvestGateway
    journal: PoolJournal
    clock: Callable[[], datetime]
    open_tickers: Callable[[], frozenset[str]]
    publish: Callable[[Universe], None]
    rules: LiquidityRules
    holidays: frozenset[date]
    workers: int = 4


@dataclass
class _Attempt:
    slot: datetime
    dates: tuple[date, ...]
    metadata: dict[str, ShareMetadata] = field(default_factory=dict)
    exclusions: dict[str, str] = field(default_factory=dict)
    index_id: str = ""
    volumes: dict[str, tuple[int, ...]] = field(default_factory=dict)
    candidates: list[str] = field(default_factory=list)
    pages: Iterator[list[Candle]] | None = None
    candles: list[Candle] = field(default_factory=list)
    stage: str = "catalog"
    threshold: str | None = None
    jobs: dict[str, Future[list[Candle]]] = field(default_factory=dict)
    parallel_started: bool = False


_CacheKey = tuple[str, int, tuple[date, ...], str, int]


class PoolRefresh:
    """One cooperative attempt per wall slot; failed attempts wait for the next slot."""

    def __init__(self, dependencies: RefreshDependencies) -> None:
        """Own staging and verified completed-session volumes, never a second feed."""
        self.deps = dependencies
        self.gate = PoolGate()
        self.universe: Universe | None = None
        self._generation = dependencies.journal.generation()
        self._attempt: _Attempt | None = None
        self._attempted_slot: datetime | None = None
        self._cache: dict[_CacheKey, tuple[int, ...]] = {}
        self._rollback_reported = False
        self._workers: HistoryWorkers | None = None

    def close(self) -> None:
        """Drain read-only jobs on all runtime exits before closing SQLite."""
        if self._workers is not None:
            self._workers.close()
            self._workers = None

    def _cancel_work(self) -> None:
        if self._workers is not None:
            self._workers.cancel()

    def _key(self, share: ShareMetadata, dates: tuple[date, ...]) -> _CacheKey:
        return (
            share.instrument_id,
            share.lot_size,
            dates,
            calendar_fingerprint(self.deps.holidays),
            _VERSION,
        )

    def seed(
        self,
        universe: Universe,
        volumes: Mapping[str, list[int]],
        *,
        dates: tuple[date, ...] | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        """Publish an already validated bootstrap snapshot and seed only complete volumes."""
        now = self.deps.clock()
        self.deps.gateway.check_cancelled()
        current_dates = completed_dates(now, self.deps.rules.sessions, self.deps.holidays)
        if dates is not None and dates != current_dates:
            raise ValueError("bootstrap completed-session window expired; restart collection")
        dates = current_dates
        for ticker, values in volumes.items():
            if len(values) == len(dates) and all(type(v) is int and v >= 0 for v in values):
                self._cache[self._key(universe.metadata[ticker], dates)] = tuple(values)
        self._commit(
            universe,
            {
                **(details or {}),
                "dates": [str(d) for d in dates],
                "calendar_fingerprint": calendar_fingerprint(self.deps.holidays),
                "bootstrap": True,
            },
        )
        if self.universe is not universe:
            raise ValueError("bootstrap epoch expired before publication; restart required")
        self._attempted_slot = universe.slot

    def check_due(self) -> None:
        """Revoke stale permission before normal feed work; coalesce to the latest slot."""
        now = self.deps.clock()
        permission = self.gate.check(now)
        if permission.reason == "pool_clock_rollback":
            if not self._rollback_reported:
                self._fail("PoolClockRollback", now)
                self._rollback_reported = True
            self._cancel_work()
            self._attempt = None
            return
        slot = permission.slot
        if slot == self._attempted_slot:
            return
        self.deps.gateway.check_cancelled()
        self._cancel_work()
        self._attempted_slot = slot
        self.gate.block("pool_refresh_in_progress")
        try:
            dates = completed_dates(now, self.deps.rules.sessions, self.deps.holidays)
            self._attempt = _Attempt(slot, dates)
        except ValueError as exc:
            self._fail(type(exc).__name__, now)

    def _fail(self, error_type: str, now: datetime) -> None:
        self._cancel_work()
        self.gate.block("pool_refresh_failed")
        attempt = self._attempt
        self._attempt = None
        context: dict[str, object] = {
            "slot": self._attempted_slot.isoformat() if self._attempted_slot else None,
            "generation": self._generation,
            "error_type": error_type,
            "stage": attempt.stage if attempt else "calendar",
            "dates": [str(d) for d in attempt.dates] if attempt else [],
            "metadata": _metadata_json(attempt.metadata) if attempt else {},
            "exclusions": attempt.exclusions if attempt else {},
            "index_id": attempt.index_id if attempt else None,
            "threshold_lots": attempt.threshold if attempt else None,
        }
        self.deps.journal.append("failed", now, context)
        logger.error("event=pool_refresh_failed ctx=%s action=block_until_next_slot", context)

    def step(self) -> None:
        """One catalog/index read or nonblocking poll of N complete-candidate futures."""
        self.check_due()
        attempt = self._attempt
        if attempt is None:
            return
        self.deps.gateway.check_cancelled()
        try:
            if attempt.stage == "catalog":
                catalog = self.deps.gateway.discover_paper_shares(pin=False)
                attempt.metadata = dict(catalog.shares)
                for ticker, share in attempt.metadata.items():
                    _validate_share(ticker, share)
                compatible = {
                    self._key(share, attempt.dates) for share in attempt.metadata.values()
                }
                self._cache = {
                    key: value for key, value in self._cache.items() if key in compatible
                }
                attempt.exclusions = dict(catalog.excluded)
                attempt.candidates = list(catalog.shares)
                attempt.stage = "index"
                return
            if attempt.stage == "index":
                attempt.index_id = self.deps.gateway.resolve_imoex_id()
                attempt.stage = "history"
                return
            if self.deps.workers > 1 and not self._parallel_history(attempt):
                return
            while attempt.candidates:
                ticker = attempt.candidates[0]
                share = attempt.metadata[ticker]
                key = self._key(share, attempt.dates)
                cached = self._cache.get(key)
                if cached is not None:
                    attempt.volumes[ticker] = cached
                    attempt.candidates.pop(0)
                    continue
                if attempt.pages is None:
                    attempt.pages = self.deps.gateway.iter_candle_pages(
                        instrument_id=share.instrument_id,
                        timeframe="5m",
                        from_=datetime.combine(attempt.dates[0], SESSION_TRADING_START, MSK),
                        to=datetime.combine(attempt.dates[-1], SESSION_CLOSING_AUCTION_START, MSK),
                    )
                try:
                    page = next(attempt.pages)
                except StopIteration:
                    values = session_volumes(attempt.candles, attempt.dates)
                    attempt.volumes[ticker] = values
                    if values:
                        self._cache[key] = values
                    attempt.candidates.pop(0)
                    attempt.pages = None
                    attempt.candles = []
                    # No second page on the same heartbeat, even when iterator exhausts.
                    return
                attempt.candles.extend(page)
                self.deps.gateway.check_cancelled()
                return
            recommendation = recommend_pool(attempt.volumes, self.deps.rules)
            attempt.threshold = str(recommendation.threshold_lots)
            attempt.exclusions.update(recommendation.excluded)
            self._apply(attempt, recommendation)
        except (GatewayError, ValueError, ArithmeticError) as exc:
            self.deps.gateway.check_cancelled()
            self._fail(type(exc).__name__, self.deps.clock())

    def _parallel_history(self, attempt: _Attempt) -> bool:
        """Owner-only polling/staging; workers read checked pages between heartbeats.

        A candidate's gateway history remains all-or-error. No page throughput is
        gated by the owner's heartbeat; the main thread is free for monitoring.
        """
        if self._workers is None:
            self._workers = HistoryWorkers(self.deps.gateway, self.deps.workers)
        workers = self._workers
        if not attempt.parallel_started:
            if not workers.restart():
                return False  # Prior epoch's in-flight sync RPC still draining.
            attempt.parallel_started = True
        failure = workers.failure
        if failure is not None:
            raise failure
        done = {ticker: future for ticker, future in attempt.jobs.items() if future.done()}
        # Fail before staging successes from this poll if any RPC failed.
        for future in done.values():
            error = future.exception()
            if error is not None:
                raise error
        for ticker, future in done.items():
            values = session_volumes(future.result(), attempt.dates)
            attempt.volumes[ticker] = values
            if values:  # Incomplete histories MUST be downloaded again next slot.
                self._cache[self._key(attempt.metadata[ticker], attempt.dates)] = values
            del attempt.jobs[ticker]
        while attempt.candidates and len(attempt.jobs) < self.deps.workers:
            ticker = attempt.candidates.pop(0)
            share = attempt.metadata[ticker]
            cached = self._cache.get(self._key(share, attempt.dates))
            if cached is not None:
                attempt.volumes[ticker] = cached
                continue
            request = partial(
                self.deps.gateway.get_candles_history,
                instrument_id=share.instrument_id,
                timeframe="5m",
                from_=datetime.combine(attempt.dates[0], SESSION_TRADING_START, MSK),
                to=datetime.combine(attempt.dates[-1], SESSION_CLOSING_AUCTION_START, MSK),
            )
            attempt.jobs[ticker] = workers.submit(request)
        self.deps.gateway.check_cancelled()
        return not attempt.jobs and not attempt.candidates

    def _apply(self, attempt: _Attempt, recommendation: PoolRecommendation) -> None:
        now = self.deps.clock()
        # Long history/catalog work never grants an already expired slot.
        if utc_slot(now) != attempt.slot:
            self.check_due()
            return
        if completed_dates(now, self.deps.rules.sessions, self.deps.holidays) != attempt.dates:
            raise ValueError("completed session window changed during refresh")
        opens = self.deps.open_tickers()
        pinned = (
            {
                t: self.universe.metadata[t]
                for t in (*self.universe.selected, *self.universe.monitoring)
            }
            if self.universe
            else self.deps.journal.recover(opens)
        )
        conflicts = {
            ticker
            for ticker in opens
            if ticker not in attempt.metadata
            or ticker not in pinned
            or pinned[ticker].instrument_id != attempt.metadata[ticker].instrument_id
            or pinned[ticker].lot_size != attempt.metadata[ticker].lot_size
        }
        if conflicts:
            attempt.exclusions.update(
                {t: "open_identity_conflict: pinned monitoring only" for t in conflicts}
            )
            logger.error(
                "event=pool_open_identity_conflict tickers=%s action=keep_pinned_block_entries",
                sorted(conflicts),
            )
            self._fail("OpenIdentityConflict", now)
            return
        selected = recommendation.selected
        metadata = dict(attempt.metadata)
        for ticker in opens:
            if ticker not in pinned:
                raise ValueError("open identity unproven")
            metadata[ticker] = pinned[ticker]
        universe = Universe(
            self._generation + 1,
            attempt.slot,
            selected,
            tuple(sorted(opens - set(selected))),
            metadata,
            attempt.index_id,
        )
        self._commit(
            universe,
            {
                "dates": [str(d) for d in attempt.dates],
                "calendar_fingerprint": calendar_fingerprint(self.deps.holidays),
                "threshold_lots": str(recommendation.threshold_lots),
                "average_daily_lots": {
                    k: str(v) for k, v in recommendation.average_daily_lots.items()
                },
                "exclusions": {
                    **attempt.exclusions,
                    **recommendation.excluded,
                    **{t: "open_identity_conflict: pinned monitoring only" for t in conflicts},
                },
            },
        )
        self._attempt = None

    def _commit(self, universe: Universe, details: dict[str, object]) -> None:
        self.deps.gateway.check_cancelled()
        now = self.deps.clock()
        permission = self.gate.check(now)
        if permission.reason == "pool_clock_rollback" or utc_slot(now) != universe.slot:
            self.gate.block("pool_refresh_due")
            return
        self.deps.journal.applied(universe, now, details)
        self._generation = universe.generation
        self.deps.gateway.check_cancelled()
        permission = self.gate.check(self.deps.clock())
        if permission.reason == "pool_clock_rollback" or permission.slot != universe.slot:
            self.gate.block("pool_refresh_due")
            return
        self.deps.publish(universe)
        self.universe = universe
        self.gate.publish(universe.slot, universe.generation)
        logger.info(
            "event=paper_pool_applied selected=%s monitoring_only=%s generation=%s slot=%s",
            universe.selected,
            universe.monitoring,
            universe.generation,
            universe.slot.isoformat(),
        )
