"""Тесты динамического индекса хаоса рынка (docs/03_STRATEGY.md, `market_regime.py`).

Независимая проверка составных формул (`ER`, нормированный `Choppiness`,
перцентиль `ATR`) — через параллельную float-реализацию в этом файле
(`_ref_*`), а не копию кода модуля, по аналогии с numpy-сверкой в
`tests/test_indicators.py`. Состояние гистерезиса/подтверждения проверяется
отдельно, напрямую через `_classify_states`, синтетическими последовательностями
`C`/`T` — так сценарии «держится недостаточно баров», «полоса гистерезиса»,
«недостаток истории» воспроизводимы детерминированно, без обратной сборки
реальных свечей под нужный хаос-скор.
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trading_bot.api_client import Candle
from trading_bot.market_regime import (
    MarketRegimeClassifier,
    MarketRegimeConfig,
    RegimeBar,
    _classify_states,
    _rolling_thresholds,
    classify_market_regime,
    compute_chaos_score,
)

_TS0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)

_PROPERTY_SETTINGS = settings(max_examples=200, deadline=None)


def _candles(
    closes: list[float], *, high_offsets: list[float] | None = None, step_min: int = 15
) -> list[Candle]:
    offsets = high_offsets or [0.3] * len(closes)
    return [
        Candle(
            open=Decimal(str(c)),
            high=Decimal(str(c + off)),
            low=Decimal(str(c - off)),
            close=Decimal(str(c)),
            volume=100,
            ts_open=_TS0 + timedelta(minutes=step_min * i),
            is_complete=True,
        )
        for i, (c, off) in enumerate(zip(closes, offsets, strict=True))
    ]


# --- MarketRegimeConfig: валидация ----------------------------------------------


def test_config_defaults_match_operator_decision() -> None:
    config = MarketRegimeConfig()
    assert config.period == 14
    assert config.chaos_window == 500
    assert config.threshold_pct == Decimal("0.80")
    assert config.hysteresis == Decimal("0.05")
    assert config.confirm_bars == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"period": 1},
        {"period": 0},
        {"chaos_window": 0},
        {"threshold_pct": Decimal("0")},
        {"threshold_pct": Decimal("1")},
        {"threshold_pct": Decimal("1.5")},
        {"hysteresis": Decimal("-0.01")},
        {"confirm_bars": 0},
    ],
)
def test_config_rejects_invalid_values(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MarketRegimeConfig(**kwargs)  # type: ignore[arg-type]


# --- compute_chaos_score: отсутствие/короткая история ----------------------------


def test_chaos_score_empty_candles_returns_empty() -> None:
    assert compute_chaos_score([], MarketRegimeConfig(period=4)) == []


def test_chaos_score_short_history_is_all_none() -> None:
    """Меньше `period + 1` закрытых бар -- C_t нигде не определён (fail-closed выше)."""
    config = MarketRegimeConfig(period=5, chaos_window=10)
    candles = _candles([100.0 + i for i in range(5)])  # ровно period баров, не period+1
    result = compute_chaos_score(candles, config)
    assert result == [None] * 5


def test_chaos_score_first_defined_index_is_period_when_atr_window_is_one() -> None:
    """`chaos_window=1` -- окно перцентиля ATR тривиально, порог — только ER/Choppiness."""
    config = MarketRegimeConfig(period=5, chaos_window=1)
    candles = _candles([100.0 + i for i in range(8)])
    result = compute_chaos_score(candles, config)
    assert result[:5] == [None] * 5
    assert result[5] is not None
    assert result[6] is not None
    assert result[7] is not None


def test_chaos_score_requires_full_chaos_window_of_atr_history() -> None:
    """docs/03_STRATEGY.md: перцентиль ATR требует полное окно `chaos_window`
    закрытых баров, а не растущее -- C_t остаётся `None`, пока ATR-истории меньше
    `chaos_window`, даже если ER/Choppiness уже определены.
    """
    config = MarketRegimeConfig(period=5, chaos_window=10)
    candles = _candles([100.0 + i for i in range(8)])  # ER/CI определены с индекса 5,
    # но ATR-истории всего 4 точки (индексы 4..7) -- меньше chaos_window=10.
    result = compute_chaos_score(candles, config)
    assert result == [None] * 8


def test_chaos_score_rejects_naive_timestamp() -> None:
    config = MarketRegimeConfig(period=3, chaos_window=5)
    candles = _candles([100.0, 101.0, 102.0, 103.0])
    naive = Candle(
        open=candles[0].open,
        high=candles[0].high,
        low=candles[0].low,
        close=candles[0].close,
        volume=candles[0].volume,
        ts_open=datetime(2026, 1, 5, 10, 0),  # noqa: DTZ001 -- naive нарочно, это и проверяем
        is_complete=True,
    )
    with pytest.raises(ValueError):
        compute_chaos_score([naive, *candles[1:]], config)


@pytest.mark.parametrize(
    "overrides",
    [
        {"is_complete": False},
        {"is_complete": None},
        {"high": Decimal("99")},
        {"close": Decimal("0")},
    ],
)
def test_classifier_evaluate_fails_closed_on_incomplete_or_invalid_last_candle(
    overrides: dict[str, object],
) -> None:
    classifier = MarketRegimeClassifier(MarketRegimeConfig(period=3, chaos_window=5))
    candles = _candles([100.0, 101.0, 102.0])
    candles[-1] = replace(candles[-1], **overrides)
    result = classifier.evaluate(candles)
    assert result == RegimeBar(chaos_score=None, threshold=None, regime="chaos")


def test_chaos_score_rejects_out_of_order_timestamps() -> None:
    config = MarketRegimeConfig(period=3, chaos_window=5)
    candles = _candles([100.0, 101.0, 102.0, 103.0])
    swapped = [candles[1], candles[0], candles[2], candles[3]]
    with pytest.raises(ValueError):
        compute_chaos_score(swapped, config)


# --- compute_chaos_score: независимая (float) сверка формул --------------------


def _ref_er(closes: list[float], end: int, period: int) -> float:
    window = closes[end - period : end + 1]
    numerator = abs(window[-1] - window[0])
    denominator = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if denominator == 0:
        return 1.0
    return numerator / denominator


def _ref_true_range(high: float, low: float, prev_close: float | None) -> float:
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def _ref_choppiness_norm(
    highs: list[float], lows: list[float], closes: list[float], end: int, period: int
) -> float:
    trs = []
    for i in range(end - period + 1, end + 1):
        prev_close = closes[i - 1] if i > 0 else None
        trs.append(_ref_true_range(highs[i], lows[i], prev_close))
    sum_tr = sum(trs)
    rng = max(highs[end - period + 1 : end + 1]) - min(lows[end - period + 1 : end + 1])
    if rng == 0:
        return 0.0
    ratio = sum_tr / rng
    if ratio <= 0:
        return 0.0
    ci_raw = 100 * math.log10(ratio) / math.log10(period)
    return min(max(ci_raw / 100, 0.0), 1.0)


def _ref_atr_series(
    highs: list[float], lows: list[float], closes: list[float], period: int
) -> list[float | None]:
    n = len(highs)
    trs = [_ref_true_range(highs[i], lows[i], closes[i - 1] if i > 0 else None) for i in range(n)]
    result: list[float | None] = [None] * n
    if n < period:
        return result
    seed = sum(trs[:period]) / period
    result[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + trs[i]) / period
        result[i] = prev
    return result


def _ref_atr_rank(atr_series: list[float | None], end: int, chaos_window: int) -> float | None:
    start = end - chaos_window + 1
    if start < 0:
        return None
    window = atr_series[start : end + 1]
    if any(v is None for v in window):
        return None
    current = atr_series[end]
    assert current is not None
    count_le = sum(1 for v in window if v is not None and v <= current)
    return count_le / len(window)


def _ref_chaos_score(candles: list[Candle], config: MarketRegimeConfig) -> list[float | None]:
    closes = [float(c.close) for c in candles]
    highs = [float(c.high) for c in candles]
    lows = [float(c.low) for c in candles]
    atr_series = _ref_atr_series(highs, lows, closes, config.period)
    n = len(candles)
    result: list[float | None] = [None] * n
    for t in range(config.period, n):
        atr_pct = _ref_atr_rank(atr_series, t, config.chaos_window)
        if atr_pct is None:
            continue
        chaos_er = 1 - _ref_er(closes, t, config.period)
        ci_norm = _ref_choppiness_norm(highs, lows, closes, t, config.period)
        result[t] = (chaos_er + ci_norm + atr_pct) / 3
    return result


# Серии: A -- тренд с шумом, D -- осцилляция (пере-используют стиль test_indicators.py).
_CLOSES_TREND = [100 + i * 0.7 + (0.3 if i % 3 == 0 else -0.1) for i in range(40)]
_CLOSES_OSCILLATION = [100 + 5 * math.sin(i * 0.4) for i in range(40)]
_HIGH_OFFSETS_VARIABLE = [0.2 + 0.1 * (i % 5) for i in range(40)]


@pytest.mark.parametrize(
    "closes,offsets",
    [
        (_CLOSES_TREND, _HIGH_OFFSETS_VARIABLE),
        (_CLOSES_OSCILLATION, _HIGH_OFFSETS_VARIABLE),
    ],
)
def test_chaos_score_matches_independent_reference(
    closes: list[float], offsets: list[float]
) -> None:
    config = MarketRegimeConfig(period=7, chaos_window=15)
    candles = _candles(closes, high_offsets=offsets)
    actual = compute_chaos_score(candles, config)
    expected = _ref_chaos_score(candles, config)
    for a, e in zip(actual, expected, strict=True):
        if e is None:
            assert a is None
        else:
            assert a is not None
            assert abs(float(a) - e) < 1e-9


@given(
    closes=st.lists(
        st.floats(min_value=50, max_value=200, allow_nan=False, allow_infinity=False),
        min_size=25,
        max_size=60,
    )
)
@_PROPERTY_SETTINGS
def test_property_chaos_score_bounded_0_to_1(closes: list[float]) -> None:
    """C_t, когда определён, обязан лежать в [0; 1] -- равновзвешенное среднее
    трёх компонент, каждая из которых по построению в [0; 1]."""
    config = MarketRegimeConfig(period=5, chaos_window=10)
    candles = _candles(closes, high_offsets=[0.5] * len(closes))
    for score in compute_chaos_score(candles, config):
        if score is not None:
            assert Decimal(0) <= score <= Decimal(1)


# --- no lookahead: будущие бары не меняют уже посчитанный C_t -------------------


def test_chaos_score_no_lookahead() -> None:
    config = MarketRegimeConfig(period=6, chaos_window=10)
    candles = _candles(_CLOSES_TREND, high_offsets=_HIGH_OFFSETS_VARIABLE)
    t = 20
    full = compute_chaos_score(candles, config)
    prefix = compute_chaos_score(candles[:t], config)
    assert full[t - 1] == prefix[t - 1]


def test_classify_market_regime_no_lookahead_full_pipeline() -> None:
    """Будущие бары не меняют подтверждённое состояние уже прошедшего бара
    (включая порог T_t и гистерезис/подтверждение, не только C_t)."""
    config = MarketRegimeConfig(period=4, chaos_window=8, confirm_bars=2)
    candles = _candles(_CLOSES_OSCILLATION, high_offsets=_HIGH_OFFSETS_VARIABLE)
    t = 25
    full = classify_market_regime(candles, config)
    prefix = classify_market_regime(candles[:t], config)
    assert full[t - 1] == prefix[t - 1]


# --- T_t: требует ровно chaos_window ПРЕДЫДУЩИХ C-баров --------------------------


def test_threshold_none_until_chaos_window_prior_scores_available() -> None:
    config = MarketRegimeConfig(period=3, chaos_window=5)
    # period=3 -> первый C на индексе 3. Нужно ещё 5 предыдущих C (индексы 3..7)
    # прежде чем T_t определится на индексе 8.
    scores: list[Decimal | None] = [None, None, None]
    scores += [Decimal("0.5")] * 5  # индексы 3..7 -- первые 5 определённых C
    scores.append(Decimal("0.5"))  # индекс 8 -- теперь есть 5 предыдущих
    thresholds = _rolling_thresholds(scores, config)
    assert thresholds[:8] == [None] * 8
    assert thresholds[8] is not None


def test_threshold_is_quantile_of_prior_window_excluding_current_bar() -> None:
    config = MarketRegimeConfig(period=2, chaos_window=4, threshold_pct=Decimal("0.5"))
    scores: list[Decimal | None] = [Decimal(v) for v in ["1", "2", "3", "4"]]
    # Текущий бар (индекс 4) имеет гигантский C, но он не должен попасть в T_t[4]:
    scores.append(Decimal("1000"))
    thresholds = _rolling_thresholds(scores, config)
    assert thresholds[:4] == [None] * 4
    # Медиана {1,2,3,4} линейной интерполяцией = 2.5, 1000 из окна исключён.
    assert thresholds[4] == Decimal("2.5")


# --- _classify_states: fail-closed, гистерезис, подтверждение --------------------


def _cfg(hysteresis: str = "0.05", confirm_bars: int = 2) -> MarketRegimeConfig:
    return MarketRegimeConfig(hysteresis=Decimal(hysteresis), confirm_bars=confirm_bars)


def test_classify_states_fail_closed_when_score_missing() -> None:
    scores: list[Decimal | None] = [None, None, None]
    thresholds: list[Decimal | None] = [None, None, None]
    assert _classify_states(scores, thresholds, _cfg()) == [True, True, True]


def test_classify_states_fail_closed_when_threshold_missing_but_score_present() -> None:
    scores: list[Decimal | None] = [Decimal("0.1"), Decimal("0.1")]
    thresholds: list[Decimal | None] = [None, None]
    assert _classify_states(scores, thresholds, _cfg()) == [True, True]


def test_classify_states_entry_requires_confirm_bars_above_threshold() -> None:
    t = Decimal("0.80")
    config = _cfg(confirm_bars=2)
    # Сначала два низких бара подтверждают выход из стартового fail-closed chaos.
    # Затем один бар выше T не возвращает в chaos, второй подтверждает вход.
    scores = [Decimal("0.70"), Decimal("0.70"), Decimal("0.81"), Decimal("0.82")]
    thresholds = [t] * len(scores)
    assert _classify_states(scores, thresholds, config) == [True, False, False, True]


def test_classify_states_exit_requires_confirm_bars_below_hysteresis_band() -> None:
    t = Decimal("0.80")
    config = _cfg(hysteresis="0.05", confirm_bars=2)
    # Старт -- fail-closed "хаос". Один бар ниже (t - hysteresis) -- кандидат на
    # выход, но подтверждения ещё нет (только 1 из 2 баров) -> остаётся хаос.
    scores = [Decimal("0.70")]
    thresholds = [t]
    assert _classify_states(scores, thresholds, config) == [True]

    # Второй подряд бар ниже полосы -- теперь подтверждено, выход из хаоса.
    scores = [Decimal("0.70"), Decimal("0.70")]
    thresholds = [t, t]
    assert _classify_states(scores, thresholds, config) == [True, False]


def test_classify_states_hysteresis_band_prevents_flicker_near_threshold() -> None:
    """C колеблется между (T - hysteresis) и T -- состояние "хаос" не должно
    сбрасываться, пока C не уйдёт НИЖЕ нижней границы полосы."""
    t = Decimal("0.80")
    config = _cfg(hysteresis="0.05", confirm_bars=1)
    # 0.76 лежит строго между 0.75 (T-hysteresis) и 0.80 (T) -- в полосе, не ниже
    # неё, значит кандидат на выход не возникает вовсе, состояние остаётся "хаос".
    scores = [Decimal("0.76")] * 6
    thresholds = [t] * 6
    assert _classify_states(scores, thresholds, config) == [True] * 6


def test_classify_states_confirm_bars_one_switches_immediately() -> None:
    t = Decimal("0.80")
    config = _cfg(hysteresis="0.05", confirm_bars=1)
    scores = [Decimal("0.70")]  # ниже полосы -> немедленный выход, подтверждение=1
    thresholds = [t]
    assert _classify_states(scores, thresholds, config) == [False]


def test_classify_states_single_opposite_bar_does_not_switch_with_confirm_two() -> None:
    t = Decimal("0.80")
    config = _cfg(hysteresis="0.05", confirm_bars=2)
    # Выходим из хаоса (два подряд низких бара), затем один высокий бар (кандидат
    # на вход) -- недостаточно для немедленного возврата в хаос.
    scores = [Decimal("0.70"), Decimal("0.70"), Decimal("0.90")]
    thresholds = [t, t, t]
    assert _classify_states(scores, thresholds, config) == [True, False, False]


def test_classify_states_interrupted_confirmation_resets_counter() -> None:
    t = Decimal("0.80")
    config = _cfg(hysteresis="0.05", confirm_bars=2)
    # Бар1: кандидат на выход (1/2). Бар2: снова внутри полосы -- кандидат пропал,
    # счётчик сбрасывается. Бар3: кандидат на выход (1/2, не 2/2) -> остаётся хаос.
    scores = [Decimal("0.70"), Decimal("0.76"), Decimal("0.70")]
    thresholds = [t, t, t]
    assert _classify_states(scores, thresholds, config) == [True, True, True]


# --- MarketRegimeClassifier: кэш по последнему закрытому бару --------------------


def test_classifier_evaluate_rejects_empty_candles() -> None:
    classifier = MarketRegimeClassifier(MarketRegimeConfig(period=3, chaos_window=5))
    with pytest.raises(ValueError):
        classifier.evaluate([])


def test_classifier_recomputes_when_same_timestamp_bar_content_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import trading_bot.market_regime as module

    classifier = MarketRegimeClassifier(MarketRegimeConfig(period=3, chaos_window=5))
    first = _candles([100.0, 101.0, 102.0])
    second = first[:-1] + [replace(first[-1], close=Decimal("103"), high=Decimal("103.3"))]
    calls = {"count": 0}

    def classify(candles: list[Candle], _config: MarketRegimeConfig) -> list[RegimeBar]:
        calls["count"] += 1
        return [RegimeBar(Decimal(candles[-1].close), None, "normal")]

    monkeypatch.setattr(module, "classify_market_regime", classify)
    assert classifier.evaluate(first).chaos_score == Decimal("102")
    assert classifier.evaluate(second).chaos_score == Decimal("103")
    assert calls["count"] == 2


def test_classifier_evaluate_returns_cached_object_for_same_last_bar() -> None:
    config = MarketRegimeConfig(period=4, chaos_window=8)
    classifier = MarketRegimeClassifier(config)
    candles = _candles(_CLOSES_TREND[:12], high_offsets=_HIGH_OFFSETS_VARIABLE[:12])
    first = classifier.evaluate(candles)
    second = classifier.evaluate(list(candles))  # новый список, тот же последний бар
    assert first is second  # кэш-хит: пересчёта не было, вернулся тот же объект


def test_classifier_evaluate_recomputes_when_new_bar_closes() -> None:
    config = MarketRegimeConfig(period=4, chaos_window=8)
    classifier = MarketRegimeClassifier(config)
    candles = _candles(_CLOSES_TREND[:12], high_offsets=_HIGH_OFFSETS_VARIABLE[:12])
    first = classifier.evaluate(candles)
    extended = _candles(_CLOSES_TREND[:13], high_offsets=_HIGH_OFFSETS_VARIABLE[:13])
    second = classifier.evaluate(extended)
    assert first is not second
    assert second == classify_market_regime(extended, config)[-1]


def test_classifier_evaluate_matches_full_pipeline_last_bar() -> None:
    config = MarketRegimeConfig(period=4, chaos_window=8)
    classifier = MarketRegimeClassifier(config)
    candles = _candles(_CLOSES_OSCILLATION[:20], high_offsets=_HIGH_OFFSETS_VARIABLE[:20])
    assert classifier.evaluate(candles) == classify_market_regime(candles, config)[-1]


def test_classifier_short_history_is_chaos_true() -> None:
    """Короткая история индекса -- fail-closed, is_chaos=True (docs задачи)."""
    config = MarketRegimeConfig(period=14, chaos_window=500)
    classifier = MarketRegimeClassifier(config)
    candles = _candles([100.0, 101.0, 99.5], step_min=1)
    result = classifier.evaluate(candles)
    assert result.is_chaos is True
    assert result.chaos_score is None
    assert result.threshold is None


# --- RegimeBar: контракт результата ----------------------------------------------


def test_regime_bar_is_frozen_dataclass_with_expected_fields() -> None:
    bar = RegimeBar(chaos_score=Decimal("0.5"), threshold=Decimal("0.8"), regime="chaos")
    assert bar.chaos_score == Decimal("0.5")
    assert bar.threshold == Decimal("0.8")
    assert bar.regime == "chaos"
    assert bar.is_chaos is True  # удобный булев алиас regime == "chaos"
    with pytest.raises(AttributeError):
        bar.regime = "normal"  # type: ignore[misc]


def test_regime_bar_is_chaos_false_for_normal_regime() -> None:
    bar = RegimeBar(chaos_score=Decimal("0.1"), threshold=Decimal("0.8"), regime="normal")
    assert bar.is_chaos is False
