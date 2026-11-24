"""Offline same-page UNAVAILABLE recovery with the existing candle retry budget."""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from grpc import StatusCode
from tinkoff.invest._errors import handle_request_error
from tinkoff.invest.exceptions import RequestError
from tinkoff.invest.logging import Metadata

from tests.test_api_client import _fake_historic_candle
from tests.test_candle_requests import Clock, _gateway_for_request
from tests.test_candle_sdk_boundary import QuotaCall
from trading_bot.api_client import (
    CandleQuotaExhaustedError,
    CandleRequestError,
    CandleRequestTiming,
)
from trading_bot.cancellation import OperationCancelled
from trading_bot.config import CandleRequestPolicy

START = datetime(2026, 10, 1, tzinfo=UTC)


def test_second_page_recovers_without_replaying_success_and_keeps_rpc_parameters() -> None:
    clock = Clock()
    calls: list[dict[str, object]] = []
    times: list[float] = []

    def request(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        times.append(clock.now)
        start = kwargs["from_"]
        assert isinstance(start, datetime)
        if len(calls) == 2:
            raise RequestError(StatusCode.UNAVAILABLE, "sensitive", None)
        return SimpleNamespace(candles=[_fake_historic_candle(100, 10, start)])

    gateway = _gateway_for_request(request, clock, CandleRequestPolicy(min_interval_sec=2))
    result = gateway.get_candles_history(
        instrument_id="ID", timeframe="5m", from_=START, to=START + timedelta(days=3)
    )
    assert [call["from_"] for call in calls] == [START + timedelta(days=n) for n in (0, 1, 1, 2)]
    assert calls[1] == calls[2]
    assert calls[1]["instrument_id"] == "ID"
    assert calls[1]["interval"] == "CANDLE_INTERVAL_5_MIN"
    assert times == [0, 2, 7, 9]
    assert len(result) == 3


@pytest.mark.parametrize(
    "attempts,budget,rpc_seconds,overshoot,expected_calls,expected_waits",
    [
        (1, 120, 0, 0, 1, []),
        (3, 120, 0, 0, 3, [5, 5]),
        (10, 9, 0, 0, 2, [5]),
        (10, 10, 6, 0, 1, []),
        (10, 5, 0, 1, 1, [5]),
    ],
)
def test_persistent_unavailable_is_bounded_and_never_requests_later_page(
    attempts: int,
    budget: float,
    rpc_seconds: float,
    overshoot: float,
    expected_calls: int,
    expected_waits: list[float],
) -> None:
    class OvershootClock(Clock):
        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            self.now += overshoot

    clock = OvershootClock()
    calls: list[object] = []

    def request(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs["from_"])
        if kwargs["from_"] == START:
            clock.now += 1  # Successful page consumes the minimum RPC spacing.
            return SimpleNamespace(candles=[_fake_historic_candle(100, 10, START)])
        clock.now += rpc_seconds
        raise RequestError(StatusCode.UNAVAILABLE, "sensitive", None)

    gateway = _gateway_for_request(
        request,
        clock,
        CandleRequestPolicy(min_interval_sec=1, max_attempts=attempts, max_retry_wait_sec=budget),
    )
    with pytest.raises(CandleRequestError) as error:
        gateway.get_candles_history(
            instrument_id="ID", timeframe="5m", from_=START, to=START + timedelta(days=3)
        )
    assert not isinstance(error.value, CandleQuotaExhaustedError)
    assert calls == [START] + [START + timedelta(days=1)] * expected_calls
    assert clock.waits == expected_waits


@pytest.mark.parametrize("first_quota", [False, True])
@pytest.mark.parametrize("attempts,budget", [(2, 120), (10, 9)])
def test_quota_total_attempt_ceiling_counts_unavailable_with_independent_waits(
    first_quota: bool, attempts: int, budget: float
) -> None:
    clock = Clock()
    codes = [StatusCode.RESOURCE_EXHAUSTED, StatusCode.UNAVAILABLE]
    if not first_quota:
        codes.reverse()
    calls = 0

    def request(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        code = codes[calls % 2]
        calls += 1
        raise RequestError(code, "sensitive", None)

    gateway = _gateway_for_request(
        request,
        clock,
        CandleRequestPolicy(max_attempts=attempts, quota_max_attempts=2, max_retry_wait_sec=budget),
    )
    with pytest.raises(CandleRequestError) as error:
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START)
    assert isinstance(error.value, CandleQuotaExhaustedError) is (not first_quota)
    assert calls == 2
    assert clock.waits == ([1] if first_quota else [5])


def test_pacing_is_included_in_unavailable_page_budget() -> None:
    clock = Clock()
    calls = 0

    def request(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        raise RequestError(StatusCode.UNAVAILABLE, "sensitive", None)

    gateway = _gateway_for_request(
        request, clock, CandleRequestPolicy(min_interval_sec=20, max_retry_wait_sec=10)
    )
    with pytest.raises(CandleRequestError) as error:
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START)
    assert not isinstance(error.value, CandleQuotaExhaustedError)
    assert calls == 1
    assert clock.waits == []


@pytest.mark.parametrize("quota", [False])
@pytest.mark.parametrize("retry_base,min_interval", [(20, 1), (5, 20)])
def test_exhausted_cooldown_keeps_its_error_class_for_later_calls(
    quota: bool, retry_base: float, min_interval: float
) -> None:
    clock = Clock()
    times: list[float] = []

    def request(**kwargs: object) -> SimpleNamespace:
        times.append(clock.now)
        if len(times) == 1:
            code = StatusCode.RESOURCE_EXHAUSTED if quota else StatusCode.UNAVAILABLE
            raise RequestError(code, "sensitive", None)
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(
        request,
        clock,
        CandleRequestPolicy(
            retry_base_sec=retry_base, min_interval_sec=min_interval, max_retry_wait_sec=10
        ),
    )
    for instrument_id in ("ID", "OTHER"):
        with pytest.raises(CandleRequestError) as error:
            gateway.get_candles(instrument_id=instrument_id, timeframe="5m", from_=START)
        assert isinstance(error.value, CandleQuotaExhaustedError) is quota
    assert times == [0]
    assert clock.waits == []

    clock.now = 15
    assert gateway.get_candles(instrument_id="OTHER", timeframe="5m", from_=START) == []
    assert times == [0, 20]
    assert clock.waits == [5]


class UnavailableCall(QuotaCall):
    """Use the installed SDK wrapper with real grpc.Call UNAVAILABLE and metadata."""

    def code(self) -> StatusCode:
        return StatusCode.UNAVAILABLE


def test_installed_sdk_wrapper_ignores_unavailable_reset_and_sanitizes_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    clock = Clock()
    times: list[float] = []

    @handle_request_error("GetCandles")
    def request(**kwargs: object) -> SimpleNamespace:
        times.append(clock.now)
        raise UnavailableCall()

    gateway = _gateway_for_request(request, clock, CandleRequestPolicy(max_attempts=3))
    with pytest.raises(CandleRequestError) as error:
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=START)
    assert not isinstance(error.value, CandleQuotaExhaustedError)
    assert times == [0, 5, 10]  # Metadata contains quota reset=17, irrelevant here.
    assert clock.waits == [5, 5]
    text = caplog.text + str(error.value)
    assert "private-token" not in text
    assert "sensitive" not in text
    assert "ratelimit" not in text
    events = [r.message for r in caplog.records if "event=candle_request_unavailable " in r.message]
    assert len(events) == 3
    assert "attempt=1 wait_sec=5.0 action=retry_current_page" in events[0]
    assert "attempt=3 wait_sec=5.0 action=stop" in events[-1]
    assert "event=candle_request_quota" not in text


