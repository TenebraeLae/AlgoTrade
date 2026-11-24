"""strategy.py — режимы рынка, сигналы входа, фильтры, journal решений (слой L3,
docs/01_ARCHITECTURE.md, docs/03_STRATEGY.md).

Разрешено: классифицировать режим рынка по индикаторам, проверять условия входа
trending/ranging по закрытым свечам, применять фильтры, которые не покрыты
`risk_manager.py` (защитное окно сессии, гэп, глобальный фильтр рынка), собирать
полный snapshot для journal на каждый сигнал и на каждый отказ. Запрещено: считать
размер позиции (это `risk_manager.py`), знать о брокере/gRPC/VK/дашборде, решать
что-либо по незакрытой свече (docs/03_STRATEGY.md, «Нет заглядывания в будущее»).

Разделение фильтров с `risk_manager.py` (чтобы не дублировать логику и тесты M5):
статус инструмента, спред, дневные лимиты, диверсификация, корреляция и
волатильность проверяются 12 проверками `risk_manager.check()` (docs/04_RISK.md,
docs/15_ACCEPTANCE.md M5). `chaos`/`volatile` блокируются стратегией раньше,
риск-менеджер дополнительно отказывает при `chaos` и превышении ATR/price.
Здесь остаются: защитное окно сессии (перенесено в `utils.can_trade_now` как
параметр `session_guard_min`), гэп при открытии, глобальный фильтр рынка (широта
рынка/IMOEX) и доступность short по инструменту — они не входят в 12 проверок риска.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from trading_bot.api_client import Candle
from trading_bot.config import DEFAULT_REGIME_REFRESH_MIN
from trading_bot.indicators import adx as _adx
from trading_bot.indicators import atr as _atr
from trading_bot.indicators import bollinger as _bollinger
from trading_bot.indicators import ema as _ema
from trading_bot.indicators import macd as _macd
from trading_bot.indicators import rsi as _rsi
from trading_bot.indicators import session_vwap as _session_vwap
from trading_bot.indicators import volume_spike as _volume_spike
from trading_bot.risk_manager import Regime, Side
from trading_bot.utils import (
    MSK,
    SESSION_ACCEPT_ORDERS_START,
    SESSION_TRADING_START,
    session_clock_decision,
)
from trading_bot.utils import session_guard_active as is_regular_session_guard_active

#: Порядок фильтров этого модуля (docs/03_STRATEGY.md, «Фильтры», пункты, не
#: покрытые `risk_manager.check()`; полный список фильтров 1–10 из документа
#: разделён между двумя модулями — см. докстринг файла).
STRATEGY_FILTER_NAMES: tuple[str, ...] = (
    "session_guard",
    "gap_pause",
    "short_availability",
    "market_breadth",
)

#: Обязательные поля journal из docs/03_STRATEGY.md. Если данных не было,
#: поле сохраняется как `n/a` вместе с причиной отказа, а не выдумывается.
INDICATOR_SNAPSHOT_KEYS = frozenset(
    (
        "ema9",
        "ema21",
        "ema50",
        "rsi_5m",
        "rsi_15m",
        "macd_hist",
        "bb_lower",
        "bb_upper",
        "adx",
        "atr",
        "atr_ratio",
        "vwap",
        "volume",
        "volume_avg20",
        "spread_bps",
        "gap_pct",
        "breadth",
        "market_chaos_score",
        "market_chaos_threshold",
        "market_regime",
    )
)


class InsufficientHistoryError(ValueError):
    """Недостаточно закрытых свечей для расчёта хотя бы одного индикатора."""


@dataclass(frozen=True)
class StrategyConfig:
    """Параметры стратегии — только из `config.Settings` (docs/02_CODING_RULES.md:
    «никаких магических чисел в коде стратегии»).
    """

    ema_fast: int
    ema_mid: int
    ema_slow: int
    rsi_period: int
    rsi_oversold: Decimal
    rsi_overbought: Decimal
    rsi_trend_low: Decimal
    rsi_trend_high: Decimal
    macd_fast: int
    macd_slow: int
    macd_signal: int
    bb_period: int
    bb_mult: Decimal
    atr_period: int
    adx_period: int
    adx_trend: Decimal
    adx_range: Decimal
    adx_chaos: Decimal
    max_atr_ratio: Decimal
    vol_window: int
    gap_pause_pct: Decimal
    gap_pause_min: int
    breadth_bearish: Decimal
    breadth_bullish: Decimal
    session_guard_min: int


@dataclass(frozen=True)
class ScheduledRegime:
    """Typed L3 input: local regime bound to a ticker, source freshness and time slot."""

    ticker: str
    regime: Regime | None
    slot: datetime | None
    observed_at: datetime
    valid_until: datetime
    reason: str
    refresh_minutes: int = DEFAULT_REGIME_REFRESH_MIN

    def usable_for(self, ticker: str, now: datetime) -> bool:
        """Reject missing, expired, mismatched and backwards-clock context."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("regime clock must be tz-aware")
        if self.slot is None or self.regime is None:
            return False
        local_slot = self.slot.astimezone(MSK)
        opening = datetime.combine(local_slot.date(), SESSION_TRADING_START, MSK)
        end = (
            opening
            if local_slot.time() == SESSION_ACCEPT_ORDERS_START
            else self.slot + timedelta(minutes=self.refresh_minutes)
        )
        return (
            self.ticker == ticker
            and self.observed_at <= now < self.valid_until
            and self.slot <= now < end
            and local_slot.date() == now.astimezone(MSK).date()
        )


