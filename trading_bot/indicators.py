"""Технические индикаторы (слой L1, docs/01_ARCHITECTURE.md, docs/03_STRATEGY.md).

Разрешено: считать EMA/RSI/MACD/Bollinger/ATR/VWAP/ADX и объёмные всплески по
списку `api_client.Candle`. Каждое значение серии использует только текущую и
предыдущие свечи — никакого заглядывания в будущее (docs/03_STRATEGY.md, «Нет
заглядывания в будущее»; docs/11_TESTING.md, инвариант №12).
Запрещено: знать о портфеле/стратегии/брокере; решать, входить в позицию или нет —
это `strategy.py` выше по стеку.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal

from trading_bot.api_client import Candle
from trading_bot.utils import MSK, SESSION_TRADING_START

#: Индексы серии без определённого значения (нет истории) помечаются `None`,
#: а не 0/NaN — вызывающий код обязан явно решить, что делать с «пока неизвестно»
#: (docs/02_CODING_RULES.md, деньги/данные не подставляются «на всякий случай»).
Series = list[Decimal | None]


def _require_positive_period(period: int) -> None:
    if period <= 0:
        raise ValueError(f"period должен быть положительным, получено {period}")


def ema(values: Sequence[Decimal], period: int) -> Series:
    """EMA (Exponential Moving Average): первое значение — SMA(period), далее рекурсия.

    Возврат: серия длины `len(values)`, первые `period - 1` элементов — `None`.
    Отказ: `ValueError` при `period <= 0`.
    """
    _require_positive_period(period)
    n = len(values)
    result: Series = [None] * n
    if n < period:
        return result

    k = Decimal(2) / Decimal(period + 1)
    seed = sum(values[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = values[i] * k + prev * (Decimal(1) - k)
        result[i] = prev
    return result


def rsi(values: Sequence[Decimal], period: int = 14) -> Series:
    """RSI(14) по методу Уайлдера (Wilder smoothing), а не простое SMA сглаживание.

    Возврат: серия длины `len(values)`; первые `period` элементов — `None`
    (нужно `period` приростов, то есть `period + 1` цен).
    """
    _require_positive_period(period)
    n = len(values)
    result: Series = [None] * n
    if n < period + 1:
        return result

    gains = Decimal(0)
    losses = Decimal(0)
    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        gains += max(diff, Decimal(0))
        losses += max(-diff, Decimal(0))
    avg_gain = gains / Decimal(period)
    avg_loss = losses / Decimal(period)

    def _rsi_from_averages(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
        if avg_loss == 0:
            return Decimal(100)
        rs = avg_gain / avg_loss
        return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))

    result[period] = _rsi_from_averages(avg_gain, avg_loss)
    for i in range(period + 1, n):
        diff = values[i] - values[i - 1]
        gain = max(diff, Decimal(0))
        loss = max(-diff, Decimal(0))
        avg_gain = (avg_gain * (period - 1) + gain) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + loss) / Decimal(period)
        result[i] = _rsi_from_averages(avg_gain, avg_loss)
    return result


def macd(
    values: Sequence[Decimal],
    *,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[Series, Series, Series]:
    """MACD(12,26,9): линия MACD, сигнальная линия, гистограмма.

    Возврат: `(macd_line, signal_line, histogram)`, каждая — серия длины `len(values)`.
    """
    if fast >= slow:
        raise ValueError("fast должен быть меньше slow")
    n = len(values)
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)

    macd_line: Series = [None] * n
    for i in range(n):
        f, s = ema_fast[i], ema_slow[i]
        if f is not None and s is not None:
            macd_line[i] = f - s

    valid_indices = [i for i, v in enumerate(macd_line) if v is not None]
    valid_values: list[Decimal] = [v for v in macd_line if v is not None]
    signal_sub = ema(valid_values, signal)

    signal_line: Series = [None] * n
    for idx, val in zip(valid_indices, signal_sub, strict=True):
        signal_line[idx] = val

    histogram: Series = [None] * n
    for i in range(n):
        m, sig = macd_line[i], signal_line[i]
        if m is not None and sig is not None:
            histogram[i] = m - sig

    return macd_line, signal_line, histogram


def bollinger(
    values: Sequence[Decimal], period: int = 20, mult: Decimal = Decimal("2.0")
) -> tuple[Series, Series, Series]:
    """Полосы Боллинджера(20, 2): средняя, верхняя, нижняя.

    Возврат: `(middle, upper, lower)`, каждая — серия длины `len(values)`.
    """
    _require_positive_period(period)
    n = len(values)
    middle: Series = [None] * n
    upper: Series = [None] * n
    lower: Series = [None] * n

    for i in range(period - 1, n):
        window = values[i - period + 1 : i + 1]
        mean = sum(window, Decimal(0)) / Decimal(period)
        variance = sum(((x - mean) ** 2 for x in window), Decimal(0)) / Decimal(period)
        std = variance.sqrt()
        middle[i] = mean
        upper[i] = mean + mult * std
        lower[i] = mean - mult * std
    return middle, upper, lower


def _true_range(current: Candle, previous: Candle | None) -> Decimal:
    if previous is None:
        return current.high - current.low
    return max(
        current.high - current.low,
        abs(current.high - previous.close),
        abs(current.low - previous.close),
    )


def atr(candles: Sequence[Candle], period: int = 14) -> Series:
    """ATR(14) по методу Уайлдера, на основе True Range.

    Возврат: серия длины `len(candles)`; первые `period - 1` элементов — `None`.
    """
    _require_positive_period(period)
    n = len(candles)
    result: Series = [None] * n
    if n < period:
        return result

    true_ranges = [_true_range(candles[i], candles[i - 1] if i > 0 else None) for i in range(n)]
    seed = sum(true_ranges[:period], Decimal(0)) / Decimal(period)
    result[period - 1] = seed
    prev = seed
    for i in range(period, n):
        prev = (prev * (period - 1) + true_ranges[i]) / Decimal(period)
        result[i] = prev
    return result


def _session_key(ts: datetime) -> date:
    """Ключ торговой сессии MOEX: сброс ровно в 10:00 МСК, не в полночь (docs/03_STRATEGY.md).

    Время с 00:00 до 09:59:59 МСК относится к сессии, начавшейся накануне в 10:00 —
    это разделяет реальный сброс (10:00 МСК) от частой ошибки «сброс в 00:00».
    """
    msk = ts.astimezone(MSK)
    if msk.time() >= SESSION_TRADING_START:
        return msk.date()
    return msk.date() - timedelta(days=1)


def session_vwap(candles: Sequence[Candle]) -> Series:
    """VWAP с начала торговой сессии, сбрасывается в 10:00 МСК (docs/03_STRATEGY.md).

    Типичная цена свечи — `(high + low + close) / 3`. Возврат: серия длины
    `len(candles)`; `None` только если накопленный объём в сессии равен нулю.
    """
    result: Series = []
    cumulative_pv = Decimal(0)
    cumulative_volume = Decimal(0)
    current_key: date | None = None

    for candle in candles:
        key = _session_key(candle.ts_open)
        if key != current_key:
            current_key = key
            cumulative_pv = Decimal(0)
            cumulative_volume = Decimal(0)
        typical_price = (candle.high + candle.low + candle.close) / Decimal(3)
        cumulative_pv += typical_price * Decimal(candle.volume)
        cumulative_volume += Decimal(candle.volume)
        result.append(cumulative_pv / cumulative_volume if cumulative_volume > 0 else None)
    return result


def adx(candles: Sequence[Candle], period: int = 14) -> Series:
    """ADX(14) по методу Уайлдера (+DM/-DM, TR, DX, сглаженный ADX).

    Возврат: серия длины `len(candles)`; определена начиная с индекса `2*period - 1`
    (нужно `period` шагов на DX плюс ещё `period` шагов на сглаживание самого ADX).
    """
    _require_positive_period(period)
    n = len(candles)
    result: Series = [None] * n
    if n < 2 * period:
        return result

    plus_dm = [Decimal(0)] * n
    minus_dm = [Decimal(0)] * n
    true_ranges = [Decimal(0)] * n
    for i in range(1, n):
        up_move = candles[i].high - candles[i - 1].high
        down_move = candles[i - 1].low - candles[i].low
        plus_dm[i] = up_move if (up_move > down_move and up_move > 0) else Decimal(0)
        minus_dm[i] = down_move if (down_move > up_move and down_move > 0) else Decimal(0)
        true_ranges[i] = _true_range(candles[i], candles[i - 1])

    smoothed_tr = sum(true_ranges[1 : period + 1], Decimal(0))
    smoothed_plus = sum(plus_dm[1 : period + 1], Decimal(0))
    smoothed_minus = sum(minus_dm[1 : period + 1], Decimal(0))

    def _dx(smoothed_tr: Decimal, smoothed_plus: Decimal, smoothed_minus: Decimal) -> Decimal:
        if smoothed_tr == 0:
            return Decimal(0)
        di_plus = Decimal(100) * smoothed_plus / smoothed_tr
        di_minus = Decimal(100) * smoothed_minus / smoothed_tr
        denom = di_plus + di_minus
        if denom == 0:
            return Decimal(0)
        return Decimal(100) * abs(di_plus - di_minus) / denom

    dx_values = [_dx(smoothed_tr, smoothed_plus, smoothed_minus)]
    dx_indices = [period]
    for i in range(period + 1, n):
        smoothed_tr = smoothed_tr - smoothed_tr / Decimal(period) + true_ranges[i]
        smoothed_plus = smoothed_plus - smoothed_plus / Decimal(period) + plus_dm[i]
        smoothed_minus = smoothed_minus - smoothed_minus / Decimal(period) + minus_dm[i]
        dx_values.append(_dx(smoothed_tr, smoothed_plus, smoothed_minus))
        dx_indices.append(i)

    if len(dx_values) < period:
        return result

    first_adx = sum(dx_values[:period], Decimal(0)) / Decimal(period)
    result[dx_indices[period - 1]] = first_adx
    prev_adx = first_adx
    for j in range(period, len(dx_values)):
        prev_adx = (prev_adx * (period - 1) + dx_values[j]) / Decimal(period)
        result[dx_indices[j]] = prev_adx
    return result


def volume_spike(volumes: Sequence[int], window: int = 20) -> list[bool | None]:
    """Объёмный всплеск: `volume[i] > mean(volume[i-window:i])` (docs/03_STRATEGY.md, п.5).

    Скользящее среднее не включает текущий объём — иначе всплеск «подтверждает сам себя».
    Возврат: серия длины `len(volumes)`; первые `window` элементов — `None` (нет истории).
    """
    _require_positive_period(window)
    n = len(volumes)
    result: list[bool | None] = [None] * n
    for i in range(window, n):
        history = volumes[i - window : i]
        mean_volume = Decimal(sum(history)) / Decimal(window)
        result[i] = Decimal(volumes[i]) > mean_volume
    return result