@pytest.mark.parametrize("stage", ["wait", "after_error", "before_retry"])
@pytest.mark.parametrize("interrupt", [False, True])
def test_cancellation_prevents_retry_or_later_pages_without_wrapping(
    stage: str, interrupt: bool, caplog: pytest.LogCaptureFixture
) -> None:
    class CancelClock(Clock):
        stopped = False

        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            if interrupt:
                raise KeyboardInterrupt
            self.stopped = True

        def timing(self) -> CandleRequestTiming:
            return CandleRequestTiming(
                monotonic=self.monotonic, sleep=self.sleep, should_stop=lambda: self.stopped
            )

    clock = CancelClock()
    calls = 0

    def request(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if stage == "after_error":
            if interrupt:
                raise KeyboardInterrupt
            clock.stopped = True
        raise RequestError(
            StatusCode.UNAVAILABLE, "sensitive", Metadata("secret", None, 0, 99, "secret")
        )

    if stage == "before_retry":
        # Stop precisely after retry scheduling, before the next RPC, without sleeping.
        class StopOnRetry(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if "event=candle_request_unavailable " in record.getMessage():
                    if interrupt:
                        raise KeyboardInterrupt
                    clock.stopped = True

        handler = StopOnRetry()
        logging.getLogger("trading_bot.api_client").addHandler(handler)
    else:
        handler = None
    gateway = _gateway_for_request(request, clock)
    try:
        with pytest.raises(KeyboardInterrupt if interrupt else OperationCancelled):
            gateway.get_candles_history(
                instrument_id="ID", timeframe="5m", from_=START, to=START + timedelta(days=3)
            )
    finally:
        if handler is not None:
            logging.getLogger("trading_bot.api_client").removeHandler(handler)
    assert calls == 1
    assert clock.waits == ([5] if stage == "wait" else [])
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