@dataclass(frozen=True)
class Signal:
    """Сигнал на вход с опциональными сырыми уровнями ranging.

    Перед сайзингом стоп округляется наружу по шагу инструмента в слое риска.
    Для trending уровни не задаются: там используются ATR-правила риска.
    """

    ticker: str
    side: Side
    reason: str
    indicators: dict[str, str]
    regime: Regime
    strength: Decimal
    ts: datetime
    stop_price: Decimal | None = None
    take_price: Decimal | None = None


@dataclass(frozen=True)
class Decision:
    """Запись journal — обязательна на каждый сигнал **и** на каждый отказ
    (docs/03_STRATEGY.md, «Journal решения»; пустой snapshot = этап не принят).

    `size_lots`/`trade_id`/`result` из полного контракта `docs/01_ARCHITECTURE.md`
    заполняются позже — `risk_manager.check()` (размер) и `executor.py` (M8, сделка) —
    strategy.py их не знает и не может посчитать (не считает лоты).
    """

    ts: datetime
    ticker: str
    regime: Regime
    side: Side | None
    indicator_snapshot: dict[str, str]
    filters_passed: tuple[str, ...]
    filters_failed: tuple[str, ...]
    verdict: Literal["signal", "no_signal"]
    reason: str
    signal: Signal | None = None


def _decimal_to_str(value: Decimal | None) -> str:
    return "n/a" if value is None else str(value)


def _market_metrics(market_filter: MarketFilterResult | None) -> dict[str, str]:
    """Сериализует только переданные метрики глобального режима рынка."""
    if market_filter is None:
        return {
            "market_chaos_score": "n/a",
            "market_chaos_threshold": "n/a",
            "market_regime": "n/a",
        }
    return {
        "market_chaos_score": _decimal_to_str(market_filter.chaos_score),
        "market_chaos_threshold": _decimal_to_str(market_filter.chaos_threshold),
        "market_regime": market_filter.market_regime or "n/a",
    }


def _unavailable_snapshot(
    *, detail_key: str, reason: str, market_filter: MarketFilterResult | None = None
) -> dict[str, str]:
    """Сохраняет полный journal-снимок до расчёта индикаторов без фиктивных чисел."""
    return {
        **dict.fromkeys(INDICATOR_SNAPSHOT_KEYS, "n/a"),
        **_market_metrics(market_filter),
        detail_key: reason,
    }


def aligned_up(*, ema_fast: Decimal, ema_mid: Decimal, ema_slow: Decimal) -> bool:
    """`aligned(up)` из docs/03_STRATEGY.md: ema9 > ema21 > ema50."""
    return ema_fast > ema_mid > ema_slow


def aligned_down(*, ema_fast: Decimal, ema_mid: Decimal, ema_slow: Decimal) -> bool:
    """`aligned(down)` — зеркально `aligned_up`."""
    return ema_fast < ema_mid < ema_slow


def classify_regime(
    *,
    adx_value: Decimal,
    atr_ratio: Decimal,
    ema_fast: Decimal,
    ema_mid: Decimal,
    ema_slow: Decimal,
    config: StrategyConfig,
) -> Regime:
    """Классификатор режима рынка (docs/03_STRATEGY.md).

    `chaos` требует высокого ADX и ATR/price; превышение порога ATR/price без
    экстремального ADX даёт `volatile` для явного отказа с причиной. По решению
    оператора 2026-10-14 стратегия не создаёт сигналы в `volatile` — коэффициент
    риска для этого режима не задействован в текущем торговом потоке.

    Возврат: один из `Regime`. Порядок проверки фиксирован и важен.
    """
    if adx_value > config.adx_chaos and atr_ratio > config.max_atr_ratio:
        return "chaos"
    if atr_ratio > config.max_atr_ratio:
        return "volatile"
    if adx_value > config.adx_trend and (
        aligned_up(ema_fast=ema_fast, ema_mid=ema_mid, ema_slow=ema_slow)
        or aligned_down(ema_fast=ema_fast, ema_mid=ema_mid, ema_slow=ema_slow)
    ):
        return "trending"
    if adx_value < config.adx_range:
        return "ranging"
    return "transitional"


