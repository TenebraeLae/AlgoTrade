"""Moscow-clock schedule for the local ticker regime (L4).

Only closed 15m candles may classify local regimes; unavailable context fails closed.
This module cannot submit orders, close positions, alter risk or deliver VK messages.
Events use a bounded nonblocking outbox; delivery requires an external transport.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from trading_bot.api_client import Candle
from trading_bot.config import DEFAULT_REGIME_REFRESH_MIN
from trading_bot.indicators import adx, atr, ema
from trading_bot.risk_manager import Regime
from trading_bot.strategy import (
    InsufficientHistoryError,
    ScheduledRegime,
    StrategyConfig,
    classify_regime,
)
from trading_bot.utils import (
    MSK,
    SESSION_ACCEPT_ORDERS_START,
    SESSION_CLOSING_AUCTION_START,
    SESSION_TRADING_START,
)

logger = logging.getLogger(__name__)
_BAR_DURATION = timedelta(minutes=15)


@dataclass(frozen=True)
class RegimeObservation:
    """Validated feed history and its independent freshness deadline (exclusive)."""

    candles_15m: list[Candle]
    valid_until: datetime


@dataclass(frozen=True)
class RegimeChanged:
    """Typed local change event; never an instruction to close a position."""

    ticker: str
    previous: Regime
    current: Regime
    slot: datetime
    observed_at: datetime


def _aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("regime clock must be tz-aware")


def _slot(now: datetime, interval: timedelta) -> datetime | None:
    _aware(now)
    local = now.astimezone(MSK)
    if local.weekday() >= 5 or not (
        SESSION_ACCEPT_ORDERS_START <= local.time() < SESSION_CLOSING_AUCTION_START
    ):
        return None
    opening = datetime.combine(local.date(), SESSION_TRADING_START, tzinfo=MSK)
    if local < opening:
        return datetime.combine(local.date(), SESSION_ACCEPT_ORDERS_START, tzinfo=MSK).astimezone(
            UTC
        )
    return (opening + ((local - opening) // interval) * interval).astimezone(UTC)


def _validate_observation(
    observation: RegimeObservation, now: datetime, config: StrategyConfig
) -> None:
    minimum = max(config.ema_slow, 2 * config.adx_period, config.atr_period + 1)
    if len(observation.candles_15m) < minimum:
        raise InsufficientHistoryError("insufficient closed 15m regime history")
    previous: datetime | None = None
    for candle in observation.candles_15m:
        _aware(candle.ts_open)
        if candle.is_complete is not True or candle.ts_open + _BAR_DURATION > now:
            raise ValueError("local regime history must be closed")
        if previous is not None and candle.ts_open <= previous:
            raise ValueError("local regime history must be ordered without duplicates")
        prices = (candle.open, candle.high, candle.low, candle.close)
        if (
            any(not price.is_finite() or price <= 0 for price in prices)
            or candle.volume < 0
            or candle.high < max(candle.open, candle.close, candle.low)
            or candle.low > min(candle.open, candle.close, candle.high)
        ):
            raise ValueError("invalid local regime OHLCV")
        previous = candle.ts_open


def _classify(observation: RegimeObservation, config: StrategyConfig) -> Regime:
    candles = observation.candles_15m
    closes = [candle.close for candle in candles]
    values = (
        adx(candles, config.adx_period)[-1],
        atr(candles, config.atr_period)[-1],
        ema(closes, config.ema_fast)[-1],
        ema(closes, config.ema_mid)[-1],
        ema(closes, config.ema_slow)[-1],
    )
    if any(value is None for value in values) or closes[-1] <= 0:
        raise InsufficientHistoryError("insufficient closed 15m regime history")
    adx_value, atr_value, fast, mid, slow = values
    assert adx_value is not None and atr_value is not None
    assert fast is not None and mid is not None and slow is not None
    return classify_regime(
        adx_value=adx_value,
        atr_ratio=atr_value / closes[-1],
        ema_fast=fast,
        ema_mid=mid,
        ema_slow=slow,
        config=config,
    )


class LocalRegimeSchedule:
    """Classify once per Moscow slot, revalidate source availability on every poll.

    A bounded in-memory notification seam cannot block trading on network delivery.
    `drain_events` transfers responsibility to a future transport, not proof of delivery.
    """

    def __init__(
        self,
        config: StrategyConfig,
        *,
        refresh_minutes: int = DEFAULT_REGIME_REFRESH_MIN,
        notification_capacity: int = 256,
    ) -> None:
        if notification_capacity < 1:
            raise ValueError("notification_capacity must be positive")
        if refresh_minutes <= 0:
            raise ValueError("refresh_minutes must be positive")
        self._config = config
        self._refresh_minutes = refresh_minutes
        self._interval = timedelta(minutes=refresh_minutes)
        self._states: dict[str, ScheduledRegime] = {}
        self._last_regime: dict[str, Regime] = {}
        self._events: deque[RegimeChanged] = deque(maxlen=notification_capacity)

    def current(self, ticker: str, now: datetime) -> ScheduledRegime:
        """Return fail-closed context when there is no current fresh classification."""
        slot = _slot(now, self._interval)
        state = self._states.get(ticker)
        if state is not None and state.usable_for(ticker, now):
            return state
        return ScheduledRegime(
            ticker, None, slot, now.astimezone(UTC), now.astimezone(UTC), "regime unavailable"
        )

    def update(
        self, ticker: str, now: datetime, observation: RegimeObservation | None
    ) -> ScheduledRegime:
        """Observe availability first; never replay missed slots or retain stale permission."""
        slot = _slot(now, self._interval)
        previous = self._states.get(ticker)
        reason = "outside regime schedule"
        if slot is not None:
            reason = "regime source unavailable or expired"
        if observation is not None:
            _aware(observation.valid_until)
        if slot is None or observation is None or observation.valid_until <= now:
            state = ScheduledRegime(
                ticker, None, slot, now.astimezone(UTC), now.astimezone(UTC), reason
            )
        else:
            opening = datetime.combine(now.astimezone(MSK).date(), SESSION_TRADING_START, MSK)
            end = opening if slot < opening else slot + self._interval
            end = min(end, observation.valid_until)
            try:
                _validate_observation(observation, now, self._config)
                cached = (
                    previous.regime
                    if previous is not None
                    and previous.slot == slot
                    and previous.observed_at <= now
                    else None
                )
                regime = cached or _classify(observation, self._config)
            except (ValueError, ArithmeticError) as exc:
                logger.warning(
                    "event=local_regime_unavailable ticker=%s ts=%s error_type=%s action=pause",
                    ticker,
                    now.isoformat(),
                    type(exc).__name__,
                )
                state = ScheduledRegime(
                    ticker, None, slot, now.astimezone(UTC), now.astimezone(UTC), "bad history"
                )
            else:
                state = ScheduledRegime(
                    ticker=ticker,
                    regime=regime,
                    slot=slot,
                    observed_at=now.astimezone(UTC),
                    valid_until=end.astimezone(UTC),
                    reason="scheduled",
                    refresh_minutes=self._refresh_minutes,
                )
                old = self._last_regime.get(ticker)
                if old is not None and old != regime:
                    event = RegimeChanged(ticker, old, regime, slot, now.astimezone(UTC))
                    logger.info(
                        "event=local_regime_changed ticker=%s previous=%s current=%s "
                        "slot=%s ts=%s transport=unavailable",
                        ticker,
                        old,
                        regime,
                        slot.isoformat(),
                        now.isoformat(),
                    )
                    if len(self._events) == self._events.maxlen:
                        logger.warning(
                            "event=local_regime_notification_overflow ticker=%s ts=%s "
                            "transport=unavailable action=drop_notification",
                            ticker,
                            now.isoformat(),
                        )
                    else:
                        self._events.append(event)
                self._last_regime[ticker] = regime
        self._states[ticker] = state
        return state

    def drain_events(self) -> tuple[RegimeChanged, ...]:
        """Drain the nonblocking outbox; no transport or delivery is implied."""
        events = tuple(self._events)
        self._events.clear()
        return events
