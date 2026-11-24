"""Exercise the installed SDK error wrapper with an offline gRPC Call failure."""

from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from grpc import Call, RpcError, StatusCode
from tinkoff.invest._errors import handle_request_error

from tests.test_candle_requests import Clock, _gateway_for_request
from trading_bot.api_client import CandleQuotaExhaustedError, CandleRequestTiming
from trading_bot.cancellation import OperationCancelled
from trading_bot.config import CandleRequestPolicy


class QuotaCall(RpcError, Call):
    """Local gRPC Call matching the SDK's real wrapper branch, without a channel."""

    def initial_metadata(self) -> tuple[SimpleNamespace, ...]:
        return (
            SimpleNamespace(key="x-tracking-id", value="private-token"),
            SimpleNamespace(key="x-ratelimit-limit", value="100"),
            SimpleNamespace(key="x-ratelimit-remaining", value="0"),
            SimpleNamespace(key="x-ratelimit-reset", value="17"),
            SimpleNamespace(key="message", value="private-token"),
        )

    def trailing_metadata(self) -> tuple[SimpleNamespace, ...]:
        return ()

    def code(self) -> StatusCode:
        return StatusCode.RESOURCE_EXHAUSTED

    def details(self) -> str:
        return "private-token sensitive SDK details"

    def is_active(self) -> bool:
        return False

    def time_remaining(self) -> float:
        return 0.0

    def cancel(self) -> bool:
        return False

    def add_callback(self, callback: Callable[[], object]) -> bool:
        return False


def test_actual_sdk_wrapper_metadata_drives_bounded_retry_without_log_leak(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    times: list[float] = []

    @handle_request_error("GetCandles")
    def request(**kwargs: object) -> SimpleNamespace:
        times.append(clock.now)
        raise QuotaCall()

    gateway = _gateway_for_request(request, clock, CandleRequestPolicy(quota_max_attempts=2))
    with pytest.raises(CandleQuotaExhaustedError):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=datetime.now(tz=UTC))

    assert times == [0, 17]
    assert clock.waits == [17]
    assert "private-token" not in caplog.text
    assert "sensitive SDK" not in caplog.text
    assert "retry_current_page" in caplog.text and "action=stop" in caplog.text


def test_sdk_quota_retry_stops_when_sleep_requests_cancellation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class StoppedClock(Clock):
        stopped = False

        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            self.stopped = True

        def timing(self) -> CandleRequestTiming:
            return CandleRequestTiming(
                monotonic=self.monotonic, sleep=self.sleep, should_stop=lambda: self.stopped
            )

    clock = StoppedClock()
    times: list[float] = []

    @handle_request_error("GetCandles")
    def request(**kwargs: object) -> SimpleNamespace:
        times.append(clock.now)
        raise QuotaCall()

    gateway = _gateway_for_request(request, clock)
    with pytest.raises(OperationCancelled):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=datetime.now(tz=UTC))
    assert times == [0]
    assert clock.waits == [17]
    assert "private-token" not in caplog.text
    assert "sensitive SDK" not in caplog.text
    assert "error_type=OperationCancelled" not in caplog.text
