"""Offline adapter contract for cooperatively staged low-priority history pages."""

import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from grpc import StatusCode  # type: ignore[import-untyped]  # SDK dependency, no stubs
from tinkoff.invest.exceptions import RequestError

from tests.test_api_client import _fake_historic_candle
from tests.test_auto_bootstrap import _share
from tests.test_candle_requests import Clock, _gateway_for_request
from tests.test_gateway_cancellation import StopState, gateway_for
from trading_bot.api_client import (
    CandleQuotaExhaustedError,
    CandleRequestError,
    InvestGateway,
    Timeframe,
)
from trading_bot.cancellation import OperationCancelled
from trading_bot.config import CandleRequestPolicy

START = datetime(2026, 10, 1, 12, 2, tzinfo=UTC)


def test_next_fetches_one_page_and_runtime_rpc_can_interleave_with_shared_pacing() -> None:
    clock = Clock()
    calls: list[tuple[object, object, float]] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        calls.append((kwargs["instrument_id"], kwargs["from_"], clock.now))
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(rpc, clock)
    pages = gateway.iter_candle_pages(
        instrument_id="pool", timeframe="5m", from_=START, to=START + timedelta(days=2)
    )
    assert calls == []
    assert next(pages) == []
    assert calls == [("pool", START, 0)]
    gateway.get_candles(instrument_id="runtime", timeframe="5m", from_=START)
    assert next(pages) == []
    assert calls == [
        ("pool", START, 0),
        ("runtime", START, 1),
        ("pool", START + timedelta(days=1), 2),
    ]
    with pytest.raises(StopIteration):
        next(pages)
    assert len(calls) == 3


@pytest.mark.parametrize("timeframe,days", [("5m", 1), ("15m", 1), ("1h", 7)])
def test_pages_sort_filter_and_deduplicate_including_adjacent_boundaries(
    timeframe: Timeframe, days: int
) -> None:
    boundary = START + timedelta(days=days)
    end = boundary + timedelta(minutes=10)
    calls: list[tuple[object, object]] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        calls.append((kwargs["from_"], kwargs["to"]))
        times = (
            [boundary, START, START, START - timedelta(minutes=5)]
            if len(calls) == 1
            else [end + timedelta(minutes=5), end, boundary]
        )
        return SimpleNamespace(candles=[_fake_historic_candle(100, 10, ts) for ts in times])

    gateway = _gateway_for_request(rpc, Clock())
    pages = list(
        gateway.iter_candle_pages(instrument_id="ID", timeframe=timeframe, from_=START, to=end)
    )
    assert [[c.ts_open for c in page] for page in pages] == [[START, boundary], [end]]
    assert calls == [(START, boundary), (boundary, end)]


@pytest.mark.parametrize("quota", [False, True])
def test_midstream_failure_raises_without_replaying_success_or_requesting_later_pages(
    quota: bool,
) -> None:
    starts: list[object] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        starts.append(kwargs["from_"])
        if len(starts) > 1:
            code = StatusCode.RESOURCE_EXHAUSTED if quota else StatusCode.UNAVAILABLE
            raise RequestError(code, "offline failure", None)
        return SimpleNamespace(candles=[_fake_historic_candle(100, 10, START)])

    gateway = _gateway_for_request(
        rpc, Clock(), CandleRequestPolicy(max_attempts=2, quota_max_attempts=2)
    )
    pages = gateway.iter_candle_pages(
        instrument_id="ID", timeframe="5m", from_=START, to=START + timedelta(days=3)
    )
    assert [c.ts_open for c in next(pages)] == [START]
    error = CandleQuotaExhaustedError if quota else CandleRequestError
    with pytest.raises(error) as raised:
        next(pages)
    assert isinstance(raised.value, CandleQuotaExhaustedError) is quota
    # Each transient status has its own configured two-attempt budget; neither replays
    # the successful first page or advances to the third page on exhaustion.
    assert starts == [START] + [START + timedelta(days=1)] * 2
    with pytest.raises(StopIteration):
        next(pages)


def test_quota_retries_only_current_page_then_yields_control() -> None:
    starts: list[object] = []

    def rpc(**kwargs: object) -> SimpleNamespace:
        starts.append(kwargs["from_"])
        if len(starts) == 2:
            raise RequestError(StatusCode.RESOURCE_EXHAUSTED, "offline failure", None)
        return SimpleNamespace(candles=[])

    gateway = _gateway_for_request(rpc, Clock())
    pages = gateway.iter_candle_pages(
        instrument_id="ID", timeframe="5m", from_=START, to=START + timedelta(days=3)
    )
    assert next(pages) == []
    assert next(pages) == []
    assert starts == [START, START + timedelta(days=1), START + timedelta(days=1)]


