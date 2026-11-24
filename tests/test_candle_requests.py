"""GetCandles pagination, quota recovery and shared per-RPC pacing, without network."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from grpc import StatusCode
from tinkoff.invest.exceptions import RequestError
from tinkoff.invest.logging import Metadata

from tests.test_api_client import _fake_historic_candle, _FakeClient
from trading_bot.api_client import (
    CandleQuotaExhaustedError,
    CandleRequestError,
    CandleRequestTiming,
    InvestGateway,
)
from trading_bot.config import CandleRequestPolicy


class Clock:
    """Advance monotonic time only when the gateway asks to sleep."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds

    def timing(self) -> CandleRequestTiming:
        return CandleRequestTiming(monotonic=self.monotonic, sleep=self.sleep)


class PageClient(_FakeClient):
    """Fail once on page two, as a real SDK wrapped quota error would do."""

    def __init__(self) -> None:
        super().__init__([], None)
        self.start = datetime(2026, 10, 1, 12, 2, tzinfo=UTC)
        self.calls: list[dict[str, object]] = []
        self.failed = False
        self.clock = Clock()
        self.times: list[float] = []

    @property
    def market_data(self) -> SimpleNamespace:
        def get_candles(**kwargs: object) -> SimpleNamespace:
            self.calls.append(kwargs)
            self.times.append(self.clock.now)
            start = kwargs["from_"]
            assert isinstance(start, datetime)
            if start > self.start and not self.failed:
                self.failed = True
                raise RequestError(
                    StatusCode.RESOURCE_EXHAUSTED,
                    "sensitive SDK details",
                    Metadata("sensitive-tracking", "100", 0, 2, "sensitive message"),
                )
            return SimpleNamespace(candles=[_fake_historic_candle(100, 10, start)])

        return SimpleNamespace(get_candles=get_candles)

    def get_all_candles(self, **kwargs: object) -> list[SimpleNamespace]:
        # Existing implementation delegates a generator to SDK; exhaustion mid-history
        # closes the generator. Restarting it would replay the successful first page.
        result: list[SimpleNamespace] = []
        for offset in range(3):
            page = dict(kwargs, from_=self.start + timedelta(days=offset))
            result.extend(self.market_data.get_candles(**page).candles)
        return result


def test_resource_exhaustion_mid_history_retries_only_current_page() -> None:
    client = PageClient()
    gateway = InvestGateway(
        "private-token",
        target="fake",
        client_factory=lambda *_: client,
        candle_policy=CandleRequestPolicy(min_interval_sec=1),
        candle_timing=client.clock.timing(),
    )

    candles = gateway.get_candles_history(
        instrument_id="ID", timeframe="5m", from_=client.start, to=client.start + timedelta(days=3)
    )

    starts = [call["from_"] for call in client.calls]
    assert starts == [client.start + timedelta(days=n) for n in (0, 1, 1, 2)]
    assert len(candles) == 3
    assert client.times == [0.0, 1.0, 3.0, 4.0]
    assert client.calls[2]["to"] == client.calls[3]["from_"]


def _gateway_for_request(
    request: Callable[..., SimpleNamespace],
    clock: Clock,
    policy: CandleRequestPolicy | None = None,
) -> InvestGateway:
    client = SimpleNamespace(market_data=SimpleNamespace(get_candles=request))

    class Context:
        def __enter__(self) -> SimpleNamespace:
            return client

        def __exit__(self, *args: object) -> bool:
            return False

    return InvestGateway(
        "private-token",
        target="fake",
        client_factory=lambda *_: Context(),
        candle_policy=policy or CandleRequestPolicy(min_interval_sec=1),
        candle_timing=clock.timing(),
    )


def test_every_page_and_direct_runtime_request_share_rpc_pacing() -> None:
    clock = Clock()
    times: list[float] = []

    def request(**kwargs: object) -> SimpleNamespace:
        times.append(clock.now)
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(request, clock)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    gateway.get_candles(instrument_id="runtime", timeframe="15m", from_=start)
    gateway.get_candles_history(
        instrument_id="history", timeframe="5m", from_=start, to=start + timedelta(days=3)
    )
    gateway.get_candles(instrument_id="runtime", timeframe="1h", from_=start)
    assert times == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("hint,expected", [(None, 1), (0, 1), (-1, 1), (17, 17)])
