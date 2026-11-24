"""Bounded read-only history workers, never owners of feed state or SQLite.

Allowed: execute gateway reads with work-scoped cooperative cancellation.
Forbidden: persistence, universe publication, account access or trading.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from threading import Event, Lock
from typing import TypeVar

from trading_bot.api_client import Candle, InvestGateway
from trading_bot.cancellation import OperationCancelled

_Result = TypeVar("_Result")
logger = logging.getLogger(__name__)


class HistoryWorkers:
    """A bounded executor whose owner controls submission, staging and shutdown."""

    def __init__(self, gateway: InvestGateway, workers: int) -> None:
        """Create at most N read workers; callers never submit an unbounded queue."""
        if not 1 <= workers <= 32:
            raise ValueError("history workers must be between 1 and 32")
        self._gateway = gateway
        self.workers = workers
        self._stop = Event()
        self._futures: list[Future[object]] = []
        self._failure_lock = Lock()
        self._failure: BaseException | None = None
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="liquidity")

    def submit(self, request: Callable[[], _Result]) -> Future[_Result]:
        """Execute only a gateway read/iterator step, not an owner-state callback."""
        self._gateway.check_cancelled()

        stop = self._stop

        def run() -> _Result:
            try:
                with self._gateway.history_worker(stop):
                    return request()
            except ValueError:
                raise  # Per-instrument invalid history is classified by the owner.
            except BaseException as exc:
                with self._failure_lock:
                    if self._failure is None:
                        self._failure = exc
                stop.set()
                if not isinstance(exc, OperationCancelled):
                    logger.error(
                        "event=history_worker_failed stage=history error_type=%s "
                        "action=cancel_work",
                        type(exc).__name__,
                    )
                raise

        future = self._executor.submit(run)
        self._futures = [item for item in self._futures if not item.done()]
        self._futures.append(future)  # type: ignore[arg-type]  # Futures are invariant
        return future

    @property
    def failure(self) -> BaseException | None:
        """Preserve the original systemic failure over peer cancellation side effects."""
        with self._failure_lock:
            return self._failure

    @property
    def idle(self) -> bool:
        """Whether cancelled/in-flight jobs have all left the worker boundary."""
        return all(future.done() for future in self._futures)

    def cancel(self) -> None:
        """Abandon the epoch; no new RPCs may start through its work-scoped flag."""
        self._stop.set()
        for future in self._futures:
            future.cancel()

    def restart(self) -> bool:
        """Reuse the fixed executor only after the prior epoch has drained."""
        if not self.idle:
            return False
        self._stop = Event()
        with self._failure_lock:
            self._failure = None
        self._futures.clear()
        return True

    def histories(
        self, requests: Mapping[str, Callable[[], list[Candle]]]
    ) -> Iterator[tuple[str, list[Candle] | ValueError]]:
        """Collect a bounded batch at a time, polling cancellation on the owner thread."""
        if self.workers == 1:
            for ticker, request in requests.items():
                self._gateway.check_cancelled()
                try:
                    result: list[Candle] | ValueError = request()
                except ValueError as exc:
                    result = exc
                yield ticker, result
            return
        pending_requests = iter(requests.items())
        active: dict[Future[list[Candle]], str] = {}
        while True:
            while len(active) < self.workers:
                item = next(pending_requests, None)
                if item is None:
                    break
                ticker, request = item
                active[self.submit(request)] = ticker
            if not active:
                return
            self._gateway.check_cancelled()
            done, _ = wait(active, timeout=0.05, return_when=FIRST_COMPLETED)
            # Discover systemic failures before returning any successes to the owner.
            failure = self.failure
            if failure is not None:
                raise failure
            for future in done:
                error = future.exception()
                if error is not None and not isinstance(error, ValueError):
                    raise error
            for future in done:
                ticker = active.pop(future)
                try:
                    result = future.result()
                except ValueError as exc:
                    result = exc
                yield ticker, result

    def close(self, *, wait: bool = True) -> None:
        """Cancel pending work and interrupt waits; already in-flight sync RPC may finish."""
        self.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)