@pytest.mark.parametrize("adjacent", [False, True])
def test_conflicting_duplicates_stop_before_yielding_bad_page(adjacent: bool) -> None:
    calls = 0
    boundary = START + timedelta(days=1)

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        candles = [_fake_historic_candle(100, 10, boundary)]
        if adjacent and calls > 1:
            candles = [_fake_historic_candle(101, 10, boundary)]
        elif not adjacent:
            candles.append(_fake_historic_candle(101, 10, boundary))
        return SimpleNamespace(candles=candles)

    gateway = _gateway_for_request(rpc, Clock())
    pages = gateway.iter_candle_pages(
        instrument_id="ID", timeframe="5m", from_=START, to=START + timedelta(days=2)
    )
    if adjacent:
        assert len(next(pages)) == 1
    with pytest.raises(CandleRequestError, match="conflicting"):
        next(pages)
    assert calls == (2 if adjacent else 1)


@pytest.mark.parametrize("stage", ["before", "rpc", "exit", "between", "before_yield"])
def test_cancellation_prevents_next_rpc_or_yield(stage: str) -> None:
    state = StopState()
    calls = 0

    def rpc(**kwargs: object) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        state.now += 1
        if stage == "rpc":
            state.stopped = True
        return SimpleNamespace(candles=[])

    gateway, _ = gateway_for(state, rpc, stage)
    pages = gateway.iter_candle_pages(
        instrument_id="ID", timeframe="5m", from_=START, to=START + timedelta(days=2)
    )
    if stage == "between":
        assert next(pages) == []
        state.stopped = True
    elif stage == "before":
        state.stopped = True
    elif stage == "before_yield":

        class StopOnPageLog(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                if "event=candle_history_page" in record.getMessage():
                    state.stopped = True

        log = logging.getLogger("trading_bot.api_client")
        handler = StopOnPageLog()
        previous_level = log.level
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        try:
            with pytest.raises(OperationCancelled):
                next(pages)
        finally:
            log.removeHandler(handler)
            log.setLevel(previous_level)
        assert calls == 1
        return
    with pytest.raises(OperationCancelled):
        next(pages)
    assert calls == (0 if stage == "before" else 1)


@pytest.mark.parametrize(
    "from_,to",
    [
        (START.replace(tzinfo=None), START + timedelta(days=1)),
        (START, (START + timedelta(days=1)).replace(tzinfo=None)),
        (START, START),
        (START, START - timedelta(seconds=1)),
    ],
)
def test_invalid_range_fails_before_rpc(from_: datetime, to: datetime) -> None:
    def rpc(**kwargs: object) -> SimpleNamespace:
        pytest.fail("invalid range must not reach SDK")

    gateway = _gateway_for_request(rpc, Clock())
    with pytest.raises(ValueError):
        list(gateway.iter_candle_pages(instrument_id="ID", timeframe="5m", from_=from_, to=to))


def test_naive_response_timestamp_is_not_yielded() -> None:
    def rpc(**kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(candles=[_fake_historic_candle(100, 10, START.replace(tzinfo=None))])

    gateway = _gateway_for_request(rpc, Clock())
    with pytest.raises(CandleRequestError, match="naive"):
        next(
            gateway.iter_candle_pages(
                instrument_id="ID", timeframe="5m", from_=START, to=START + timedelta(days=1)
            )
        )


def test_unpinned_catalog_is_fresh_filtered_and_preserves_pinned_identity() -> None:
    client = MagicMock()
    client.__enter__.return_value = client
    client.instruments.shares.side_effect = [
        SimpleNamespace(instruments=[_share("GOOD", figi="original")]),
        SimpleNamespace(
            instruments=[_share("GOOD", figi="replacement"), _share("USD", currency="usd")]
        ),
    ]
    gateway = InvestGateway("offline", target="fake", client_factory=lambda *_: client)
    original = gateway.discover_paper_shares().shares["GOOD"]
    pinned = gateway._discovered_shares
    fresh = gateway.discover_paper_shares(pin=False)
    assert fresh.shares["GOOD"].instrument_id == "replacement"
    assert fresh.excluded == {"USD": "not_rub"}
    assert gateway._discovered_shares is pinned
    assert gateway.get_share_metadata(ticker="GOOD") is original
    assert client.instruments.shares.call_count == 2