@dataclass(frozen=True)
class _Snapshot:
    """Последние значения индикаторов, посчитанные по закрытым свечам."""

    ema_fast_5m: Decimal
    ema_mid_5m: Decimal
    ema_slow_5m: Decimal
    ema_fast_15m: Decimal
    ema_mid_15m: Decimal
    ema_slow_15m: Decimal
    ema_fast_1h: Decimal
    ema_mid_1h: Decimal
    ema_slow_1h: Decimal
    rsi_5m: Decimal
    rsi_15m: Decimal
    macd_hist_15m: Decimal
    macd_hist_15m_prev: Decimal
    bb_lower_5m: Decimal
    bb_upper_5m: Decimal
    bb_middle_5m: Decimal
    bb_middle_5m_prev: Decimal
    adx_15m: Decimal
    atr_15m: Decimal
    atr_ratio_15m: Decimal
    vwap_5m: Decimal
    close_5m: Decimal
    close_5m_prev: Decimal
    open_5m: Decimal
    high_5m: Decimal
    low_5m: Decimal
    volume_5m: int
    volume_avg_5m: Decimal
    volume_spike_5m: bool
    ema9_cross_up: bool
    ema9_cross_down: bool


def _last_defined(series: list[Decimal | None]) -> Decimal:
    value = series[-1]
    if value is None:
        raise InsufficientHistoryError("недостаточно закрытых свечей для индикатора")
    return value


def compute_snapshot(
    *,
    candles_5m: list[Candle],
    candles_15m: list[Candle],
    candles_1h: list[Candle],
    config: StrategyConfig,
) -> _Snapshot:
    """Считает все индикаторы, нужные правилам входа, по последним закрытым свечам.

    Использует только `is_complete is True`: незакрытые свечи и свечи стрима
    с неизвестной полнотой не могут повлиять на сигнал (docs/03_STRATEGY.md).
    Отказ: `InsufficientHistoryError`, если закрытой истории недостаточно хотя бы
    для одного нужного индикатора (нет заглушек вместо реального значения).
    """
    candles_5m = [c for c in candles_5m if c.is_complete is True]
    candles_15m = [c for c in candles_15m if c.is_complete is True]
    candles_1h = [c for c in candles_1h if c.is_complete is True]
    if not candles_5m or not candles_15m or not candles_1h:
        raise InsufficientHistoryError("нет закрытых свечей для одного из таймфреймов")
    closes_5m = [c.close for c in candles_5m]
    closes_15m = [c.close for c in candles_15m]
    closes_1h = [c.close for c in candles_1h]
    volumes_5m = [c.volume for c in candles_5m]

    ema_fast_5m = _ema(closes_5m, config.ema_fast)
    ema_mid_5m = _ema(closes_5m, config.ema_mid)
    ema_slow_5m = _ema(closes_5m, config.ema_slow)
    ema_fast_15m = _ema(closes_15m, config.ema_fast)
    ema_mid_15m = _ema(closes_15m, config.ema_mid)
    ema_slow_15m = _ema(closes_15m, config.ema_slow)
    ema_fast_1h = _ema(closes_1h, config.ema_fast)
    ema_mid_1h = _ema(closes_1h, config.ema_mid)
    ema_slow_1h = _ema(closes_1h, config.ema_slow)

    rsi_5m = _rsi(closes_5m, config.rsi_period)
    rsi_15m = _rsi(closes_15m, config.rsi_period)
    _, _, macd_hist_15m = _macd(
        closes_15m, fast=config.macd_fast, slow=config.macd_slow, signal=config.macd_signal
    )
    bb_middle_5m, bb_upper_5m, bb_lower_5m = _bollinger(closes_5m, config.bb_period, config.bb_mult)
    adx_15m = _adx(candles_15m, config.adx_period)
    atr_15m = _atr(candles_15m, config.atr_period)
    vwap_5m = _session_vwap(candles_5m)
    spike_5m = _volume_spike(volumes_5m, config.vol_window)
    if spike_5m[-1] is None:
        raise InsufficientHistoryError("недостаточно закрытых 5m-свечей для среднего объёма")
    volume_avg_5m = Decimal(sum(volumes_5m[-config.vol_window - 1 : -1])) / Decimal(
        config.vol_window
    )

    if len(ema_fast_5m) < 2 or len(ema_mid_5m) < 2:
        raise InsufficientHistoryError("недостаточно 5m-свечей для EMA-пересечения")
    ema_fast_5m_prev = ema_fast_5m[-2]
    ema_mid_5m_prev = ema_mid_5m[-2]
    if ema_fast_5m_prev is None or ema_mid_5m_prev is None:
        raise InsufficientHistoryError("недостаточно 5m-свечей для EMA-пересечения")

    if len(macd_hist_15m) < 2:
        raise InsufficientHistoryError("недостаточно 15m-свечей для MACD")
    macd_hist_15m_prev = macd_hist_15m[-2]
    if macd_hist_15m_prev is None:
        raise InsufficientHistoryError("недостаточно 15m-свечей для MACD")

    if len(bb_middle_5m) < 2:
        raise InsufficientHistoryError("недостаточно 5m-свечей для Bollinger")
    bb_middle_5m_prev = bb_middle_5m[-2]
    if bb_middle_5m_prev is None:
        raise InsufficientHistoryError("недостаточно 5m-свечей для Bollinger")

    close_5m = closes_5m[-1]
    atr_ratio_15m = _last_defined(atr_15m) / close_5m if close_5m != 0 else Decimal(0)

    ema_fast_5m_last = _last_defined(ema_fast_5m)
    ema_mid_5m_last = _last_defined(ema_mid_5m)

    return _Snapshot(
        ema_fast_5m=ema_fast_5m_last,
        ema_mid_5m=ema_mid_5m_last,
        ema_slow_5m=_last_defined(ema_slow_5m),
        ema_fast_15m=_last_defined(ema_fast_15m),
        ema_mid_15m=_last_defined(ema_mid_15m),
        ema_slow_15m=_last_defined(ema_slow_15m),
        ema_fast_1h=_last_defined(ema_fast_1h),
        ema_mid_1h=_last_defined(ema_mid_1h),
        ema_slow_1h=_last_defined(ema_slow_1h),
        rsi_5m=_last_defined(rsi_5m),
        rsi_15m=_last_defined(rsi_15m),
        macd_hist_15m=_last_defined(macd_hist_15m),
        macd_hist_15m_prev=macd_hist_15m_prev,
        bb_lower_5m=_last_defined(bb_lower_5m),
        bb_upper_5m=_last_defined(bb_upper_5m),
        bb_middle_5m=_last_defined(bb_middle_5m),
        bb_middle_5m_prev=bb_middle_5m_prev,
        adx_15m=_last_defined(adx_15m),
        atr_15m=_last_defined(atr_15m),
        atr_ratio_15m=atr_ratio_15m,
        vwap_5m=_last_defined(vwap_5m),
        close_5m=close_5m,
        close_5m_prev=closes_5m[-2],
        open_5m=candles_5m[-1].open,
        high_5m=candles_5m[-1].high,
        low_5m=candles_5m[-1].low,
        volume_5m=candles_5m[-1].volume,
        volume_avg_5m=volume_avg_5m,
        volume_spike_5m=spike_5m[-1],
        ema9_cross_up=ema_fast_5m_prev <= ema_mid_5m_prev and ema_fast_5m_last > ema_mid_5m_last,
        ema9_cross_down=ema_fast_5m_prev >= ema_mid_5m_prev and ema_fast_5m_last < ema_mid_5m_last,
    )