def test_retry_honors_relative_reset_or_uses_fallback(hint: int | None, expected: int) -> None:
    clock = Clock()
    calls = 0

    def request(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if calls == 1:
            metadata = None if hint is None else Metadata("secret", None, 0, hint, "secret")
            raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "secret", metadata)
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(request, clock)
    assert (
        gateway.get_candles(
            instrument_id="ID", timeframe="5m", from_=datetime(2026, 10, 1, tzinfo=UTC)
        )
        == []
    )
    assert calls == 2
    assert clock.waits == [expected]


@pytest.mark.parametrize(
    "hint,max_attempts,budget,expected_calls", [(None, 3, 120, 3), (121, 4, 120, 4), (8, 4, 10, 4)]
)
def test_exhaustion_stops_without_partial_history_or_clipping_hint(
    hint: int | None,
    max_attempts: int,
    budget: float,
    expected_calls: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = Clock()
    calls = 0

    def request(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        raise RequestError(
            StatusCode.RESOURCE_EXHAUSTED,
            "private-token sensitive details",
            Metadata("sensitive-tracking", "1", 0, hint, "sensitive message"),
        )

    gateway = _gateway_for_request(
        request,
        clock,
        CandleRequestPolicy(quota_max_attempts=max_attempts, max_retry_wait_sec=budget),
    )
    with pytest.raises(CandleQuotaExhaustedError) as error:
        gateway.get_candles_history(
            instrument_id="ID",
            timeframe="5m",
            from_=datetime(2026, 10, 1, tzinfo=UTC),
            to=datetime(2026, 10, 4, tzinfo=UTC),
        )
    assert calls == expected_calls
    assert clock.now == (expected_calls - 1) * max(1, hint or 0)
    assert "private-token" not in caplog.text + str(error.value)
    assert "sensitive" not in caplog.text + str(error.value)
    assert "action=stop" in caplog.text


@pytest.mark.parametrize(
    "code",
    [
        StatusCode.UNAUTHENTICATED,
        StatusCode.PERMISSION_DENIED,
        StatusCode.INTERNAL,
        StatusCode.NOT_FOUND,
    ],
)
def test_nonquota_sdk_errors_stop_immediately_without_sleep(code: StatusCode) -> None:
    clock = Clock()
    calls = 0

    def request(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        raise RequestError(code, "sensitive", None)

    gateway = _gateway_for_request(request, clock)
    with pytest.raises(CandleRequestError):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=datetime.now(tz=UTC))
    assert calls == 1
    assert not clock.waits


def test_keyboard_interrupt_during_quota_wait_is_not_swallowed() -> None:
    class InterruptedClock(Clock):
        def sleep(self, seconds: float) -> None:
            raise KeyboardInterrupt

    clock = InterruptedClock()

    def request(**kwargs: object) -> SimpleNamespace:
        raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "sensitive", None)

    gateway = _gateway_for_request(request, clock)
    with pytest.raises(KeyboardInterrupt):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=datetime.now(tz=UTC))


@pytest.mark.parametrize("timeframe,days", [("5m", 1), ("15m", 1), ("1h", 7)])
def test_unaligned_pages_overlap_deduplicate_and_filter_requested_range(
    timeframe: str,
    days: int,
) -> None:
    clock = Clock()
    start = datetime(2026, 10, 1, 12, 2, 7, tzinfo=UTC)
    boundary = start + timedelta(days=days)
    end = boundary + timedelta(minutes=17)
    times = [start - timedelta(minutes=5), start, boundary, end, end + timedelta(minutes=5)]
    candles = [_fake_historic_candle(100, 10, ts) for ts in times]
    calls: list[tuple[object, object]] = []

    def request(**kwargs: object) -> SimpleNamespace:
        calls.append((kwargs["from_"], kwargs["to"]))
        return SimpleNamespace(candles=list(reversed(candles)))

    gateway = _gateway_for_request(request, clock)
    result = gateway.get_candles_history(
        instrument_id="ID",
        timeframe=timeframe,
        from_=start,
        to=end,
    )
    assert calls == [(start, boundary), (boundary, end)]
    assert [c.ts_open for c in result] == [start, boundary, end]


