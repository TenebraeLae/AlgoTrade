"""Тесты market_data.RateLimiter: приоритеты, порог 80% для несрочных запросов."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_bot.market_data import Priority, RateLimiter

_TS = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def test_urgent_requests_pass_until_capacity_fully_exhausted() -> None:
    limiter = RateLimiter(capacity=10, refill_period=timedelta(minutes=1), clock=lambda: _TS)

    for _ in range(10):
        assert limiter.try_acquire(Priority.POSITIONS) is True
    assert limiter.try_acquire(Priority.POSITIONS) is False


def test_non_urgent_requests_are_cut_at_80_percent_budget_used() -> None:
    limiter = RateLimiter(capacity=10, refill_period=timedelta(minutes=1), clock=lambda: _TS)

    # Съедаем срочными запросами 8 из 10 токенов -> использовано 80%.
    for _ in range(8):
        assert limiter.try_acquire(Priority.ORDERS) is True

    assert limiter.try_acquire(Priority.CANDLES) is False
    assert limiter.try_acquire(Priority.POOL) is False
    # Срочные запросы всё ещё проходят, пока есть токены.
    assert limiter.try_acquire(Priority.POSITIONS) is True


def test_non_urgent_requests_pass_below_80_percent_threshold() -> None:
    limiter = RateLimiter(capacity=10, refill_period=timedelta(minutes=1), clock=lambda: _TS)

    for _ in range(7):
        assert limiter.try_acquire(Priority.ORDERS) is True

    assert limiter.try_acquire(Priority.CANDLES) is True


def test_bucket_refills_after_period() -> None:
    now = {"value": _TS}
    limiter = RateLimiter(
        capacity=2, refill_period=timedelta(minutes=1), clock=lambda: now["value"]
    )

    assert limiter.try_acquire(Priority.POSITIONS) is True
    assert limiter.try_acquire(Priority.POSITIONS) is True
    assert limiter.try_acquire(Priority.POSITIONS) is False

    now["value"] = _TS + timedelta(minutes=1, seconds=1)

    assert limiter.try_acquire(Priority.POSITIONS) is True


def test_capacity_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError):
        RateLimiter(capacity=0, refill_period=timedelta(minutes=1))
