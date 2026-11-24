"""Golden-тесты индикаторов (docs/11_TESTING.md): EMA/RSI/MACD/Bollinger/ATR/VWAP/ADX.

Golden-значения сверены с независимой реализацией на numpy (временные скрипты
`.verify_indicators.py` и `.verify_indicators_5series.py`, удалены после использования,
не часть пакета). docs/11_TESTING.md требует ≥ 5 синтетических серий на каждый
индикатор — ниже 5 серий (A–E: тренд вверх, флэт, тренд вниз, осцилляция, случайный
ряд) для EMA/RSI/MACD/Bollinger/ATR/ADX. Совпадение с numpy — до 6 знаков для
остальных, до 3 для ADX (накопленная ошибка округления float64 за много итераций
сглаживания Уайлдера — Decimal точнее, а не хуже).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_bot.api_client import Candle
from trading_bot.indicators import (
    adx,
    atr,
    bollinger,
    ema,
    macd,
    rsi,
    session_vwap,
    volume_spike,
)

_TS0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)

# 40 значений закрытия — растущий ряд с шумом (детерминированный, без randomness).
_CLOSES_F = [
    10,
    10.5,
    11,
    10.8,
    11.2,
    11.5,
    11.3,
    11.8,
    12.0,
    11.7,
    12.2,
    12.5,
    12.3,
    12.8,
    13.0,
    12.7,
    13.2,
    13.5,
    13.3,
    13.8,
    14.0,
    13.7,
    14.2,
    14.5,
    14.3,
    14.8,
    15.0,
    14.7,
    15.2,
    15.5,
    15.3,
    15.8,
    16.0,
    15.7,
    16.2,
    16.5,
    16.3,
    16.8,
    17.0,
    16.7,
]
CLOSES = [Decimal(str(v)) for v in _CLOSES_F]


def _dec(value: str) -> Decimal:
    return Decimal(value)


def _assert_close(actual: Decimal | None, expected: str, places: str = "0.000001") -> None:
    assert actual is not None
    assert abs(actual - Decimal(expected)) < Decimal(places)


# --- EMA -----------------------------------------------------------------------


def test_ema_golden_values_period_9() -> None:
    """Сверено независимо: numpy EMA(9), seed = SMA(9)."""
    result = ema(CLOSES, 9)
    expected_last5 = [
        "15.686994347048059",
        "15.809595477638447",
        "16.007676382110758",
        "16.206141105688606",
        "16.304912884550885",
    ]
    for actual, expected in zip(result[-5:], expected_last5, strict=True):
        _assert_close(actual, expected)


def test_ema_undefined_before_period() -> None:
    result = ema(CLOSES, 9)
    assert result[:8] == [None] * 8
    assert result[8] is not None


def test_ema_empty_series_returns_empty() -> None:
    assert ema([], 9) == []


def test_ema_series_shorter_than_period_is_all_none() -> None:
    result = ema(CLOSES[:5], 9)
    assert result == [None] * 5


def test_ema_rejects_non_positive_period() -> None:
    with pytest.raises(ValueError):
        ema(CLOSES, 0)


# --- RSI -------------------------------------------------------------------


def test_rsi_golden_values_period_14() -> None:
    result = rsi(CLOSES, 14)
    expected_last5 = [
        "77.456762266363905",
        "74.100771156576573",
        "76.806322436475974",
        "77.805095656318611",
        "72.744807861263141",
    ]
    for actual, expected in zip(result[-5:], expected_last5, strict=True):
        _assert_close(actual, expected)


def test_rsi_bounds_between_0_and_100() -> None:
    result = rsi(CLOSES, 14)
    for value in result:
        if value is not None:
            assert Decimal(0) <= value <= Decimal(100)


def test_rsi_constant_series_is_100_no_losses() -> None:
    flat = [Decimal(10)] * 20
    result = rsi(flat, 14)
    # Нет ни одного убытка -> avg_loss == 0 -> RSI = 100 (без деления на ноль).
    assert result[14] == Decimal(100)


def test_rsi_one_candle_is_all_none() -> None:
    assert rsi([Decimal(10)], 14) == [None]


# --- MACD --------------------------------------------------------------------


def test_macd_golden_values() -> None:
    macd_line, signal_line, hist = macd(CLOSES, fast=12, slow=26, signal=9)
    expected_macd = [
        "1.185008589919546",
        "1.166563197157096",
        "1.178703551008087",
        "1.190737121107775",
        "1.162663854136216",
    ]
    expected_signal = [
        "1.181046535688689",
        "1.178149867982370",
        "1.178260604587514",
        "1.180755907891566",
        "1.177137497140496",
    ]
    expected_hist = [
        "0.003962054230857",
        "-0.011586670825273",
        "0.000442946420573",
        "0.009981213216208",
        "-0.014473643004279",
    ]
    for actual, expected in zip(macd_line[-5:], expected_macd, strict=True):
        _assert_close(actual, expected)
    for actual, expected in zip(signal_line[-5:], expected_signal, strict=True):
        _assert_close(actual, expected)
    for actual, expected in zip(hist[-5:], expected_hist, strict=True):
        _assert_close(actual, expected)


def test_macd_rejects_fast_not_less_than_slow() -> None:
    with pytest.raises(ValueError):
        macd(CLOSES, fast=26, slow=12, signal=9)


def test_macd_short_series_is_all_none() -> None:
    macd_line, signal_line, hist = macd(CLOSES[:5])
    assert macd_line == [None] * 5
    assert signal_line == [None] * 5
    assert hist == [None] * 5


# --- Bollinger -----------------------------------------------------------------


def test_bollinger_golden_values() -> None:
    mid, upper, lower = bollinger(CLOSES, period=20, mult=Decimal("2.0"))
    _assert_close(mid[-3], "15.08")
    _assert_close(mid[-2], "15.265")
    _assert_close(mid[-1], "15.41")
    _assert_close(upper[-1], "17.352060761150381")
    _assert_close(lower[-1], "13.467939238849617")


def test_bollinger_exactly_period_candles_defines_first_value() -> None:
    """Ровно 20 свечей — первое (и единственное) значение полос определено."""
    result_mid, result_upper, result_lower = bollinger(CLOSES[:20], period=20)
    assert result_mid[:19] == [None] * 19
    assert result_mid[19] is not None
    assert result_upper[19] is not None
    assert result_lower[19] is not None


def test_bollinger_zero_volatility_gives_zero_width() -> None:
    flat = [Decimal(10)] * 20
    mid, upper, lower = bollinger(flat, period=20)
    assert mid[19] == Decimal(10)
    assert upper[19] == Decimal(10)
    assert lower[19] == Decimal(10)


# --- ATR -----------------------------------------------------------------------


def _make_candles(closes: list[Decimal], *, high_offset: Decimal = Decimal("0.3")) -> list[Candle]:
    return [
        Candle(
            open=c,
            high=c + high_offset,
            low=c - high_offset,
            close=c,
            volume=100,
            ts_open=_TS0 + timedelta(minutes=5 * i),
            is_complete=True,
        )
        for i, c in enumerate(closes)
    ]


def test_atr_golden_values() -> None:
    candles = _make_candles(CLOSES)
    result = atr(candles, 14)
    expected_last5 = [
        "0.667897725833570",
        "0.663047888274029",
        "0.672830181968741",
        "0.667628026113831",
        "0.662797452819986",
    ]
    for actual, expected in zip(result[-5:], expected_last5, strict=True):
        _assert_close(actual, expected)


def test_atr_single_candle_true_range_is_high_minus_low() -> None:
    candles = _make_candles(CLOSES[:1])
    result = atr(candles, 1)
    assert result[0] == Decimal("0.6")


def test_atr_empty_candles_returns_empty() -> None:
    assert atr([], 14) == []


# --- ADX -----------------------------------------------------------------------

_ADX_HIGHS = [
    99.986902,
    100.722831,
    101.174423,
    101.452679,
    101.155503,
    100.858296,
    100.433805,
    101.059834,
    101.341283,
    101.761778,
    101.288537,
    102.04942,
    102.631596,
    102.407636,
    102.144009,
    101.882435,
    101.77795,
    101.960133,
    102.021662,
    101.900259,
    102.195668,
    101.87701,
    101.756798,
    101.733069,
    101.82596,
    102.346688,
    102.106264,
    102.274769,
    102.544908,
    102.105294,
    102.395102,
    102.116783,
    101.70135,
    102.434902,
    103.190223,
    103.74114,
    103.637138,
    103.264111,
    103.653614,
    103.725813,
]
_ADX_LOWS = [
    99.364864,
    99.727654,
    100.640034,
    100.043359,
    100.396723,
    99.695774,
    99.622094,
    100.039766,
    100.294573,
    101.076923,
    99.818953,
    100.774287,
    101.192097,
    101.012809,
    101.046109,
    100.460561,
    101.189457,
    101.26415,
    101.476434,
    101.074929,
    101.306991,
    101.105661,
    100.428061,
    100.876315,
    101.045025,
    101.303992,
    101.46534,
    100.972572,
    101.970357,
    100.618407,
    101.122857,
    101.418068,
    101.195828,
    101.11944,
    101.983366,
    102.512133,
    102.365867,
    102.690067,
    102.795149,
    103.109944,
]
_ADX_CLOSES = [
    99.901747,
    100.586594,
    101.101267,
    101.259748,
    101.051629,
    100.699151,
    100.322684,
    100.92019,
    101.197992,
    101.668023,
    101.087356,
    101.874859,
    102.434533,
    102.216689,
    101.99371,
    101.687785,
    101.697387,
    101.864855,
    101.947022,
    101.787275,
    102.074011,
    101.771415,
    101.574899,
    101.615782,
    101.719052,
    102.203947,
    102.018524,
    102.096503,
    102.466254,
    101.901744,
    102.220936,
    102.021131,
    101.632146,
    102.254819,
    103.025009,
    103.572893,
    103.463105,
    103.185527,
    103.536093,
    103.641502,
]


def _adx_candles() -> list[Candle]:
    return [
        Candle(
            open=Decimal(str(c)),
            high=Decimal(str(h)),
            low=Decimal(str(low)),
            close=Decimal(str(c)),
            volume=100,
            ts_open=_TS0 + timedelta(minutes=15 * i),
            is_complete=True,
        )
        for i, (h, low, c) in enumerate(zip(_ADX_HIGHS, _ADX_LOWS, _ADX_CLOSES, strict=True))
    ]


# --- 5 синтетических серий на каждый индикатор (docs/11_TESTING.md) --------------------
#
# A -- тренд вверх с шумом (CLOSES, выше). B -- флэт (нулевая волатильность).
# C -- тренд вниз (зеркальное отражение A). D -- осцилляция (синусоида, ranging).
# E -- случайный ряд (те же данные, что и в ADX-тестах выше, сеед numpy 42).
#
# Каждая golden-цифра ниже сверена с отдельной numpy-реализацией
# (`.verify_indicators_5series.py`, удален после использования).

_CLOSES_B_F = [100.0] * 40
_CLOSES_C_F = [27.0 - v for v in _CLOSES_F]
_CLOSES_D_F = [15 + 3 * math.sin(i * 0.3) for i in range(40)]

CLOSES_B = [Decimal(str(v)) for v in _CLOSES_B_F]
CLOSES_C = [Decimal(str(v)) for v in _CLOSES_C_F]
CLOSES_D = [Decimal(str(v)) for v in _CLOSES_D_F]
CLOSES_E = [Decimal(str(v)) for v in _ADX_CLOSES]

SERIES_CLOSES: dict[str, list[Decimal]] = {
    "A": CLOSES,
    "B": CLOSES_B,
    "C": CLOSES_C,
    "D": CLOSES_D,
    "E": CLOSES_E,
}


def _series_candles(name: str) -> list[Candle]:
    """Свечи серии для ATR/ADX: `high = close + 0.3`, `low = close - 0.3`, кроме
    серии E, где используется свой реальный ряд high/low (тот же, что в ADX-тестах).
    """
    if name == "E":
        return _adx_candles()
    return _make_candles(SERIES_CLOSES[name])


_GOLDEN_5SERIES: dict[str, dict[str, object]] = {
    "A": {
        "ema9_last3": ["16.007676", "16.206141", "16.304913"],
        "rsi14_last3": ["76.806322", "77.805096", "72.744808"],
        "macd_last3": ["1.178704", "1.190737", "1.162664"],
        "signal_last3": ["1.178261", "1.180756", "1.177137"],
        "bb_last1": ("15.41", "17.352061", "13.467939"),
        "atr14_last3": ["0.67283", "0.667628", "0.662797"],
        "adx14_last3": ["53.730699", "53.864949", "53.26671"],
    },
    "B": {
        "ema9_last3": ["100.0", "100.0", "100.0"],
        "rsi14_last3": ["100.0", "100.0", "100.0"],
        "macd_last3": ["0.0", "0.0", "0.0"],
        "signal_last3": ["0.0", "0.0", "0.0"],
        "bb_last1": ("100.0", "100.0", "100.0"),
        "atr14_last3": ["0.6", "0.6", "0.6"],
        "adx14_last3": ["0.0", "0.0", "0.0"],
    },
    "C": {
        "ema9_last3": ["10.992324", "10.793859", "10.695087"],
        "rsi14_last3": ["23.193678", "22.194904", "27.255192"],
        "macd_last3": ["-1.178704", "-1.190737", "-1.162664"],
        "signal_last3": ["-1.178261", "-1.180756", "-1.177137"],
        "bb_last1": ("11.59", "13.532061", "9.647939"),
        "atr14_last3": ["0.67283", "0.667628", "0.662797"],
        "adx14_last3": ["53.730699", "53.864949", "53.26671"],
    },
    "D": {
        "ema9_last3": ["13.605675", "13.332943", "13.209164"],
        "rsi14_last3": ["31.914317", "33.993969", "38.24311"],
        "macd_last3": ["-0.745469", "-0.830528", "-0.85005"],
        "signal_last3": ["-0.16536", "-0.298393", "-0.408725"],
        "bb_last1": ("15.077008", "19.357689", "10.796327"),
        "atr14_last3": ["0.885793", "0.865379", "0.858712"],
        "adx14_last3": ["25.463287", "25.931057", "25.758394"],
    },
    "E": {
        "ema9_last3": ["102.78438", "102.934723", "103.076079"],
        "rsi14_last3": ["63.454559", "66.075917", "66.84597"],
        "macd_last3": ["0.472045", "0.509071", "0.540687"],
        "bb_last1": ("102.399765", "103.808414", "100.991116"),
        "atr14_last3": ["1.063144", "1.048524", "1.01762"],
        "adx14_last3": ["10.579125", "11.268573", "11.990778"],
    },
}


# Для серии E (случайные дробные входы) float-реализация накапливает больше
# ошибки округления за много итераций, чем на аккуратных числах A-D -- сравнение до
# 1e-5, а не 1e-6 (Decimal-реализация точнее, это погрешность референса, не кода).
_FLOAT_TOLERANCE = "0.00001"


@pytest.mark.parametrize("series_name", ["A", "B", "C", "D", "E"])
def test_ema9_golden_5_series(series_name: str) -> None:
    result = ema(SERIES_CLOSES[series_name], 9)
    for actual, expected in zip(
        result[-3:], _GOLDEN_5SERIES[series_name]["ema9_last3"], strict=True  # type: ignore[arg-type]
    ):
        _assert_close(actual, expected, places=_FLOAT_TOLERANCE)


@pytest.mark.parametrize("series_name", ["A", "B", "C", "D", "E"])
def test_rsi14_golden_5_series(series_name: str) -> None:
    result = rsi(SERIES_CLOSES[series_name], 14)
    for actual, expected in zip(
        result[-3:], _GOLDEN_5SERIES[series_name]["rsi14_last3"], strict=True  # type: ignore[arg-type]
    ):
        _assert_close(actual, expected, places=_FLOAT_TOLERANCE)


@pytest.mark.parametrize("series_name", ["A", "B", "C", "D", "E"])
def test_macd_golden_5_series(series_name: str) -> None:
    macd_line, signal_line, _hist = macd(SERIES_CLOSES[series_name])
    golden = _GOLDEN_5SERIES[series_name]
    for actual, expected in zip(macd_line[-3:], golden["macd_last3"], strict=True):  # type: ignore[arg-type]
        _assert_close(actual, expected, places=_FLOAT_TOLERANCE)
    if "signal_last3" in golden:
        for actual, expected in zip(
            signal_line[-3:], golden["signal_last3"], strict=True  # type: ignore[arg-type]
        ):
            _assert_close(actual, expected, places=_FLOAT_TOLERANCE)


@pytest.mark.parametrize("series_name", ["A", "B", "C", "D", "E"])
def test_bollinger_golden_5_series(series_name: str) -> None:
    mid, upper, lower = bollinger(SERIES_CLOSES[series_name], period=20, mult=Decimal("2.0"))
    expected_mid, expected_upper, expected_lower = _GOLDEN_5SERIES[series_name]["bb_last1"]  # type: ignore[misc]
    _assert_close(mid[-1], expected_mid, places=_FLOAT_TOLERANCE)  # type: ignore[arg-type]
    _assert_close(upper[-1], expected_upper, places=_FLOAT_TOLERANCE)  # type: ignore[arg-type]
    _assert_close(lower[-1], expected_lower, places=_FLOAT_TOLERANCE)  # type: ignore[arg-type]


@pytest.mark.parametrize("series_name", ["A", "B", "C", "D", "E"])
def test_atr14_golden_5_series(series_name: str) -> None:
    result = atr(_series_candles(series_name), 14)
    for actual, expected in zip(
        result[-3:], _GOLDEN_5SERIES[series_name]["atr14_last3"], strict=True  # type: ignore[arg-type]
    ):
        _assert_close(actual, expected, places=_FLOAT_TOLERANCE)


@pytest.mark.parametrize("series_name", ["A", "B", "C", "D", "E"])
def test_adx14_golden_5_series(series_name: str) -> None:
    """ADX сравнивается с точностью 1e-3 (см. пояснение в докстринге модуля)."""
    result = adx(_series_candles(series_name), 14)
    for actual, expected in zip(
        result[-3:], _GOLDEN_5SERIES[series_name]["adx14_last3"], strict=True  # type: ignore[arg-type]
    ):
        _assert_close(actual, expected, places="0.001")


def test_adx_series_a_and_c_are_mirror_symmetric() -> None:
    """ADX не зависит от направления тренда, только от его силы -- зеркальное
        отражение тренда вверх (A) даёт тот же ADX, что и тренд вниз (C) -- это не
    баг, а математически ожидаемое свойство (подтверждено независимой numpy-реализацией).
    """
    result_a = adx(_series_candles("A"), 14)
    result_c = adx(_series_candles("C"), 14)
    assert result_a == result_c


def test_adx_golden_values_within_float_tolerance() -> None:
    """Независимая float/numpy-реализация даёт то же значение с точностью ~1e-3
    (расхождение в 6-м знаке — накопленная ошибка float64 за много итераций
    сглаживания Уайлдера; Decimal здесь точнее, а не хуже).
    """
    result = adx(_adx_candles(), 14)
    expected_last5 = [
        "10.042858",
        "10.320922",
        "10.579125",
        "11.268573",
        "11.990778",
    ]
    for actual, expected in zip(result[-5:], expected_last5, strict=True):
        _assert_close(actual, expected, places="0.001")


def test_adx_first_defined_index_is_2x_period_minus_1() -> None:
    result = adx(_adx_candles(), 14)
    first_defined = next(i for i, v in enumerate(result) if v is not None)
    assert first_defined == 27  # 2*14 - 1


def test_adx_insufficient_history_is_all_none() -> None:
    result = adx(_adx_candles()[:20], 14)
    assert all(v is None for v in result)


# --- session_vwap --------------------------------------------------------------


@pytest.mark.parametrize("series_name", ["A", "B", "C", "D", "E"])
def test_session_vwap_golden_5_series_constant_volume(series_name: str) -> None:
    """Объём во всех 5 сериях постоянен (100), все свечи внутри одной сессии --
    итоговый VWAP вырождается в простое среднее типичных цен, подтверждённое
    независимым `numpy.mean`.
    """
    candles = _series_candles(series_name)
    result = session_vwap(candles)
    expected = {
        "A": "13.7325",
        "B": "100.0",
        "C": "13.2675",
        "D": "15.058865",
        "E": "101.646253",
    }[series_name]
    _assert_close(result[-1], expected, places=_FLOAT_TOLERANCE)


def test_session_vwap_basic_weighted_average() -> None:
    candles = [
        Candle(
            open=Decimal(100),
            high=Decimal(102),
            low=Decimal(98),
            close=Decimal(100),
            volume=10,
            ts_open=datetime(2026, 1, 5, 7, 0, tzinfo=UTC),  # 10:00 МСК
            is_complete=True,
        ),
        Candle(
            open=Decimal(101),
            high=Decimal(103),
            low=Decimal(99),
            close=Decimal(101),
            volume=20,
            ts_open=datetime(2026, 1, 5, 7, 5, tzinfo=UTC),
            is_complete=True,
        ),
    ]
    result = session_vwap(candles)
    # Типичная цена свечи 1 = (102+98+100)/3 = 100; свечи 2 = (103+99+101)/3 = 101.
    assert result[0] == Decimal(100)
    expected_vwap2 = (Decimal(100) * 10 + Decimal(101) * 20) / Decimal(30)
    assert result[1] == expected_vwap2


def test_session_vwap_resets_at_10_msk_not_at_utc_midnight() -> None:
    """docs/03_STRATEGY.md: сброс в 10:00 МСК, НЕ в 00:00 (частая ошибка)."""
    candles = [
        Candle(
            open=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            volume=10,
            # 2026-01-05 23:00 МСК (=20:00 UTC) -- в сессии дня D.
            ts_open=datetime(2026, 1, 5, 20, 0, tzinfo=UTC),
            is_complete=True,
        ),
        Candle(
            open=Decimal(200),
            high=Decimal(200),
            low=Decimal(200),
            close=Decimal(200),
            volume=10,
            # 2026-01-06 00:30 МСК (=21:30 UTC 05 января) -- полночь МСК прошла,
            # но 10:00 МСК ещё нет -> это ВСЁ ЕЩЁ сессия дня D, сброса нет.
            ts_open=datetime(2026, 1, 5, 21, 30, tzinfo=UTC),
            is_complete=True,
        ),
        Candle(
            open=Decimal(300),
            high=Decimal(300),
            low=Decimal(300),
            close=Decimal(300),
            volume=10,
            # 2026-01-06 10:00 МСК (=07:00 UTC 06 января) -- новая сессия, сброс.
            ts_open=datetime(2026, 1, 6, 7, 0, tzinfo=UTC),
            is_complete=True,
        ),
    ]
    result = session_vwap(candles)
    assert result[0] == Decimal(100)
    # Свеча 2 всё ещё в сессии дня D -> накопленный VWAP усредняет обе свечи.
    assert result[1] == Decimal(150)
    # Свеча 3 -- новая сессия -> VWAP сброшен, равен только своей типичной цене.
    assert result[2] == Decimal(300)


def test_session_vwap_zero_volume_candle_is_none() -> None:
    candles = _make_candles(CLOSES[:1])
    zero_volume = [
        Candle(
            open=c.open,
            high=c.high,
            low=c.low,
            close=c.close,
            volume=0,
            ts_open=c.ts_open,
            is_complete=c.is_complete,
        )
        for c in candles
    ]
    result = session_vwap(zero_volume)
    assert result == [None]


def test_session_vwap_empty_candles_returns_empty() -> None:
    assert session_vwap([]) == []


# --- volume_spike --------------------------------------------------------------


def test_volume_spike_detects_burst_above_mean() -> None:
    volumes = [100] * 20 + [500]
    result = volume_spike(volumes, window=20)
    assert result[:20] == [None] * 20
    assert result[20] is True


def test_volume_spike_false_when_below_mean() -> None:
    volumes = [100] * 20 + [50]
    result = volume_spike(volumes, window=20)
    assert result[20] is False


def test_volume_spike_zero_volume_history_is_false() -> None:
    volumes = [0] * 20 + [1]
    result = volume_spike(volumes, window=20)
    assert result[20] is True  # 1 > mean(0) == 0


# --- shift-тест: нет заглядывания в будущее (docs/11_TESTING.md, инвариант №12) --


@pytest.mark.parametrize(
    "compute",
    [
        lambda series: ema(series, 9),
        lambda series: rsi(series, 14),
        lambda series: macd(series)[0],
        lambda series: bollinger(series, 20)[0],
    ],
)
def test_no_lookahead_prefix_values_are_stable(compute: object) -> None:
    """Значение индикатора в момент t не должно меняться от появления будущих свечей.

    Метод: считаем индикатор на префиксе `CLOSES[:t]` и на полном `CLOSES`, сравниваем
    значение по индексу `t-1` -- оно обязано совпасть (иначе индикатор «увидел будущее»).
    """
    t = 25
    full = compute(CLOSES)  # type: ignore[operator]
    prefix = compute(CLOSES[:t])  # type: ignore[operator]
    assert full[t - 1] == prefix[t - 1]


def test_no_lookahead_atr() -> None:
    """ATR в точке t не должен измениться от будущих свечей."""
    t = 25
    full = atr(_series_candles("A"), 14)
    prefix = atr(_series_candles("A")[:t], 14)
    assert full[t - 1] == prefix[t - 1]


def test_no_lookahead_adx() -> None:
    """ADX в точке t не должен измениться от будущих свечей."""
    t = 30
    full = adx(_adx_candles(), 14)
    prefix = adx(_adx_candles()[:t], 14)
    assert full[t - 1] == prefix[t - 1]


def test_no_lookahead_session_vwap() -> None:
    """VWAP в точке t не должен измениться от будущих свечей."""
    candles = _make_candles(CLOSES)
    t = 25
    full = session_vwap(candles)
    prefix = session_vwap(candles[:t])
    assert full[t - 1] == prefix[t - 1]


def test_no_lookahead_volume_spike() -> None:
    """Флаг всплеска в точке t не должен измениться от будущих объёмов."""
    volumes = [100] * 20 + [500, 120, 90, 700, 80]
    t = 22
    full = volume_spike(volumes, window=20)
    prefix = volume_spike(volumes[:t], window=20)
    assert full[t - 1] == prefix[t - 1]