def _snapshot_to_journal(
    snap: _Snapshot,
    *,
    spread_bps: Decimal | None,
    gap_pct: Decimal | None,
    breadth: Decimal | None,
    market_filter: MarketFilterResult | None,
) -> dict[str, str]:
    """Собирает полный snapshot для journal (docs/03_STRATEGY.md, «Journal решения»)."""
    return {
        **_market_metrics(market_filter),
        "ema9": _decimal_to_str(snap.ema_fast_5m),
        "ema21": _decimal_to_str(snap.ema_mid_5m),
        "ema50": _decimal_to_str(snap.ema_slow_5m),
        "rsi_5m": _decimal_to_str(snap.rsi_5m),
        "rsi_15m": _decimal_to_str(snap.rsi_15m),
        "macd_hist": _decimal_to_str(snap.macd_hist_15m),
        "bb_lower": _decimal_to_str(snap.bb_lower_5m),
        "bb_upper": _decimal_to_str(snap.bb_upper_5m),
        "bb_middle_prev": _decimal_to_str(snap.bb_middle_5m_prev),
        "prev_close_5m": _decimal_to_str(snap.close_5m_prev),
        "adx": _decimal_to_str(snap.adx_15m),
        "atr": _decimal_to_str(snap.atr_15m),
        "atr_ratio": _decimal_to_str(snap.atr_ratio_15m),
        "vwap": _decimal_to_str(snap.vwap_5m),
        "volume": str(snap.volume_5m),
        "volume_avg20": str(snap.volume_avg_5m),
        "volume_spike": str(snap.volume_spike_5m),
        "spread_bps": _decimal_to_str(spread_bps),
        "gap_pct": _decimal_to_str(gap_pct),
        "breadth": _decimal_to_str(breadth),
    }


@dataclass(frozen=True)
class ConditionResult:
    """Одно из шести обязательных условий входа trending (docs/03_STRATEGY.md,
    таблица «Веткладка trending»). Все шесть обязательны, ни одно не может быть
    пропущено — поэтому это список именованных проверок, а не единый `bool`.
    """

    name: str
    passed: bool
    reason: str


