"""Local regime schedule: deterministic Moscow slots, fail-closed context and events."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.test_strategy import _config
from trading_bot.api_client import Candle
from trading_bot.regime_schedule import LocalRegimeSchedule, RegimeObservation
from trading_bot.utils import MSK


def _at(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=MSK).astimezone(UTC)


def _observation(now: datetime, *, chaos: bool = False) -> RegimeObservation:
    candles = [
        Candle(
            open=Decimal(100 + i),
            close=Decimal(101 + i),
            high=Decimal(102 + i) if not chaos else Decimal(1000 + i),
            low=Decimal(99 + i) if not chaos else Decimal(1),
            volume=100,
            ts_open=now - timedelta(minutes=15 * (70 - i)),
            is_complete=True,
        )
        for i in range(70)
    ]
    return RegimeObservation(candles, now + timedelta(minutes=15))


def test_pre_session_and_exact_half_hour_slots() -> None:
    schedule = LocalRegimeSchedule(_config())
    assert schedule.update("SBER", _at(9, 49), _observation(_at(9, 49))).regime is None
    pre = schedule.update("SBER", _at(9, 50), _observation(_at(9, 50)))
    assert pre.regime == "trending"
    assert pre.slot == _at(9, 50)
    opened = schedule.update("SBER", _at(10), _observation(_at(10)))
    assert opened.slot == _at(10)
    same = schedule.update("SBER", _at(10, 29), _observation(_at(10, 29), chaos=True))
    assert same.regime == "trending"  # classifier is deliberately not per-bar
    boundary = schedule.update("SBER", _at(10, 30), _observation(_at(10, 30), chaos=True))
    assert boundary.regime == "chaos"
    assert boundary.slot == _at(10, 30)


def test_late_start_two_tickers_and_new_date_have_no_replay() -> None:
    schedule = LocalRegimeSchedule(_config())
    first = schedule.update("SBER", _at(14, 17), _observation(_at(14, 17)))
    other = schedule.update("GAZP", _at(14, 17), _observation(_at(14, 17), chaos=True))
    assert first.slot == other.slot == _at(14)
    assert first.regime == "trending" and other.regime == "chaos"
    assert schedule.current("SBER", _at(9, 49, day=3)).regime is None
    tomorrow = schedule.update("SBER", _at(9, 50, day=3), _observation(_at(9, 50, day=3)))
    assert tomorrow.slot == _at(9, 50, day=3)


def test_missing_and_expired_context_never_reuse_permission() -> None:
    schedule = LocalRegimeSchedule(_config())
    now = _at(12, 1)
    observation = _observation(now)
    assert schedule.update("SBER", now, observation).regime == "trending"
    assert schedule.update("SBER", now, None).regime is None
    assert schedule.current("SBER", now).regime is None
    restored = schedule.update("SBER", now, observation)
    assert restored.regime == "trending"
    assert schedule.current("SBER", observation.valid_until).regime is None
    assert schedule.update("SBER", now, replace(observation, candles_15m=[])).regime is None


def test_change_event_queue_is_nonblocking_and_logs_transport_unavailable(
    caplog: pytest.LogCaptureFixture,
) -> None:
    schedule = LocalRegimeSchedule(_config(), notification_capacity=1)
    with caplog.at_level(logging.INFO):
        schedule.update("SBER", _at(12), _observation(_at(12)))
        schedule.update("SBER", _at(12, 30), _observation(_at(12, 30), chaos=True))
        schedule.update("SBER", _at(13), _observation(_at(13)))
    events = schedule.drain_events()
    assert len(events) == 1
    assert events[0].ticker == "SBER"
    assert events[0].previous == "trending" and events[0].current == "chaos"
    assert events[0].observed_at.tzinfo is UTC
    assert "transport=unavailable" in caplog.text
    assert "action=drop_notification" in caplog.text
    assert schedule.current("SBER", _at(13)).regime == "trending"
    assert schedule.drain_events() == ()


def test_naive_clock_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        LocalRegimeSchedule(_config()).current("SBER", datetime(2026, 3, 2))  # noqa: DTZ001


@pytest.mark.parametrize("invalid", ["unclosed", "future", "short", "duplicate", "nan"])
def test_invalid_observation_revokes_same_slot_permission(invalid: str) -> None:
    schedule = LocalRegimeSchedule(_config())
    now = _at(12)
    good = _observation(now)
    assert schedule.update("SBER", now, good).regime == "trending"
    candles = good.candles_15m
    if invalid == "unclosed":
        candles = [replace(c, is_complete=False) for c in candles]
    elif invalid == "future":
        candles = [replace(c, ts_open=now + timedelta(minutes=i)) for i, c in enumerate(candles)]
    elif invalid == "short":
        candles = candles[-1:]
    elif invalid == "duplicate":
        candles = candles + candles[-1:]
    else:
        candles = [replace(candles[-1], close=Decimal("NaN"))]
    assert schedule.update("SBER", now, replace(good, candles_15m=candles)).regime is None
