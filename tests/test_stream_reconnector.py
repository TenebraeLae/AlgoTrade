"""Тесты market_data.StreamReconnector: backoff, восстановление, отказ после max_attempts."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_bot.api_client import Candle, GatewayError
from trading_bot.market_data import StreamReconnector

_TS = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
_CANDLE = Candle(
    open=Decimal(1),
    high=Decimal(1),
    low=Decimal(1),
    close=Decimal(1),
    volume=1,
    ts_open=_TS,
    is_complete=None,
)


class _FakeGateway:
    """Гейтвей, чей `stream_candles` рвётся N раз, затем отдаёт свечи."""

    def __init__(self, *, failures_before_success: int, candles_after: list[Candle]) -> None:
        self._failures_before_success = failures_before_success
        self._candles_after = candles_after
        self.call_count = 0

    def stream_candles(self, *, instrument_ids: list[str], timeframe: str) -> list[Candle]:
        self.call_count += 1
        if self.call_count <= self._failures_before_success:
            raise GatewayError(f"обрыв связи #{self.call_count}")
        return self._candles_after


def test_reconnects_after_transport_error_and_yields_candles() -> None:
    gateway = _FakeGateway(failures_before_success=2, candles_after=[_CANDLE])
    sleeps: list[float] = []
    reconnector = StreamReconnector(
        gateway,  # type: ignore[arg-type]
        backoff_base_sec=5,
        max_attempts=10,
        sleep=sleeps.append,
    )

    received = list(reconnector.candles(instrument_ids=["BBG004730N88"], timeframe="5m"))

    assert received == [_CANDLE]
    assert sleeps == [5, 10]  # exponential backoff: 5*2^0, 5*2^1
    assert gateway.call_count == 3


def test_gives_up_after_max_attempts() -> None:
    gateway = _FakeGateway(failures_before_success=999, candles_after=[])
    sleeps: list[float] = []
    reconnector = StreamReconnector(
        gateway,  # type: ignore[arg-type]
        backoff_base_sec=1,
        max_attempts=3,
        sleep=sleeps.append,
    )

    with pytest.raises(GatewayError):
        list(reconnector.candles(instrument_ids=["BBG004730N88"], timeframe="5m"))

    assert gateway.call_count == 4  # 1 исходная попытка + 3 повтора
    assert sleeps == [1, 2, 4]


def test_rejects_non_positive_backoff_base() -> None:
    gateway = _FakeGateway(failures_before_success=0, candles_after=[])

    with pytest.raises(ValueError):
        StreamReconnector(gateway, backoff_base_sec=0, max_attempts=10)  # type: ignore[arg-type]


def test_rejects_non_positive_max_attempts() -> None:
    gateway = _FakeGateway(failures_before_success=0, candles_after=[])

    with pytest.raises(ValueError):
        StreamReconnector(gateway, backoff_base_sec=5, max_attempts=0)  # type: ignore[arg-type]