def trending_conditions(
    side: Side, snap: _Snapshot, config: StrategyConfig
) -> list[ConditionResult]:
    """Шесть условий входа trending (docs/03_STRATEGY.md). `side` задаёт направление —
    для `short` все условия зеркальны long, как явно указано в документе.
    """
    if side == "long":
        cross = snap.ema9_cross_up
        rsi_low, rsi_high = config.rsi_trend_low, config.rsi_trend_high
        momentum = snap.macd_hist_15m > 0 and snap.macd_hist_15m > snap.macd_hist_15m_prev
        price_fair = snap.close_5m > snap.vwap_5m
        mtf = (
            aligned_up(
                ema_fast=snap.ema_fast_5m, ema_mid=snap.ema_mid_5m, ema_slow=snap.ema_slow_5m
            )
            and aligned_up(
                ema_fast=snap.ema_fast_15m, ema_mid=snap.ema_mid_15m, ema_slow=snap.ema_slow_15m
            )
            and aligned_up(
                ema_fast=snap.ema_fast_1h, ema_mid=snap.ema_mid_1h, ema_slow=snap.ema_slow_1h
            )
        )
    else:
        cross = snap.ema9_cross_down
        rsi_low, rsi_high = (
            Decimal(100) - config.rsi_trend_high,
            Decimal(100) - config.rsi_trend_low,
        )
        momentum = snap.macd_hist_15m < 0 and snap.macd_hist_15m < snap.macd_hist_15m_prev
        price_fair = snap.close_5m < snap.vwap_5m
        mtf = (
            aligned_down(
                ema_fast=snap.ema_fast_5m, ema_mid=snap.ema_mid_5m, ema_slow=snap.ema_slow_5m
            )
            and aligned_down(
                ema_fast=snap.ema_fast_15m, ema_mid=snap.ema_mid_15m, ema_slow=snap.ema_slow_15m
            )
            and aligned_down(
                ema_fast=snap.ema_fast_1h, ema_mid=snap.ema_mid_1h, ema_slow=snap.ema_slow_1h
            )
        )

    rsi_ok = rsi_low <= snap.rsi_15m <= rsi_high
    return [
        ConditionResult(
            "ema_cross", cross, f"ema9/ema21 cross {'up' if side == 'long' else 'down'}: {cross}"
        ),
        ConditionResult(
            "rsi_not_overheated",
            rsi_ok,
            f"RSI15m={snap.rsi_15m} in [{rsi_low},{rsi_high}]: {rsi_ok}",
        ),
        ConditionResult(
            "momentum",
            momentum,
            f"macd_hist={snap.macd_hist_15m} vs prev={snap.macd_hist_15m_prev}: {momentum}",
        ),
        ConditionResult(
            "price_fair_vs_vwap",
            price_fair,
            f"close={snap.close_5m} vs vwap={snap.vwap_5m}: {price_fair}",
        ),
        ConditionResult(
            "volume_confirmation", snap.volume_spike_5m, f"volume_spike={snap.volume_spike_5m}"
        ),
        ConditionResult("multi_timeframe_alignment", mtf, f"aligned на 5m/15m/1h: {mtf}"),
    ]


def ranging_conditions(
    side: Side, snap: _Snapshot, config: StrategyConfig
) -> list[ConditionResult]:
    """Mean-reversion вход в ranging (docs/03_STRATEGY.md, «Ветровка ranging»).

    EMA-пересечения здесь намеренно игнорируются полностью — документ явно
    предупреждает, что во флэте они дают ложные сигналы.
    """
    if side == "long":
        band_touch = snap.close_5m <= snap.bb_lower_5m
        rsi_extreme = snap.rsi_5m < config.rsi_oversold
        reversal_bar = snap.close_5m > snap.open_5m and snap.close_5m_prev < snap.bb_middle_5m_prev
    else:
        band_touch = snap.close_5m >= snap.bb_upper_5m
        rsi_extreme = snap.rsi_5m > config.rsi_overbought
        reversal_bar = snap.close_5m < snap.open_5m and snap.close_5m_prev > snap.bb_middle_5m_prev

    return [
        ConditionResult("band_touch", band_touch, f"close={snap.close_5m} vs band: {band_touch}"),
        ConditionResult("rsi_extreme", rsi_extreme, f"RSI5m={snap.rsi_5m}: {rsi_extreme}"),
        ConditionResult(
            "reversal_bar",
            reversal_bar,
            f"open={snap.open_5m} close={snap.close_5m} "
            f"prev_close={snap.close_5m_prev} prev_middle={snap.bb_middle_5m_prev}: "
            f"{reversal_bar}",
        ),
    ]


