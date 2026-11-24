"""Admitted work keeps a quota lease across arbitrary pre-dispatch scheduling gaps."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

import pytest
from grpc import StatusCode
from tinkoff.invest.exceptions import RequestError
from tinkoff.invest.logging import Metadata

from tests.test_candle_requests import Clock, _gateway_for_request
from trading_bot.api_client import (
    CandleQuotaExhaustedError,
    CandleRequestError,
    CandleRequestTiming,
)
from trading_bot.cancellation import OperationCancelled
from trading_bot.config import CandleRequestPolicy

START = datetime(2026, 10, 1, tzinfo=UTC)


def test_delayed_admitted_request_cannot_age_out_before_actual_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    starts: list[float] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        starts.append(clock.now)
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(
        rpc,
        clock,
        CandleRequestPolicy(
            min_interval_sec=0.001, market_data_requests_per_minute=2, max_retry_wait_sec=300
        ),
    )
    admitted, release = Event(), Event()
    original = gateway._admit_market_data

    def stalled_admission(deadline: float) -> object:
        value = original(deadline)
        if not admitted.is_set():
            admitted.set()
            assert release.wait(2)
        return value

    monkeypatch.setattr(gateway, "_admit_market_data", stalled_admission)
    with ThreadPoolExecutor(1) as workers:
        first = workers.submit(
            gateway.get_candles, instrument_id="delayed", timeframe="5m", from_=START
        )
        try:
            assert admitted.wait(1)
            clock.now = 61  # The pending callback has not entered the SDK for >60 seconds.
            for _ in range(2):
                gateway.get_candles(instrument_id="ordinary", timeframe="5m", from_=START)
        finally:
            release.set()
        assert first.result(timeout=1) == []
    assert len(starts) == 3
    assert all(sum(t - 60 < s <= t for s in starts) <= 2 for t in starts)


def test_delayed_admission_rechecks_new_shared_server_cooldown_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = Clock()
    starts: dict[str, float] = {}

    def rpc(**kwargs: object) -> SimpleNamespace:
        name = str(kwargs["instrument_id"])
        starts[name] = clock.now
        if name == "quota":
            raise RequestError(
                StatusCode.RESOURCE_EXHAUSTED,
                "private",
                Metadata("private", None, 0, 180, "private"),
            )
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(rpc, clock, CandleRequestPolicy(quota_max_attempts=1))
    admitted, release = Event(), Event()
    original = gateway._admit_market_data

    def stalled_admission(deadline: float) -> object:
        value = original(deadline)
        if not admitted.is_set():
            admitted.set()
            assert release.wait(2)
        return value

    monkeypatch.setattr(gateway, "_admit_market_data", stalled_admission)
    with ThreadPoolExecutor(1) as workers:
        first = workers.submit(
            gateway.get_candles, instrument_id="delayed", timeframe="5m", from_=START
        )
        try:
            assert admitted.wait(1)
            with pytest.raises(CandleQuotaExhaustedError):
                gateway.get_candles(instrument_id="quota", timeframe="5m", from_=START)
        finally:
            release.set()
        assert first.result(timeout=1) == []
    assert starts["delayed"] >= starts["quota"] + 180


@pytest.mark.parametrize("failure", ["sdk", "non_sdk", "cancel_before", "cancel_rpc"])
def test_all_callback_error_and_cancellation_paths_release_the_lease(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopClock(Clock):
        stopped = False

        def timing(self) -> CandleRequestTiming:
            return CandleRequestTiming(
                monotonic=self.monotonic, sleep=self.sleep, should_stop=lambda: self.stopped
            )

    clock = StopClock()
    calls = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if failure == "sdk":
            raise RequestError(StatusCode.UNAUTHENTICATED, "private", None)
        if failure == "non_sdk":
            raise ValueError("private")
        clock.stopped = True
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(rpc, clock)
    if failure == "cancel_before":
        original = gateway._admit_market_data

        def cancelled_admission(deadline: float) -> object:
            value = original(deadline)
            clock.stopped = True
            return value

        monkeypatch.setattr(gateway, "_admit_market_data", cancelled_admission)
    error = OperationCancelled if failure.startswith("cancel") else CandleRequestError
    with pytest.raises(error):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START)
    assert calls == (0 if failure == "cancel_before" else 1)
    assert len(gateway._market_data_leases) == 0
    assert len(gateway._market_data_completions) == 1