def test_exact_page_boundary_does_not_add_zero_length_rpc() -> None:
    clock = Clock()
    calls: list[dict[str, object]] = []

    def request(**kwargs: object) -> SimpleNamespace:
        calls.append(kwargs)
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(request, clock)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    assert (
        gateway.get_candles_history(
            instrument_id="ID", timeframe="5m", from_=start, to=start + timedelta(days=1)
        )
        == []
    )
    assert len(calls) == 1


def test_sdk_candle_error_logging_does_not_leak_details_or_metadata(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from tinkoff.invest.logging import log_error

    clock = Clock()

    def request(**kwargs: object) -> SimpleNamespace:
        # Installed _errors.handle_request_error logs this before raising.
        log_error("private-token", "GetCandles", "private-token sensitive SDK text")
        raise RequestError(StatusCode.UNAUTHENTICATED, "private-token", None)

    gateway = _gateway_for_request(request, clock)
    with pytest.raises(CandleRequestError):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=datetime.now(tz=UTC))
    assert "private-token" not in caplog.text
    assert "sensitive SDK text" not in caplog.text
    assert "candle_request_failed" in caplog.text


def test_exhausted_second_page_does_not_replay_success_or_request_later_pages() -> None:
    clock = Clock()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    starts: list[object] = []

    def request(**kwargs: object) -> SimpleNamespace:
        starts.append(kwargs["from_"])
        if kwargs["from_"] != start:
            raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "sensitive", None)
        return SimpleNamespace(candles=[_fake_historic_candle(100, 10, start)])

    gateway = _gateway_for_request(request, clock, CandleRequestPolicy(quota_max_attempts=3))
    with pytest.raises(CandleQuotaExhaustedError):
        gateway.get_candles_history(
            instrument_id="ID", timeframe="5m", from_=start, to=start + timedelta(days=3)
        )
    assert starts == [start, *([start + timedelta(days=1)] * 3)]


def test_quota_sleep_overshoot_does_not_consume_transport_deadline() -> None:
    class SlowClock(Clock):
        def sleep(self, seconds: float) -> None:
            self.now += seconds + 1

    clock = SlowClock()
    calls = 0

    def request(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "sensitive", None)

    gateway = _gateway_for_request(
        request, clock, CandleRequestPolicy(max_retry_wait_sec=1, quota_max_attempts=2)
    )
    with pytest.raises(CandleQuotaExhaustedError):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=datetime.now(tz=UTC))
    assert calls == 2


def test_reset_larger_than_page_budget_does_not_poison_shared_gateway() -> None:
    """Exhaust the quota count, retaining full server cooldown for the next request."""
    clock = Clock()
    calls: list[float] = []

    def request(**kwargs: object) -> SimpleNamespace:
        calls.append(clock.now)
        if len(calls) == 1:
            raise RequestError(
                StatusCode.RESOURCE_EXHAUSTED,
                "sensitive",
                Metadata("secret", None, 0, 20, "secret"),
            )
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(
        request, clock, CandleRequestPolicy(max_retry_wait_sec=10, quota_max_attempts=1)
    )
    start = datetime(2026, 10, 1, tzinfo=UTC)
    with pytest.raises(CandleQuotaExhaustedError):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=start)
    assert calls == [0.0]
    assert clock.waits == []
    assert gateway.get_candles(instrument_id="OTHER", timeframe="5m", from_=start) == []
    assert calls == [0.0, 20.0]
    assert clock.waits == [20.0]


def test_conflicting_timestamp_duplicates_are_not_silently_replaced() -> None:
    clock = Clock()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    calls = 0

    def request(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(candles=[_fake_historic_candle(100 + calls, 10, start)])

    gateway = _gateway_for_request(request, clock)
    with pytest.raises(CandleRequestError, match="conflicting"):
        gateway.get_candles_history(
            instrument_id="ID", timeframe="5m", from_=start, to=start + timedelta(days=2)
        )