def gap_filter(
    *,
    now: datetime,
    session_open_price: Decimal,
    prev_session_close: Decimal,
    config: StrategyConfig,
) -> tuple[bool, Decimal]:
    """Гэп при открытии сессии (docs/03_STRATEGY.md, фильтр №5): пауза по тикеру
    `gap_pause_min` минут, если `|open - prev_close| / prev_close > gap_pause_pct`.

    Реализовано как чистая функция времени (без хранения состояния паузы внутри
    модуля, как и `risk_manager`/`should_cancel_remainder` в `broker/paper.py`):
    гэп физически может случиться только на открытии сессии, поэтому «активна
    пауза» эквивалентно «прошло меньше `gap_pause_min` минут от начала сессии
    сегодня, и в момент открытия гэп был больше порога» — обе части чистые функции
    входных данных, без мутируемого состояния.

    Возврат: `(gap_pause_active, gap_pct)`.
    """
    from trading_bot.utils import MSK, SESSION_TRADING_START

    if prev_session_close == 0:
        return False, Decimal(0)
    gap_pct = abs(session_open_price - prev_session_close) / prev_session_close
    if gap_pct <= config.gap_pause_pct:
        return False, gap_pct

    msk_now = now.astimezone(MSK)
    session_start_dt = datetime.combine(msk_now.date(), SESSION_TRADING_START, tzinfo=MSK)
    minutes_since_open = (msk_now - session_start_dt).total_seconds() / 60
    active = 0 <= minutes_since_open < config.gap_pause_min
    return active, gap_pct


@dataclass(frozen=True)
class MarketFilterResult:
    """Итог глобального фильтра рынка (docs/03_STRATEGY.md, «Глобальный фильтр
    рынка»), пересчитывается перед сессией и каждые `REGIME_REFRESH_MIN` минут —
    сам таймер живёт у вызывающего кода (executor/M8), здесь только чистая формула.
    """

    allow_long: bool
    allow_short: bool
    paused: bool
    reason: str
    breadth: Decimal | None = None
    chaos_score: Decimal | None = None
    chaos_threshold: Decimal | None = None
    market_regime: Literal["chaos", "normal"] | None = None


def evaluate_market_breadth(
    *,
    breadth: Decimal,
    atr_imoex_rising: bool,
    deviation_std: Decimal,
    chaos_std_threshold: Decimal,
    config: StrategyConfig,
) -> MarketFilterResult:
    """`breadth` — доля тикеров пула с закрытием выше предыдущего закрытия.
    `chaos_std_threshold` не входит в `docs/07_CONFIG_SPEC.md` как именованный
    параметр — передаётся вызывающим кодом (рассчитывается на пуле динамически,
    это не фиксированная настройка риска).
    """
    if deviation_std > chaos_std_threshold:
        return MarketFilterResult(
            allow_long=False,
            allow_short=False,
            paused=True,
            breadth=breadth,
            reason=(
                f"std отклонений {deviation_std} > порога {chaos_std_threshold} — " "пауза 30 минут"
            ),
        )
    allow_long = not (breadth < config.breadth_bearish and atr_imoex_rising)
    allow_short = not (breadth > config.breadth_bullish and atr_imoex_rising)
    if allow_long and allow_short:
        reason = f"breadth={breadth}, атр_imoex_растёт={atr_imoex_rising}: без ограничений"
    else:
        reason = (
            f"breadth={breadth}, атр_imoex_растёт={atr_imoex_rising}: ограничена сторона сигнала"
        )
    return MarketFilterResult(
        allow_long=allow_long, allow_short=allow_short, paused=False, reason=reason, breadth=breadth
    )


