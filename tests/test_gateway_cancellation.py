"""Cooperative gateway cancellation at offline SDK/context/wait boundaries."""

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from grpc import StatusCode
from tinkoff.invest.exceptions import RequestError

from tests.test_api_client import _fake_historic_candle
from trading_bot.api_client import CandleRequestTiming, InvestGateway
from trading_bot.cancellation import OperationCancelled
from trading_bot.config import CandleRequestPolicy


class StopState:
    """Deterministic handler-like flag: cancellation does not raise in the callback."""

    def __init__(self) -> None:
        self.stopped = False
        self.now = 0.0
        self.waits: list[float] = []

    def should_stop(self) -> bool:
        return self.stopped

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds
        self.stopped = True

    def timing(self) -> CandleRequestTiming:
        return CandleRequestTiming(
            monotonic=lambda: self.now, sleep=self.sleep, should_stop=self.should_stop
        )


def gateway_for(
    state: StopState, rpc: Callable[..., Any], stage: str = "rpc"
) -> tuple[InvestGateway, list[str]]:
    """Build a context whose entry/exit can request cancellation without network."""
    events: list[str] = []
    client = SimpleNamespace(
        instruments=SimpleNamespace(shares=rpc, find_instrument=rpc),
        market_data=SimpleNamespace(get_candles=rpc, get_trading_status=rpc, get_order_book=rpc),
    )

    class Context:
        def __enter__(self) -> SimpleNamespace:
            events.append("enter")
            if stage == "enter":
                state.stopped = True
            return client

        def __exit__(self, *args: object) -> bool:
            events.append("exit")
            if stage == "exit":
                state.stopped = True
            return False

    def factory(*args: object) -> Any:
        events.append("factory")
        if stage == "factory":
            state.stopped = True
        return Context()

    return (
        InvestGateway(
            "offline-token",
            target="fake",
            client_factory=factory,
            candle_policy=CandleRequestPolicy(min_interval_sec=1),
            candle_timing=state.timing(),
        ),
        events,
    )


def invoke(gateway: InvestGateway, method: str) -> object:
    """Exercise catalog, index, metadata and candle public bootstrap methods."""
    start = datetime(2026, 10, 1, tzinfo=UTC)
    if method == "catalog":
        return gateway.discover_paper_shares()
    if method == "index":
        return gateway.resolve_imoex_id()
    if method == "metadata":
        return gateway.get_share_metadata(ticker="SBER")
    if method == "status":
        return gateway.get_trading_status(instrument_id="ID")
    if method == "book":
        return gateway.get_order_book_quote(instrument_id="ID")
    if method == "candles":
        return gateway.get_candles(instrument_id="ID", timeframe="5m", from_=start)
    return gateway.get_candles_history(
        instrument_id="ID", timeframe="5m", from_=start, to=start + timedelta(days=3)
    )


@pytest.mark.parametrize(
    "method", ["catalog", "index", "metadata", "candles", "history", "status", "book"]
)
@pytest.mark.parametrize("stage", ["before", "factory", "enter", "rpc", "exit"])
def test_cancelled_bootstrap_does_not_return_or_reach_next_rpc(
    method: str, stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    state = StopState()
    calls = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if stage == "rpc":
            state.stopped = True
        return SimpleNamespace(instruments=[], candles=[])

    gateway, events = gateway_for(state, rpc, stage)
    state.stopped = stage == "before"
    with pytest.raises(OperationCancelled):
        invoke(gateway, method)
    # A handler returning normally during SDK work must still prevent all later work.
    with pytest.raises(OperationCancelled):
        gateway.get_share_metadata(ticker="NEXT")
    assert calls == (1 if stage in {"rpc", "exit"} else 0)
    expected_events = {
        "before": [],
        "factory": ["factory"],
    }.get(stage, ["factory", "enter", "exit"])
    assert events == expected_events
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.parametrize("quota", [False, True])
def test_stop_flag_during_pacing_or_quota_sleep_prevents_next_rpc(quota: bool) -> None:
    state = StopState()
    calls = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        if quota:
            raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "offline error", None)
        return SimpleNamespace(candles=[])

    gateway, _ = gateway_for(state, rpc)
    with pytest.raises(OperationCancelled):
        invoke(gateway, "history")
    assert calls == 1
    assert state.waits == [1]  # Quota fallback is independent of UNAVAILABLE's 5s base.


@pytest.mark.parametrize("quota", [False, True])
def test_stop_during_failed_rpc_takes_precedence_without_error_log(
    quota: bool, caplog: pytest.LogCaptureFixture
) -> None:
    state = StopState()
    calls = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        state.stopped = True
        code = StatusCode.RESOURCE_EXHAUSTED if quota else StatusCode.UNAVAILABLE
        raise RequestError(code, "offline error", None)

    gateway, _ = gateway_for(state, rpc)
    with pytest.raises(OperationCancelled):
        invoke(gateway, "history")
    assert calls == 1
    assert state.waits == []
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_cancellation_exception_from_sleep_is_not_wrapped_or_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    state = StopState()

    def sleep(seconds: float) -> None:
        raise OperationCancelled

    def rpc(**kwargs: object) -> SimpleNamespace:
        raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "offline error", None)

    gateway, _ = gateway_for(state, rpc)
    gateway._candle_timing = CandleRequestTiming(sleep=sleep)
    with pytest.raises(OperationCancelled):
        invoke(gateway, "candles")
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.parametrize(
    "method", ["catalog", "index", "metadata", "candles", "history", "status", "book"]
)
@pytest.mark.parametrize("raw_cancel", [False, True])
def test_sdk_exception_after_stop_is_not_wrapped_or_logged(
    method: str, raw_cancel: bool, caplog: pytest.LogCaptureFixture
) -> None:
    state = StopState()

    def rpc(**kwargs: object) -> SimpleNamespace:
        if raw_cancel:
            raise OperationCancelled
        state.stopped = True
        raise RuntimeError("offline failure during cancellation")

    gateway, _ = gateway_for(state, rpc)
    with pytest.raises(OperationCancelled):
        invoke(gateway, method)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]


def test_cancelled_second_page_does_not_return_accumulated_history() -> None:
    state = StopState()
    starts: list[object] = []
    start = datetime(2026, 10, 1, tzinfo=UTC)

    def rpc(**kwargs: object) -> SimpleNamespace:
        starts.append(kwargs["from_"])
        state.now += 1
        if len(starts) == 2:
            state.stopped = True
        return SimpleNamespace(candles=[_fake_historic_candle(100, 10, start)])

    gateway, _ = gateway_for(state, rpc)
    with pytest.raises(OperationCancelled):
        invoke(gateway, "history")
    assert starts == [start, start + timedelta(days=1)]
    assert state.waits == []


def test_cached_metadata_and_public_checkpoint_honor_stop() -> None:
    state = StopState()
    gateway, events = gateway_for(state, lambda **kwargs: SimpleNamespace())
    gateway._discovered_shares["SBER"] = SimpleNamespace(ticker="SBER")  # type: ignore[assignment]
    gateway.check_cancelled()
    state.stopped = True
    with pytest.raises(OperationCancelled):
        gateway.check_cancelled()
    with pytest.raises(OperationCancelled):
        gateway.get_share_metadata(ticker="SBER")
    assert events == []
