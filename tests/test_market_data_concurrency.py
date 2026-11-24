"""Offline shared unary admission and concurrent RPC regression tests."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from grpc import StatusCode
from tinkoff.invest.exceptions import RequestError
from tinkoff.invest.logging import Metadata

from tests.test_candle_requests import Clock, _gateway_for_request
from trading_bot.api_client import CandleQuotaExhaustedError, CandleRequestError, InvestGateway
from trading_bot.cancellation import OperationCancelled
from trading_bot.config import CandleRequestPolicy
from trading_bot.history_workers import HistoryWorkers

START = datetime(2026, 10, 1, tzinfo=UTC)


def test_rpc_latency_overlaps_without_holding_admission_lock() -> None:
    entered = Event()
    release = Event()
    lock = Lock()
    inflight = 0
    peak = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal inflight, peak
        with lock:
            inflight += 1
            peak = max(peak, inflight)
            if inflight == 2:
                entered.set()
        release.wait(2)
        with lock:
            inflight -= 1
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(rpc, Clock())
    with ThreadPoolExecutor(2) as workers:
        futures = [
            workers.submit(gateway.get_candles, instrument_id=str(i), timeframe="5m", from_=START)
            for i in range(2)
        ]
        try:
            assert entered.wait(1), "second RPC was serialized behind first RPC latency"
        finally:
            release.set()
        assert [f.result() for f in futures] == [[], []]
    assert peak == 2


def test_sliding_window_covers_combined_actual_unary_methods() -> None:
    clock = Clock()
    starts: list[float] = []

    def record(**kwargs: object) -> SimpleNamespace:
        starts.append(clock.now)
        return SimpleNamespace(
            candles=[],
            trading_status=SimpleNamespace(name="NORMAL"),
            orderbook_ts=START,
            bids=[SimpleNamespace(price=SimpleNamespace(units=99, nano=0), quantity=1)],
            asks=[SimpleNamespace(price=SimpleNamespace(units=101, nano=0), quantity=1)],
        )

    class Client:
        market_data = SimpleNamespace(
            get_candles=record, get_trading_status=record, get_order_book=record
        )

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    gateway = InvestGateway(
        "offline",
        target="fake",
        client_factory=lambda *_: Client(),
        candle_timing=clock.timing(),
        candle_policy=CandleRequestPolicy(min_interval_sec=0.001),
    )
    for i in range(601):
        if i % 3 == 0:
            gateway.get_trading_status(instrument_id="ID")
        elif i % 3 == 1:
            gateway.get_order_book_quote(instrument_id="ID")
        else:
            gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START)
    assert len(starts) == 601
    assert starts[600] >= 60
    assert all(sum(t - 60 < s <= t for s in starts) <= 600 for t in starts)


@pytest.mark.parametrize("first", ["candles", "status", "book"])
def test_server_cooldown_from_any_unary_method_blocks_all_later_methods(first: str) -> None:
    clock = Clock()
    starts: list[float] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        starts.append(clock.now)
        if len(starts) == 1:
            raise RequestError(
                StatusCode.RESOURCE_EXHAUSTED,
                "private",
                Metadata("private", None, 0, 180, "private"),
            )
        return SimpleNamespace(
            candles=[],
            trading_status=SimpleNamespace(name="NORMAL"),
            orderbook_ts=START,
            bids=[SimpleNamespace(price=SimpleNamespace(units=99, nano=0), quantity=1)],
            asks=[SimpleNamespace(price=SimpleNamespace(units=101, nano=0), quantity=1)],
        )

    class Client:
        market_data = SimpleNamespace(get_candles=rpc, get_trading_status=rpc, get_order_book=rpc)

        def __enter__(self) -> "Client":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    gateway = InvestGateway(
        "offline",
        target="fake",
        client_factory=lambda *_: Client(),
        candle_timing=clock.timing(),
        candle_policy=CandleRequestPolicy(quota_max_attempts=1),
    )
    methods = {
        "candles": partial(gateway.get_candles, instrument_id="ID", timeframe="5m", from_=START),
        "status": partial(gateway.get_trading_status, instrument_id="ID"),
        "book": partial(gateway.get_order_book_quote, instrument_id="ID"),
    }
    with pytest.raises(CandleQuotaExhaustedError):
        methods[first]()
    for method in methods.values():
        method()
    assert starts[0] == 0
    assert starts[1] == 180
    assert all(t >= 180 for t in starts[1:])


def test_long_quota_reset_is_waited_and_61_attempts_are_finite() -> None:
    clock = Clock()
    starts: list[float] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        starts.append(clock.now)
        if len(starts) <= 60:
            raise RequestError(
                StatusCode.RESOURCE_EXHAUSTED,
                "private",
                Metadata("private", None, 0, 180, "private"),
            )
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(rpc, clock)
    assert gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START) == []
    assert len(starts) == 61
    assert starts[-1] == 60 * 180


def test_quota_rpc_latency_does_not_consume_unavailable_elapsed_budget() -> None:
    clock = Clock()
    calls = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            clock.now += 3
            raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "private", None)
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(rpc, clock, CandleRequestPolicy(max_retry_wait_sec=1))
    assert gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START) == []
    assert calls == 2


def test_quota_wait_does_not_erase_unrelated_transport_pacing_budget() -> None:
    clock = Clock()
    calls = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "private", None)
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(
        rpc, clock, CandleRequestPolicy(min_interval_sec=200, quota_max_attempts=2)
    )
    with pytest.raises(CandleRequestError) as error:
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START)
    assert not isinstance(error.value, CandleQuotaExhaustedError)
    assert calls == 1
    assert clock.waits == [1]


def test_persistent_quota_stops_after_61_actual_calls() -> None:
    clock = Clock()
    starts: list[float] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        starts.append(clock.now)
        raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "private", None)

    with pytest.raises(CandleQuotaExhaustedError):
        _gateway_for_request(rpc, clock).get_candles(
            instrument_id="ID", timeframe="5m", from_=START
        )
    assert len(starts) == 61
    assert starts[-1] == 60


def test_mixed_unavailable_and_quota_cannot_exceed_61_total_actual_attempts() -> None:
    clock = Clock()
    calls = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        code = StatusCode.UNAVAILABLE if calls <= 3 else StatusCode.RESOURCE_EXHAUSTED
        raise RequestError(code, "private", None)

    gateway = _gateway_for_request(rpc, clock)
    with pytest.raises(CandleQuotaExhaustedError):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START)
    assert calls == 61


def test_worker_failure_immediately_cancels_queued_peer_rpc() -> None:
    clock = Clock()
    calls: list[str] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        calls.append(str(kwargs["instrument_id"]))
        raise RequestError(StatusCode.UNAUTHENTICATED, "private", None)

    gateway = _gateway_for_request(rpc, clock)
    workers = HistoryWorkers(gateway, 2)
    try:
        first = workers.submit(
            partial(gateway.get_candles, instrument_id="A", timeframe="5m", from_=START)
        )
        with pytest.raises(CandleRequestError):
            first.result(timeout=1)
        second = workers.submit(
            partial(gateway.get_candles, instrument_id="B", timeframe="5m", from_=START)
        )
        with pytest.raises(OperationCancelled):
            second.result(timeout=1)
        assert calls == ["A"]
    finally:
        workers.close()