def evaluate(
    *,
    ticker: str,
    candles_5m: list[Candle],
    candles_15m: list[Candle],
    candles_1h: list[Candle],
    config: StrategyConfig,
    now: datetime,
    is_session_open: bool,
    session_reason: str,
    short_enabled: bool,
    session_guard_active: bool,
    spread_bps: Decimal | None = None,
    gap_result: tuple[bool, Decimal] | None = None,
    market_filter: MarketFilterResult | None = None,
    session_refusal_filter: Literal["session_open", "instrument_status_normal"] = "session_open",
    local_regime: ScheduledRegime | None = None,
) -> Decision:
    """Главная точка входа стратегии: считает режим, проверяет фильтры этого модуля
    и правила входа, возвращает `Decision` с полным snapshot для journal.

    Runtime передаёт `local_regime`: недоступный/протухший контекст закрывает вход,
    без fallback на per-bar classifier. Отсутствие аргумента сохраняет standalone API.
    Незакрытые свечи отбрасываются в `compute_snapshot` (docs/03_STRATEGY.md,
    «Нет заглядывания в будущее»); без достаточной закрытой истории — отказ.
    `session_guard_active` обязателен: вызывающий код передаёт
    `utils.can_trade_now().guard_active`; стратегия дополнительно проверяет окно
    по `now` и `config.session_guard_min`, чтобы неверный `False` не дал сигнал.
    `is_session_open` означает отсутствие *другого* отказа сессии: при guard-отказе
    `can_trade_now()` вызывающий передаёт `allowed or guard_active`, но для риска
    и исполнения сохраняет исходное `allowed=False`. Если другая причина закрыла
    сессию, её отказ имеет приоритет перед защитным окном.
    `session_clock_decision` также отказывает вне буднего обычного окна, даже если
    внешний `is_session_open` ошибочно равен `True`. Праздники и статус инструмента
    остаются ответственностью вызывающего кода. Защитное окно — отдельная причина
    отказа в journal.
    Фильтры `risk_manager.check()` (статус/спред/лимиты/корреляция/диверсификация)
    здесь не повторяются — их прогоняет вызывающий код отдельно после `Signal`
    (docs/01_ARCHITECTURE.md, торговый цикл), это не входит в зону strategy.py.

    Возврат: `Decision` — `verdict="signal"` только если все фильтры этого модуля
    прошли и хотя бы одна из сторон trending/ranging дала все обязательные условия.
    """
    filters_passed: list[str] = []
    filters_failed: list[str] = []
    regime_metrics = (
        {
            "local_regime_source": "scheduled",
            "local_regime_slot": local_regime.slot.isoformat() if local_regime.slot else "n/a",
            "local_regime_observed_at": local_regime.observed_at.isoformat(),
            "local_regime_valid_until": local_regime.valid_until.isoformat(),
            "local_regime_reason": local_regime.reason,
        }
        if local_regime is not None
        else {"local_regime_source": "standalone"}
    )

    clock_decision = session_clock_decision(now=now)
    local_guard_active = is_regular_session_guard_active(
        now=now, guard_min=config.session_guard_min
    )
    if not clock_decision.allowed or not is_session_open:
        if not clock_decision.allowed:
            refusal_reason = clock_decision.reason
        elif session_reason not in ("", "ok"):
            refusal_reason = session_reason
        else:
            refusal_reason = "сессия закрыта (причина не указана)"
        refusal_filter = session_refusal_filter if clock_decision.allowed else "session_open"
        preceding: tuple[str, ...] = ()
        if refusal_filter == "instrument_status_normal":
            preceding = ("session_open",)
            if not session_guard_active and not local_guard_active:
                preceding += ("session_guard",)
        return Decision(
            ts=now,
            ticker=ticker,
            regime="transitional",
            side=None,
            indicator_snapshot={
                **_unavailable_snapshot(
                    detail_key="session_reason", reason=refusal_reason, market_filter=market_filter
                ),
                **regime_metrics,
            },
            filters_passed=preceding,
            filters_failed=(refusal_filter,),
            verdict="no_signal",
            reason=refusal_reason,
        )
    filters_passed.append("session_open")
    if session_guard_active or local_guard_active:
        guard_reason = (
            session_reason
            if session_guard_active and session_reason not in ("", "ok")
            else f"защитное окно {config.session_guard_min} мин у открытия/закрытия обычных торгов"
        )
        return Decision(
            ts=now,
            ticker=ticker,
            regime="transitional",
            side=None,
            indicator_snapshot={
                **_unavailable_snapshot(
                    detail_key="session_reason", reason=guard_reason, market_filter=market_filter
                ),
                **regime_metrics,
            },
            filters_passed=tuple(filters_passed),
            filters_failed=("session_guard",),
            verdict="no_signal",
            reason=guard_reason,
        )
    filters_passed.append("session_guard")

    try:
        snap = compute_snapshot(
            candles_5m=candles_5m, candles_15m=candles_15m, candles_1h=candles_1h, config=config
        )
    except InsufficientHistoryError as exc:
        return Decision(
            ts=now,
            ticker=ticker,
            regime="transitional",
            side=None,
            indicator_snapshot={
                **_unavailable_snapshot(
                    detail_key="error", reason=str(exc), market_filter=market_filter
                ),
                **regime_metrics,
            },
            filters_passed=tuple(filters_passed),
            filters_failed=("insufficient_history",),
            verdict="no_signal",
            reason=str(exc),
        )

    if local_regime is not None and not local_regime.usable_for(ticker, now):
        return Decision(
            ts=now,
            ticker=ticker,
            regime="transitional",
            side=None,
            indicator_snapshot={
                **_unavailable_snapshot(
                    detail_key="error", reason=local_regime.reason, market_filter=market_filter
                ),
                **regime_metrics,
            },
            filters_passed=tuple(filters_passed),
            filters_failed=("local_regime_unavailable",),
            verdict="no_signal",
            reason="scheduled local regime unavailable; pause new entries",
        )

    if local_regime is not None:
        assert local_regime.regime is not None  # validated above, no standalone fallback
        regime = local_regime.regime
    else:
        regime = classify_regime(
            adx_value=snap.adx_15m,
            atr_ratio=snap.atr_ratio_15m,
            ema_fast=snap.ema_fast_15m,
            ema_mid=snap.ema_mid_15m,
            ema_slow=snap.ema_slow_15m,
            config=config,
        )
    gap_active, gap_pct = gap_result if gap_result is not None else (False, None)
    snapshot_dict = _snapshot_to_journal(
        snap,
        spread_bps=spread_bps,
        gap_pct=gap_pct,
        breadth=market_filter.breadth if market_filter is not None else None,
        market_filter=market_filter,
    )
    snapshot_dict.update(regime_metrics)

    if regime in ("chaos", "volatile", "transitional"):
        return Decision(
            ts=now,
            ticker=ticker,
            regime=regime,
            side=None,
            indicator_snapshot=snapshot_dict,
            filters_passed=tuple(filters_passed),
            filters_failed=(f"regime_{regime}",),
            verdict="no_signal",
            reason=f"режим {regime}: сигналы не формируются",
        )

    filters_passed.append("regime_tradable")
    if gap_active:
        filters_failed.append("gap_pause")
        return Decision(
            ts=now,
            ticker=ticker,
            regime=regime,
            side=None,
            indicator_snapshot=snapshot_dict,
            filters_passed=tuple(filters_passed),
            filters_failed=tuple(filters_failed),
            verdict="no_signal",
            reason=f"гэп {gap_pct}: пауза {config.gap_pause_min} минут по тикеру",
        )
    if gap_result is not None:
        filters_passed.append("gap_pause")

    if market_filter is not None and market_filter.paused:
        return Decision(
            ts=now,
            ticker=ticker,
            regime=regime,
            side=None,
            indicator_snapshot=snapshot_dict,
            filters_passed=tuple(filters_passed),
            filters_failed=("market_breadth",),
            verdict="no_signal",
            reason=market_filter.reason,
        )
    filters_passed.append("market_breadth")

    candidate_sides: list[Side] = ["long", "short"]
    if market_filter is not None:
        if not market_filter.allow_long:
            filters_failed.append(f"market_breadth_long: {market_filter.reason}")
        if not market_filter.allow_short:
            filters_failed.append(f"market_breadth_short: {market_filter.reason}")
        candidate_sides = [
            s
            for s in candidate_sides
            if (s == "long" and market_filter.allow_long)
            or (s == "short" and market_filter.allow_short)
        ]

    for side in candidate_sides:
        if side == "short" and not short_enabled:
            filters_failed.append("short_availability")
            continue

        if regime == "trending":
            conditions = trending_conditions(side, snap, config)
        elif regime == "ranging":
            conditions = ranging_conditions(side, snap, config)
        else:
            continue

        if all(c.passed for c in conditions):
            stop_price: Decimal | None = None
            take_price: Decimal | None = None
            signal_snapshot = snapshot_dict
            if regime == "ranging":
                stop_price = snap.low_5m if side == "long" else snap.high_5m
                take_price = snap.bb_middle_5m
                levels_valid = (
                    stop_price.is_finite()
                    and take_price.is_finite()
                    and stop_price > 0
                    and take_price > 0
                    and (
                        (side == "long" and stop_price < snap.close_5m < take_price)
                        or (side == "short" and take_price < snap.close_5m < stop_price)
                    )
                )
                if not levels_valid:
                    filters_failed.append(
                        f"entry_{side}: invalid_exit_levels stop={stop_price} "
                        f"close={snap.close_5m} take={take_price}"
                    )
                    continue
                signal_snapshot = {
                    **snapshot_dict,
                    "stop_price": str(stop_price),
                    "take_price": str(take_price),
                }
            strength = Decimal(sum(1 for c in conditions if c.passed)) / Decimal(len(conditions))
            reason = f"{regime} {side}: все условия входа выполнены"
            signal = Signal(
                ticker=ticker,
                side=side,
                reason=reason,
                indicators=signal_snapshot,
                regime=regime,
                strength=strength,
                ts=now,
                stop_price=stop_price,
                take_price=take_price,
            )
            return Decision(
                ts=now,
                ticker=ticker,
                regime=regime,
                side=side,
                indicator_snapshot=signal_snapshot,
                filters_passed=tuple(filters_passed) + (f"entry_{side}",),
                filters_failed=tuple(filters_failed),
                verdict="signal",
                reason=reason,
                signal=signal,
            )
        failed_reasons = ", ".join(f"{c.name} ({c.reason})" for c in conditions if not c.passed)
        filters_failed.append(f"entry_{side}: {failed_reasons}")

    return Decision(
        ts=now,
        ticker=ticker,
        regime=regime,
        side=None,
        indicator_snapshot=snapshot_dict,
        filters_passed=tuple(filters_passed),
        filters_failed=tuple(filters_failed),
        verdict="no_signal",
        reason="ни одна сторона не дала полного набора условий входа",
    )
