"""Динамический индекс хаоса рынка (docs/03_STRATEGY.md, «Глобальный фильтр рынка»).

Разрешено: считать по индексу (IMOEX) составной индекс хаоса `C_t` на закрытых
свечах и классифицировать «хаос/не хаос» по скользящему порогу с гистерезисом
и подтверждением несколькими барами. Запрещено: знать о стратегии/портфеле/
брокере выше по стеку (решение, торговать или нет, принимает `strategy.py`);
использовать Hurst-экспоненту, энтропийные меры или HMM — оператор явно выбрал
более простой и объяснимый состав индикаторов (эффективность хода + choppiness +
перцентиль ATR); импортировать SDK напрямую.

## Семантика

`C_t` — равновзвешенное среднее трёх компонент хаоса, каждая на шкале [0; 1],
по `period` закрытых бар индекса, заканчивающихся баром `t` (без заглядывания
в будущее — `t` обязан быть уже закрытым баром):

1. `1 - ER_t` — обратная эффективность хода Кауфмана (Kaufman Efficiency Ratio).
   `ER_t = |close_t - close_{t-period}| / sum(|close_i - close_{i-1}|)` по окну
   `[t-period+1; t]`. `ER_t in [0; 1]`: 1 — чистый тренд (путь == смещению),
   0 — максимально «шумное» блуждание. Если знаменатель (сумма приращений) равен
   нулю — цена не менялась вовсе внутри окна; по допущению оператора это
   трактуется как `ER_t = 1` (нет шума, раз нет и движения), то есть вклад в
   хаос от этой компоненты — нулевой, а не неопределённый (явное допущение,
   см. отчёт агента).
2. Нормированный Choppiness Index: `CI_t = 100 * log10(sum(TR) / (max(high) -
   min(low))) / log10(period)` по тому же окну `[t-period+1; t]`, нормируется
   делением на 100 и зажимается в `[0; 1]` (теоретическая граница классического
   CI — `[0; 100]`, зажим — защита от погрешности на вырожденных окнах).
   Если `max(high) - min(low) == 0` (цена не покидала одного уровня) —
   числитель суммы TR тоже обязан быть нулём; по допущению оператора это
   `CI_t = 0` (не хаос, а просто отсутствие движения).
3. Перцентильный ранг ATR(`period`) (метод Уайлдера, та же формула, что
   `indicators.atr`) в полном скользящем окне ровно `chaos_window` закрытых баров,
   включая текущий (docs/03_STRATEGY.md: окно такой же длины, что и для порога
   `T_t`, без растущего/неполного окна). Пока `chaos_window` закрытых значений
   ATR не набрано — ранг не определён, следовательно и `C_t` не определён целиком
   (fail-closed выше после, `is_chaos`/`regime` всё равно определён). Ранг — доля
   значений окна `<= текущего`, на шкале `(0; 1]`.

Порог `T_t` — скользящий квантиль уровня `threshold_pct` (по умолчанию 0.80)
по **строго предыдущим** `chaos_window` значениям `C` (бары `[t-chaos_window;
t-1]`, текущий бар `t` не входит — иначе это было бы заглядыванием в будущее).
Пока предыдущих `C`-баров меньше `chaos_window`, `T_t` не определён.

Классификация `regime` по каждому бару — гистерезис + подтверждение:

- Если данных недостаточно для `C_t` или для `T_t` — результат **fail-closed**:
  `regime = "chaos"` немедленно, без ожидания подтверждения (нет данных —
  нет доверия, безопасный дефолт, AGENTS.md §3 «проверяй, а не додумывай»).
- Если сейчас подтверждённое состояние «не хаос»: кандидат на переход в хаос —
  `C_t > T_t` (верхняя граница).
- Если сейчас подтверждённое состояние «хаос»: кандидат на выход из хаоса —
  `C_t < T_t - hysteresis` (нижняя граница полосы гистерезиса); иначе остаётся
  хаосом. Полоса гистерезиса не даёт индексу «дребезжать» около одного порога.
- Переход подтверждается, только если один и тот же кандидат-переход держится
  `confirm_bars` баров подряд (по умолчанию 2); иначе подтверждённое состояние
  не меняется.

Кэш в `MarketRegimeClassifier.evaluate()` — по последнему закрытому бару
(`ts_open` и количество свечей последнего вызова), не по факту вызова: пока
новый бар не закрылся, повторный опрос возвращает закэшированный результат без
пересчёта всей серии — показатели читаются из уже закрытых свечей индекса, а не
меняются между закрытиями баров.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from trading_bot.api_client import Candle
from trading_bot.indicators import Series, atr

logger = logging.getLogger(__name__)

#: Регим рынка по индексу хаоса: `"chaos"` блокирует сигналы, `"normal"` — нет.
Regime = Literal["chaos", "normal"]

#: Компоненты C_t равновзвешены (docs заказчика: «equal-weight average»).
_COMPONENT_COUNT = Decimal(3)


@dataclass(frozen=True)
class MarketRegimeConfig:
    """Параметры индекса хаоса. Все пороги — `Decimal` (деньги/коэффициенты риска
    не допускают `float`, docs/02_CODING_RULES.md), количество баров — `int`.

    `period` — окно ER/Choppiness/ATR (по умолчанию 14, как и остальные
    индикаторы `indicators.py`). `chaos_window` — длина окна скользящего
    квантиля порога и верхняя граница окна перцентиля ATR (по умолчанию 500).
    `threshold_pct` — уровень квантиля порога `T_t` (по умолчанию 0.80).
    `hysteresis` — ширина полосы гистерезиса в единицах `C` (по умолчанию 0.05).
    `confirm_bars` — число подряд идущих баров-кандидатов для подтверждения
    смены состояния (по умолчанию 2).
    """

    period: int = 14
    chaos_window: int = 500
    threshold_pct: Decimal = Decimal("0.80")
    hysteresis: Decimal = Decimal("0.05")
    confirm_bars: int = 2

    def __post_init__(self) -> None:
        if self.period < 2:
            raise ValueError("period должен быть >= 2 (нужен log10(period) в Choppiness)")
        if self.chaos_window < 1:
            raise ValueError("chaos_window должен быть положительным")
        if not (Decimal(0) < self.threshold_pct < Decimal(1)):
            raise ValueError("threshold_pct должен быть строго между 0 и 1")
        if self.hysteresis < 0:
            raise ValueError("hysteresis не может быть отрицательным")
        if self.confirm_bars < 1:
            raise ValueError("confirm_bars должен быть положительным")


#: Модульный singleton-дефолт (ruff B008: вызов в аргументах по умолчанию
#: запрещён) — безопасен, так как `MarketRegimeConfig` иммутабелен (`frozen=True`).
_DEFAULT_CONFIG = MarketRegimeConfig()


@dataclass(frozen=True)
class RegimeBar:
    """Результат классификации одного закрытого бара индекса.

    `chaos_score` (`C_t`) и `threshold` (`T_t`) — `None`, если истории не
    хватает для их расчёта (короткая история индекса) — остаются в `RegimeBar` для
    журнала (прозрачность решения, docs/03_STRATEGY.md «Journal решения»). `regime`
    определён всегда и в этом случае равен `"chaos"` (fail-closed).
    """

    chaos_score: Decimal | None
    threshold: Decimal | None
    regime: Regime

    @property
    def is_chaos(self) -> bool:
        """Удобный булев алиас `regime == "chaos"` для вызывающего кода (фильтры/журнал)."""
        return self.regime == "chaos"


def _true_range(current: Candle, previous: Candle | None) -> Decimal:
    """True Range текущего бара (дублирует формулу `indicators._true_range`
    локально — этот модуль не опирается на приватные имена чужого модуля).
    """
    if previous is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def _efficiency_ratio(closes: Sequence[Decimal], end: int, period: int) -> Decimal:
    """ER_t Кауфмана по закрытиям `closes[end-period .. end]` (нужно `period+1` точек)."""
    window = closes[end - period : end + 1]
    numerator = abs(window[-1] - window[0])
    denominator = sum((abs(window[i] - window[i - 1]) for i in range(1, len(window))), Decimal(0))
    if denominator == 0:
        # Допущение оператора: цена не двигалась вовсе -> нет шума -> ER=1 (см. докстринг модуля).
        return Decimal(1)
    return numerator / denominator


def _choppiness_norm(candles: Sequence[Candle], end: int, period: int) -> Decimal:
    """Нормированный Choppiness Index по окну `[end-period+1 .. end]`, шкала `[0;1]`."""
    window = candles[end - period + 1 : end + 1]
    prevs = candles[end - period : end]
    true_ranges = [_true_range(c, p) for c, p in zip(window, prevs, strict=True)]
    sum_tr = sum(true_ranges, Decimal(0))
    high_max = max(c.high for c in window)
    low_min = min(c.low for c in window)
    rng = high_max - low_min
    if rng == 0:
        # Допущение оператора: нулевой диапазон => sum_tr тоже 0 -> не хаос, а штиль.
        return Decimal(0)
    ratio = sum_tr / rng
    if ratio <= 0:
        return Decimal(0)
    ci_raw = Decimal(100) * (ratio.log10() / Decimal(period).log10())
    ci_norm = ci_raw / Decimal(100)
    return min(max(ci_norm, Decimal(0)), Decimal(1))


def _atr_percentile_rank(atr_series: Series, end: int, chaos_window: int) -> Decimal | None:
    """Перцентильный ранг `atr_series[end]` в полном окне длиной `chaos_window`.

    docs/03_STRATEGY.md требует полное окно в `chaos_window` закрытых баров для перцентиля
    ATR, так же как и для порога `T_t` — без растущего окна: возврат `None`,
    пока не набралось ровно `chaos_window` опредёлённых значений ATR включая текущее
    (вызывающий код трактует `None` как недостаточно истории -> fail-closed `C_t`).
    """
    start = end - chaos_window + 1
    if start < 0:
        return None
    window = atr_series[start : end + 1]
    if any(v is None for v in window):
        return None
    current = atr_series[end]
    assert current is not None  # вызывающий код гарантирует это по построению
    count_le = sum(1 for v in window if v is not None and v <= current)
    return Decimal(count_le) / Decimal(len(window))


def compute_chaos_score(
    candles: Sequence[Candle], config: MarketRegimeConfig = _DEFAULT_CONFIG
) -> list[Decimal | None]:
    """`C_t` для каждого бара: `None`, пока не набралось `period+1` закрытых бар
    (ER/Choppiness) И полное окно `chaos_window` закрытых значений ATR для перцентиля
    (docs/03_STRATEGY.md) — на практике при дефолтах это значит `None` до `period +
    chaos_window - 1` включительно (индексация с 0).

    Возврат: список длины `len(candles)`. Чистая функция без побочных эффектов —
    каждый элемент использует только бары с индексом `<= t` (без заглядывания
    в будущее, docs/03_STRATEGY.md).
    """
    n = len(candles)
    result: list[Decimal | None] = [None] * n
    if n == 0:
        return result
    _validate_candles(candles)

    atr_series = atr(candles, config.period)
    for t in range(config.period, n):
        atr_pct = _atr_percentile_rank(atr_series, t, config.chaos_window)
        if atr_pct is None:
            continue  # недостаточно истории ATR на полное окно `chaos_window` -> C_t не определён
        chaos_er = Decimal(1) - _efficiency_ratio([c.close for c in candles], t, config.period)
        ci_norm = _choppiness_norm(candles, t, config.period)
        result[t] = (chaos_er + ci_norm + atr_pct) / _COMPONENT_COUNT
    return result


def _quantile(sorted_values: Sequence[Decimal], p: Decimal) -> Decimal:
    """Квантиль уровня `p` по уже отсортированным значениям, линейная интерполяция."""
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    pos = p * Decimal(n - 1)
    lower_idx = int(pos)
    upper_idx = min(lower_idx + 1, n - 1)
    frac = pos - Decimal(lower_idx)
    return sorted_values[lower_idx] + frac * (sorted_values[upper_idx] - sorted_values[lower_idx])


def _rolling_thresholds(
    scores: Sequence[Decimal | None], config: MarketRegimeConfig
) -> list[Decimal | None]:
    """`T_t` по строго предыдущим `chaos_window` определённым `C`-барам (не включая `t`)."""
    n = len(scores)
    result: list[Decimal | None] = [None] * n
    history: list[Decimal] = []
    for t in range(n):
        if len(history) >= config.chaos_window:
            window = sorted(history[-config.chaos_window :])
            result[t] = _quantile(window, config.threshold_pct)
        score_t = scores[t]
        if score_t is not None:
            history.append(score_t)
    return result


def _classify_states(
    scores: Sequence[Decimal | None],
    thresholds: Sequence[Decimal | None],
    config: MarketRegimeConfig,
) -> list[bool]:
    """Гистерезис + подтверждение `confirm_bars` баров поверх сырых `C_t`/`T_t`.

    Чистая функция состояния: при недостатке данных (`None` у score или
    threshold) состояние немедленно становится `True` (fail-closed) и счётчик
    подтверждения сбрасывается — частичное/устаревшее подтверждение не
    переносится через провал в данных.
    """
    state = True  # fail-closed дефолт до первой валидной классификации
    pending_candidate: bool | None = None
    pending_count = 0
    result: list[bool] = []
    for score, threshold in zip(scores, thresholds, strict=True):
        if score is None or threshold is None:
            state = True
            pending_candidate = None
            pending_count = 0
            result.append(state)
            continue

        candidate = score >= (threshold - config.hysteresis) if state else score > threshold

        if candidate == state:
            pending_candidate = None
            pending_count = 0
        else:
            if pending_candidate == candidate:
                pending_count += 1
            else:
                pending_candidate = candidate
                pending_count = 1
            if pending_count >= config.confirm_bars:
                state = candidate
                pending_candidate = None
                pending_count = 0
        result.append(state)
    return result


def _validate_candles(candles: Sequence[Candle]) -> None:
    """Отказывает при незакрытой или некорректной истории индекса."""
    previous_ts = None
    for candle in candles:
        if candle.ts_open.tzinfo is None or candle.ts_open.utcoffset() is None:
            raise ValueError("ts_open должен быть timezone-aware (docs/02_CODING_RULES.md)")
        if previous_ts is not None and candle.ts_open <= previous_ts:
            raise ValueError("candles должны иметь строго возрастающий ts_open")
        if candle.is_complete is not True:
            raise ValueError("индексная свеча не подтверждена как закрытая")
        if (
            any(
                not isinstance(price, Decimal) or not price.is_finite() or price <= 0
                for price in (candle.open, candle.high, candle.low, candle.close)
            )
            or candle.low > min(candle.open, candle.close)
            or candle.high < max(candle.open, candle.close)
            or candle.low > candle.high
            or not isinstance(candle.volume, int)
            or candle.volume < 0
        ):
            raise ValueError("некорректная OHLC/volume свеча индекса")
        previous_ts = candle.ts_open


def classify_market_regime(
    candles: Sequence[Candle], config: MarketRegimeConfig = _DEFAULT_CONFIG
) -> list[RegimeBar]:
    """Полная серия классификации по всем закрытым барам индекса.

    Возврат: список `RegimeBar` длины `len(candles)`, выровненный по позиции
    с `candles` (бар `result[t]` соответствует `candles[t]`).
    Отказ: `ValueError` при неполных/некорректных свечах или нарушенном порядке;
    вызывающий автономный классификатор превращает ошибку в fail-closed chaos.
    """
    if not candles:
        return []
    _validate_candles(candles)
    scores = compute_chaos_score(candles, config)
    thresholds = _rolling_thresholds(scores, config)
    states = _classify_states(scores, thresholds, config)
    return [
        RegimeBar(chaos_score=s, threshold=th, regime="chaos" if is_chaos else "normal")
        for s, th, is_chaos in zip(scores, thresholds, states, strict=True)
    ]


@dataclass
class MarketRegimeClassifier:
    """Кэш классификации по последнему закрытому бару индекса (не по опросу).

    Пересчёт происходит при изменении любой свечи закрытой истории (включая
    коррекцию OHLC/volume или флаг завершения), а не при каждом опросе.
    """

    config: MarketRegimeConfig = field(default_factory=MarketRegimeConfig)
    _cache_key: tuple[Candle, ...] | None = field(default=None, init=False, repr=False)
    _cache_value: RegimeBar | None = field(default=None, init=False, repr=False)

    def evaluate(self, candles: Sequence[Candle]) -> RegimeBar:
        """Классификация последнего бара в `candles` (обязан быть уже закрытым).

        Возврат: `RegimeBar` для `candles[-1]`; некорректная или незакрытая
        история даёт `chaos` без чисел. Пустая история — `ValueError`.
        """
        if not candles:
            raise ValueError("candles не может быть пустым")
        key = tuple(candles)
        if self._cache_key == key and self._cache_value is not None:
            return self._cache_value
        try:
            result = classify_market_regime(candles, self.config)[-1]
        except ValueError as exc:
            logger.error(
                "event=index_bar_invalid ts=%s error_type=%s action=chaos",
                candles[-1].ts_open,
                type(exc).__name__,
            )
            result = RegimeBar(chaos_score=None, threshold=None, regime="chaos")
        self._cache_key = key
        self._cache_value = result
        return result
