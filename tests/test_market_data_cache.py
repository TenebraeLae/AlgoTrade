"""Тесты market_data.CandleCache: TTL по таймфрейму, сеть не идёт повторно."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_bot.api_client import InvestGateway
from trading_bot.market_data import CandleCache

_TS = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def _counting_gateway() -> tuple[InvestGateway, list[int]]:
    calls: list[int] = []

    def factory(token: str, target: str) -> object:
        class _Client:
            def __enter__(self) -> _Client:
                return self

            def __exit__(self, *exc_info: object) -> bool:
                return False

            @property
            def market_data(self) -> object:
                def get_candles(**kwargs: object) -> object:
                    calls.append(1)
                    from types import SimpleNamespace

                    return SimpleNamespace(candles=[])

                from types import SimpleNamespace

                return SimpleNamespace(get_candles=get_candles)

        return _Client()

    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=factory)
    return gateway, calls


def test_second_request_within_1h_ttl_does_not_hit_network() -> None:
    gateway, calls = _counting_gateway()
    now = {"value": _TS}
    cache = CandleCache(gateway, clock=lambda: now["value"])

    cache.get_candles(instrument_id="BBG004730N88", timeframe="1h", from_=_TS)
    now["value"] = _TS + timedelta(minutes=30)
    cache.get_candles(instrument_id="BBG004730N88", timeframe="1h", from_=_TS)

    assert calls == [1]


def test_request_after_ttl_expiry_hits_network_again() -> None:
    gateway, calls = _counting_gateway()
    now = {"value": _TS}
    cache = CandleCache(gateway, clock=lambda: now["value"])

    cache.get_candles(instrument_id="BBG004730N88", timeframe="1h", from_=_TS)
    now["value"] = _TS + timedelta(hours=1, seconds=1)
    cache.get_candles(instrument_id="BBG004730N88", timeframe="1h", from_=_TS)

    assert calls == [1, 1]


def test_different_timeframes_are_cached_independently() -> None:
    gateway, calls = _counting_gateway()
    cache = CandleCache(gateway, clock=lambda: _TS)

    cache.get_candles(instrument_id="BBG004730N88", timeframe="5m", from_=_TS)
    cache.get_candles(instrument_id="BBG004730N88", timeframe="1h", from_=_TS)

    assert calls == [1, 1]
