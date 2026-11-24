"""Executable read-only latency benchmark: python -m tests.benchmark_liquidity.

Default: simulated SDK latency through the real InvestGateway and PaperTickFeed,
not a network measurement. --sandbox explicitly opts into bounded historical reads
using the OS keyring; no SQLite, positions, orders, or runtime restarts are involved.
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
from collections.abc import Callable
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import partial
from threading import Lock
from time import monotonic, sleep
from types import SimpleNamespace
from typing import TypeVar, cast

from trading_bot.api_client import (
    NORMAL_TRADING_STATUS,
    CandleRequestTiming,
    InvestGateway,
    ShareMetadata,
    sandbox_target,
)
from trading_bot.cancellation import CancellationToken, OperationCancelled
from trading_bot.config import Settings
from trading_bot.history_workers import HistoryWorkers
from trading_bot.market_regime import MarketRegimeClassifier, MarketRegimeConfig
from trading_bot.paper_account import PaperAccountSource
from trading_bot.paper_feed import PaperTickFeed
from trading_bot.security import get_stored_token
from trading_bot.strategy import StrategyConfig

_Result = TypeVar("_Result")
_NOW = datetime(2026, 10, 16, 12, tzinfo=UTC)


@dataclass
class Budget:
    """Hard actual-call cap across both phases; deadline is cooperative, not RPC abort."""

    limit: int = 120
    deadline: float = field(default_factory=lambda: monotonic() + 120)
    count: int = 0
    lock: Lock = field(default_factory=Lock)


class MeasuredGateway(InvestGateway):
    """Measure actual request callbacks, including every retry, outside admission lock."""

    def __init__(self, *args: object, budget: Budget, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self.budget = budget
        self.starts: list[float] = []
        self.inflight = self.peak = 0
        self.metrics_lock = Lock()

    def _request_candles(
        self, request: Callable[[], _Result], instrument_id: str, timeframe: str
    ) -> _Result:
        def measured() -> _Result:
            with self.budget.lock:
                if self.budget.count >= self.budget.limit:
                    raise OperationCancelled
                self.budget.count += 1
            with self.metrics_lock:
                self.starts.append(monotonic())
                self.inflight += 1
                self.peak = max(self.peak, self.inflight)
            try:
                return request()
            finally:
                with self.metrics_lock:
                    self.inflight -= 1

        return super()._request_candles(measured, instrument_id, timeframe)


class SimulatedClient:
    """Synthetic SDK responses only; real wall-clock sleep models unary latency."""

    def __init__(self, latency: float) -> None:
        self.latency = latency
        self.market_data = SimpleNamespace(get_candles=self.get_candles)

    def __enter__(self) -> SimulatedClient:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def get_candles(self, **kwargs: object) -> SimpleNamespace:
        """Return exact complete weekday grids for the requested inclusive page."""
        sleep(self.latency)
        start, end = kwargs["from_"], kwargs["to"]
        assert isinstance(start, datetime) and isinstance(end, datetime)
        day = start.replace(hour=7, minute=0, second=0, microsecond=0)
        candles = []
        price = SimpleNamespace(units=100, nano=0)
        while day <= end:
            if day.weekday() < 5:
                for offset in range(164):
                    ts = day + timedelta(minutes=5 * offset)
                    if start <= ts <= end:
                        candles.append(
                            SimpleNamespace(
                                open=price,
                                high=price,
                                low=price,
                                close=price,
                                volume=1,
                                time=ts,
                                is_complete=True,
                            )
                        )
            day += timedelta(days=1)
        return SimpleNamespace(candles=candles)


def _summary(gateway: MeasuredGateway, workers: int, began: float) -> dict[str, object]:
    elapsed = monotonic() - began
    starts = sorted(gateway.starts)
    return {
        "workers": workers,
        "elapsed_sec": elapsed,
        "actual_rpc_starts": len(starts),
        "rpc_per_sec": len(starts) / elapsed,
        "peak_inflight": gateway.peak,
        "start_span_sec": starts[-1] - starts[0] if len(starts) > 1 else 0,
        "minimum_start_spacing_sec": min(
            (b - a for a, b in zip(starts, starts[1:], strict=False)), default=0
        ),
        "maximum_sliding_60s_starts": max(
            (sum(t - 60 < s <= t for s in starts) for t in starts), default=0
        ),
    }


def _simulated_phase(
    workers: int, latency: float, budget: Budget, token: CancellationToken
) -> dict[str, object]:
    client = SimulatedClient(latency)
    gateway = MeasuredGateway(
        "synthetic-unused",
        target="offline-no-network",
        budget=budget,
        client_factory=lambda *_: client,
        candle_timing=CandleRequestTiming(
            sleep=token.sleep,
            should_stop=lambda: token.is_cancelled() or monotonic() >= budget.deadline,
        ),
    )
    tickers = tuple(f"BENCH{i}" for i in range(4))
    # Seed verified local metadata only in this synthetic benchmark (no SDK catalogue read).
    gateway._discovered_shares = {
        ticker: ShareMetadata(
            ticker, ticker, 1, Decimal(".01"), "RUB", "benchmark", True, NORMAL_TRADING_STATUS
        )
        for ticker in tickers
    }
    settings = Settings(_env_file=None)
    strategy = StrategyConfig(
        **{item.name: getattr(settings, item.name) for item in fields(StrategyConfig)}
    )
    feed = PaperTickFeed(
        gateway=gateway,
        account_source=cast(
            PaperAccountSource, object()
        ),  # Must never be accessed by history work.
        regime_classifier=MarketRegimeClassifier(MarketRegimeConfig()),
        strategy_config=strategy,
        approved_tickers=tickers,
        index_instrument_id="synthetic-index",
        clock=lambda: _NOW,
        max_bar_staleness=timedelta(minutes=10),
        max_quote_staleness=timedelta(seconds=30),
        liquidity_workers=workers,
    )
    began = monotonic()
    volumes = feed.daily_volume_lots(tickers, sessions=1)
    assert volumes == {ticker: [164] for ticker in tickers}
    return _summary(gateway, workers, began)


def _sandbox_phases(
    args: argparse.Namespace, budget: Budget, token: CancellationToken
) -> list[dict[str, object]]:
    if not args.instrument_id or not args.start:
        raise ValueError(
            "--sandbox requires --instrument-id and --start (aware historical ISO time)"
        )
    start = datetime.fromisoformat(args.start)
    if start.tzinfo is None or start + timedelta(days=args.pages) >= datetime.now(tz=UTC):
        raise ValueError("sandbox range must be timezone-aware and entirely historical")
    gateway = MeasuredGateway(
        get_stored_token("broker_token"),
        target=sandbox_target(),
        budget=budget,
        candle_timing=CandleRequestTiming(
            sleep=token.sleep,
            should_stop=lambda: token.is_cancelled() or monotonic() >= budget.deadline,
        ),
    )
    phases = []
    for workers in (1, 4):
        gateway.starts.clear()
        gateway.peak = 0
        jobs = {
            str(i): partial(
                gateway.get_candles,
                instrument_id=args.instrument_id,
                timeframe="5m",
                from_=start + timedelta(days=i),
                to=start + timedelta(days=i + 1),
            )
            for i in range(args.pages)
        }
        executor = HistoryWorkers(gateway, workers)
        began = monotonic()
        try:
            results = list(executor.histories(jobs))
            if any(isinstance(value, ValueError) for _, value in results):
                raise ValueError("historical page validation failed")
        finally:
            executor.close()
        phases.append(_summary(gateway, workers, began))
    return phases


def main() -> int:
    """Print bounded JSON measurements; never emit credentials or raw SDK failures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("--instrument-id")
    parser.add_argument("--start")
    parser.add_argument("--pages", type=int, default=12, help="pages per sandbox phase (1..60)")
    parser.add_argument("--latency", type=float, default=0.4, help="simulated RPC seconds")
    args = parser.parse_args()
    if not 1 <= args.pages <= 60 or not 0.01 <= args.latency <= 2:
        parser.error("pages must be 1..60; latency must be .01..2")
    logging.basicConfig(level=logging.CRITICAL)
    token = CancellationToken()
    budget = Budget()
    old_handlers = {
        sig: signal.signal(sig, lambda *_: token.request_stop())
        for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        # Warm verified SDK symbols outside both timed phases; no RPC or token lookup.
        sandbox_target()
        phases = (
            _sandbox_phases(args, budget, token)
            if args.sandbox
            else [_simulated_phase(workers, args.latency, budget, token) for workers in (1, 4)]
        )
        first, second = phases
        print(
            json.dumps(
                {
                    "mode": (
                        "sandbox-network" if args.sandbox else "simulated-sdk-latency-not-network"
                    ),
                    "simulated_rpc_latency_sec": None if args.sandbox else args.latency,
                    "warmup": "SDK symbols loaded before timing; no warmup RPC",
                    "scope": "one gateway/current process; no control over other tokens/processes",
                    "deadline": "120s cooperative; in-flight sync RPC may finish later",
                    "phases": phases,
                    "speedup_elapsed": first["elapsed_sec"] / second["elapsed_sec"],
                    "total_actual_rpc_starts": budget.count,
                },
                indent=2,
            )
        )
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "outcome": "stopped",
                    "error_type": type(exc).__name__,
                    "actual_rpc_starts": budget.count,
                }
            )
        )
        return 1
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    raise SystemExit(main())
