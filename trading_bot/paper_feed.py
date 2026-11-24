"""Production-поставщик paper-тиков (слой L4, docs/01_ARCHITECTURE.md).

Разрешено: собрать реальный `executor.PaperTick` из инъецированных read-only
`api_client.InvestGateway`, `paper_account.PaperAccountSource`,
`market_regime.MarketRegimeClassifier`, `strategy.StrategyConfig`,
выбранного paper-пула, подтверждённого индекса, monitoring-only тикеров
и источника времени (`clock`). Все
факты — явно запрошенные данные шлюза (`get_share_metadata`,
`get_candles_history`, `get_order_book_quote`, `get_trading_status`,
`utils.can_trade_now`) или производные от них чистые вычисления.

Запрещено: выдумывать значения контекста, если данных не хватает или они не
прошли валидацию (AGENTS.md §3); вызывать торговый SDK (здесь нет и не может
быть заявок — только чтение через `InvestGateway`); править `executor.py`/
`strategy.py`/`api_client.py`/`pool.py`/`main.py`/`paper_account.py` —
только читать их публичный контракт.

## Контракт `next_paper_tick()`/`ack()` (важно для встраивания в `main.py`)

`next_paper_tick()` возвращает `PaperTick | SourceRefusal | None`
(`trading_bot.source_events.SourceRefusal`). Дедупликация "один раз на бар на
тикер" держится не на факте выдачи тика, а на явном подтверждении: после того
как `PaperCycle.run_tick(tick)` успешно отработал по полученному `PaperTick`,
вызывающий код обязан вызвать `feed.ack(tick)`. Без этого вызова тот же
закрытый бар будет предложен повторно на следующем опросе того же тикера —
осознанный выбор в пользу «не потерять решение» (как дубль) важнее риска «не
задвоить» при корректной интеграции `ack()`. Интеграция с `main.py`
(вызов `ack()`, обработка `SourceRefusal.action`) сделана вне этого файла.

Если по конкретному тикеру-кандидату (в порядке round-robin по `self._cursor`)
что-то не удалось получить честно — метаданные/история/OHLCV, пустая или
протухшая 5m-история ИМЕННО в ожидаемую обычную сессию, стакан/статус/снимок
счёта/корреляция/старшие таймфреймы при построении тика — `next_paper_tick()`
**не бросает исключение и не пытается молча перебрать других тикеров в этом же
вызове**: курсор уже сдвинут на следующий тикер (следующий опрос обслужит
остальных), а этот вызов возвращает `SourceRefusal(ticker, stage, observed_at,
source_bar_ts, action, error_type)` — безопасный факт отказа без текста
исключения/секретов (`error_type` — только имя класса исключения).
`action="skip"` — восстановимый/легальный по смыслу отказ отдельного тикера
(шлюз недоступен для метаданных/истории, битый OHLCV, пустая/протухшая 5m-
история ИМЕННО в ожидаемую обычную сессию). `action="stop"` — отказ ПОСЛЕ того,
как новый закрытый бар уже подтверждён (стакан/снимок счёта/корреляция/старшие
таймфреймы в `_build_tick`) — решение по этому бару нельзя тихо потерять,
вызывающий код (вне этого файла) решает, останавливать ли процесс.
Этот модуль сам никогда не пишет `SourceRefusal` в journal/БД.

Если отдельный тикер пула (не текущий кандидат) недоступен для breadth, или
индекс недоступен/содержит битую историю для classifier — это не
инфраструктурный сбой кандидата, а легальное рыночное состояние: неполный
breadth/недоступный или битый индекс кодируются через
`strategy.MarketFilterResult(paused=True, market_regime="chaos", ...)`
(docs/03_STRATEGY.md §«хаос» — fail-closed при нехватке данных индекса,
строки 111-118) и идут в `PaperCycle` как обычный (журналируемый) отказ, а не
исключение и не `SourceRefusal`.

Валидное «нового бара ещё нет» (тот же бар уже подтверждён `ack()`) или легальный
офлайн-сессионный пробел в 5m-истории — не отказ и не повод для `SourceRefusal`:
вызов просто переходит к следующему тикеру того же опроса; если там исчерпаны
все тикеры — `None` (придуманный бар никогда не возвращается).

## Прочие явные допущения

- Индекс хаоса классифицируется на закрытых свечах `index_timeframe`
  (по умолчанию "15m"); `MarketFilterResult.chaos_score/chaos_threshold/
  market_regime` заполняются напрямую из `market_regime.RegimeBar`. Если
  индекс недоступен (сбой шлюза, нет истории) — `market_regime="chaos"`,
  `chaos_score`/`chaos_threshold` — `None` (числа не придумываются, но режим
  явно хаос, docs/03_STRATEGY.md о фильтре рынка — хаос это безопасный дефолт
  при нехватке данных, то же решение, что и в самом классификаторе).
- У `strategy.evaluate_market_breadth()` есть отдельный от хаоса индекса вход
  `deviation_std`/`chaos_std_threshold` — величина, которую этот поставщик не
  умеет честно посчитать (не специфицирована в этом задании). Чтобы не
  передавать туда выдуманное число, этот модуль НЕ вызывает
  `evaluate_market_breadth()`: при полном (по всем `approved_tickers`) и
  определённом (не-хаос) контексте применяется напрямую та же формула
  breadth/ATR из docs/03_STRATEGY.md (две строки, без std-ветки) — то есть
  параметр `deviation_std` просто не участвует, а не подставляется нулём.
- Частичный breadth (не по всем `approved_tickers` — часть недоступна у
  шлюза) не считается числовым фактом и не усредняется по доступным: он
  приравнивается к «неизвестно» и ведёт к паузе, как и полностью недоступный.
- Глубина истории (`lookback_*`) по умолчанию не подобрана на глаз: считается
  из констант сессии MOEX (`utils.SESSION_TRADING_START/END`) и периодов
  индикаторов/классификатора (`required_bars_*`/`_lookback_for_bars`).
  Явно переданный `lookback_*` меньше вычисленного минимума — `ValueError`
  при создании, не тихая работа на куцей истории.
- `available_lots` в `PaperMarketContext` остаётся `None` (документированный
  дефолт контракта) — у этого поставщика нет проверенного источника доступной
  для фактического исполнения ликвидности.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from functools import partial
from typing import Literal

from trading_bot.api_client import (
    Candle,
    GatewayError,
    InvestGateway,
    OrderBookQuote,
    ShareMetadata,
    Timeframe,
)
from trading_bot.config import DEFAULT_REGIME_REFRESH_MIN
from trading_bot.executor import PaperMarketContext, PaperTick, StrategyInputs
from trading_bot.history_workers import HistoryWorkers
from trading_bot.indicators import atr as _atr_series
from trading_bot.market_regime import MarketRegimeClassifier, RegimeBar
from trading_bot.paper_account import PaperAccountError, PaperAccountSource
from trading_bot.pool_refresh import PoolGate, Universe
from trading_bot.regime_schedule import (
    LocalRegimeSchedule,
    RegimeChanged,
    RegimeObservation,
)
from trading_bot.risk_manager import AccountState
from trading_bot.source_events import SourceRefusal
from trading_bot.strategy import MarketFilterResult, ScheduledRegime, StrategyConfig, gap_filter
from trading_bot.utils import (
    MSK,
    SESSION_ACCEPT_ORDERS_START,
    SESSION_CLOSING_AUCTION_START,
    SESSION_TRADING_START,
    can_trade_now,
    session_calendar_decision,
    session_guard_active,
)

logger = logging.getLogger(__name__)

_TIMEFRAME_DURATION: dict[Timeframe, timedelta] = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
}

#: Длительность регулярной сессии MOEX в минутах — реальные константы из
#: `utils.py` (docs/00_PLAN.md §4), не придуманное число.
#: Длительность ИМЕННО обычных торгов (без аукциона закрытия 23:40-23:50
#: МСК, docs/03_STRATEGY.md «фильтр 1»/docs/00_PLAN.md §4) — в аукционе обычных 5m-баров
#: нет, считать их там — завышать число доступных баров и занижать lookback.
_SESSION_MINUTES = (
    datetime.combine(date(2000, 1, 1), SESSION_CLOSING_AUCTION_START)
    - datetime.combine(date(2000, 1, 1), SESSION_TRADING_START)
).total_seconds() / 60

#: Запас на выходные/праздники при переводе числа баров в календарные дни:
#: 5 торговых дней из 7 календарных, плюс явный буфер на непредвиденные
#: нерабочие дни (праздники MOEX, docs/00_PLAN.md §4). Это множитель
#: календаря, а не "подобранное на глаз" число баров.
_WEEKDAY_FACTOR = Decimal(7) / Decimal(5)
_HOLIDAY_MARGIN_DAYS = 4

#: Время последнего возможного обычного 5m-бара сессии (закрывается ровно в
#: SESSION_CLOSING_AUCTION_START=23:40 МСК — не в SESSION_TRADING_END=23:50, это граница
#: аукциона закрытия, где обычных 5m-свечей уже нет (docs/03_STRATEGY.md «фильтр 1»).
_LAST_5M_BAR_TIME = (
    datetime.combine(date(2000, 1, 1), SESSION_CLOSING_AUCTION_START) - timedelta(minutes=5)
).time()


class PaperFeedError(Exception):
    """Базовая ошибка этого поставщика тиков."""


class InsufficientFeedDataError(PaperFeedError):
    """Нужная метрика (например, корреляция) не может быть посчитана честно."""


class StaleDataError(PaperFeedError):
    """Свежие данные (стакан/бар) старше явно заданного допуска (`max_*_staleness`).

    Не раздвигается и не округляется вызывающим кодом — отказ, а не догадка
    о том, что устаревшаяся цена «наверное» ещё актуальна.
    """


class EmptyBarHistoryError(PaperFeedError):
    """Нет ни одного закрытого 5m-бара у тикера, хотя ожидается обычная сессия.

    Используется только как имя для `SourceRefusal.error_type` (не всегда фактически
    выбрасывается — отсутствие истории во время ожидаемой сессии — условие, не try/except).
    """


def _bars_per_session(timeframe_minutes: int) -> Decimal:
    if timeframe_minutes <= 0:
        raise ValueError("timeframe_minutes должен быть положительным")
    return Decimal(_SESSION_MINUTES) / Decimal(timeframe_minutes)


def _lookback_for_bars(bars: int, *, timeframe_minutes: int) -> timedelta:
    """Переводит требуемое число баров таймфрейма в календарный запас истории."""
    if bars <= 0:
        raise ValueError("bars должен быть положительным")
    sessions_needed = Decimal(bars) / _bars_per_session(timeframe_minutes)
    calendar_days = (sessions_needed * _WEEKDAY_FACTOR).to_integral_value(rounding=ROUND_CEILING)
    return timedelta(days=int(calendar_days) + _HOLIDAY_MARGIN_DAYS)


def required_bars_5m(
    config: StrategyConfig, *, correlation_window: int, volume_avg_window: int
) -> int:
    """Минимум закрытых 5m-баров, нужных стратегии/этому поставщику (с запасом 5)."""
    return (
        max(
            config.ema_slow,
            config.bb_period,
            config.vol_window + 1,
            correlation_window + 1,
            volume_avg_window + 1,
        )
        + 5
    )


def required_bars_15m(config: StrategyConfig) -> int:
    """Минимум закрытых 15m-баров (EMA медленная, ADX ×2, MACD slow+signal)."""
    return max(config.ema_slow, config.adx_period * 2, config.macd_slow + config.macd_signal) + 5


def required_bars_1h(config: StrategyConfig) -> int:
    """Минимум закрытых 1h-баров (EMA медленная для mtf-согласования)."""
    return config.ema_slow + 5


def required_bars_index(classifier: MarketRegimeClassifier) -> int:
    """Минимум баров индекса для classifier (ATR-перцентиль на полном окне)."""
    cfg = classifier.config
    return cfg.period + cfg.chaos_window + cfg.confirm_bars + 5


def _default_lookback_5m(
    config: StrategyConfig, *, correlation_window: int, volume_avg_window: int
) -> timedelta:
    bars = required_bars_5m(
        config, correlation_window=correlation_window, volume_avg_window=volume_avg_window
    )
    return _lookback_for_bars(bars, timeframe_minutes=5)


def _default_lookback_15m(config: StrategyConfig) -> timedelta:
    return _lookback_for_bars(required_bars_15m(config), timeframe_minutes=15)


def _default_lookback_1h(config: StrategyConfig) -> timedelta:
    return _lookback_for_bars(required_bars_1h(config), timeframe_minutes=60)


def _default_index_lookback(
    classifier: MarketRegimeClassifier, *, timeframe: Timeframe
) -> timedelta:
    minutes = {"5m": 5, "15m": 15, "1h": 60}[timeframe]
    return _lookback_for_bars(required_bars_index(classifier), timeframe_minutes=minutes)


def spread_bps_from_quote(quote: OrderBookQuote) -> Decimal:
    """Спред в базисных пунктах от свежего стакана (докстринг `get_order_book_quote`)."""
    mid = (quote.best_bid + quote.best_ask) / Decimal(2)
    if mid <= 0:
        raise ValueError("средняя цена стакана должна быть положительной")
    return (quote.best_ask - quote.best_bid) / mid * Decimal(10000)


def average_volume_prior(volumes: Sequence[int], window: int) -> Decimal | None:
    """Средний объём `window` закрытых 5m-баров ПЕРЕД последним (без самого себя).

    Та же формула, что `strategy.compute_snapshot` (`volumes_5m[-window-1:-1]`):
    текущий бар не подтверждает сам себя. Возврат: `None`, если истории не
    хватает — не 0, чтобы вызывающий код не принял отсутствие данных за факт.
    """
    if window <= 0:
        raise ValueError("window должен быть положительным")
    if len(volumes) < window + 1:
        return None
    prior = volumes[-(window + 1) : -1]
    return Decimal(sum(prior)) / Decimal(window)


def _session_date(ts: datetime) -> date:
    """Ключ торговой сессии MOEX: сброс в 10:00 МСК (как `indicators._session_key`)."""
    msk = ts.astimezone(MSK)
    if msk.time() >= SESSION_TRADING_START:
        return msk.date()
    return msk.date() - timedelta(days=1)


def _session_open_bar(candles_5m: Sequence[Candle], trading_day: date) -> Candle | None:
    """Первый уже закрытый 10:00 МСК-бар указанного дня, либо None при пробеле."""
    session = [
        candle
        for candle in candles_5m
        if candle.ts_open.astimezone(MSK).date() == trading_day
        and candle.ts_open.astimezone(MSK).time() >= SESSION_TRADING_START
    ]
    if not session or session[0].ts_open.astimezone(MSK).time() != SESSION_TRADING_START:
        return None
    return session[0]


def session_open_and_prev_close(candles_5m: Sequence[Candle]) -> tuple[Candle, Candle] | None:
    """Первая 5m-свеча текущей сессии (её `open` - цена открытия дня) и последняя
    свеча предыдущей сессии (её `close` - вчерашнее закрытие). `None`, если в
    истории нет обеих сессий целиком, либо найденные свечи не стоят ровно на
    границе сессии (первая - не 10:00 МСК, последняя предыдущей - не последний
    обычный 5m-бар перед аукционом 23:40 МСК): без этого цена открытия/закрытия дня -
    выдумка по случайной свече, а не проверенная граница сессии.
    """
    if not candles_5m:
        return None
    current_session = _session_date(candles_5m[-1].ts_open)
    current = [c for c in candles_5m if _session_date(c.ts_open) == current_session]
    previous = [c for c in candles_5m if _session_date(c.ts_open) < current_session]
    if not current or not previous:
        return None
    opening_candle, closing_candle = current[0], previous[-1]
    if (
        opening_candle.ts_open.astimezone(MSK).time() != SESSION_TRADING_START
        or closing_candle.ts_open.astimezone(MSK).time() != _LAST_5M_BAR_TIME
    ):
        return None
    return opening_candle, closing_candle


def compute_gap(
    candles_5m: Sequence[Candle], *, now: datetime, config: StrategyConfig
) -> tuple[bool, Decimal] | None:
    """Гэп сегодняшней сессии через `strategy.gap_filter`; `None` без обеих сессий."""
    found = session_open_and_prev_close(candles_5m)
    if found is None:
        return None
    session_open, prev_close_candle = found
    return gap_filter(
        now=now,
        session_open_price=session_open.open,
        prev_session_close=prev_close_candle.close,
        config=config,
    )


def pool_breadth(latest_pairs: Mapping[str, tuple[Decimal, Decimal]]) -> Decimal:
    """Доля тикеров с закрытием выше предыдущего. Требует хотя бы одну пару —
    пустой/частичный охват пула решает вызывающий код (`_market_filter`), не эта
    функция.
    """
    if not latest_pairs:
        raise ValueError("latest_pairs не может быть пустым")
    rising = sum(1 for latest, previous in latest_pairs.values() if latest > previous)
    return Decimal(rising) / Decimal(len(latest_pairs))


def index_atr_rising(candles: Sequence[Candle], period: int) -> bool | None:
    """Растёт ли ATR индекса на последнем закрытом баре. `None` без двух значений."""
    series = _atr_series(candles, period)
    if len(series) < 2 or series[-1] is None or series[-2] is None:
        return None
    return series[-1] > series[-2]


def _returns_by_ts(
    candles: Sequence[Candle], *, window: int, bar_duration: timedelta = _TIMEFRAME_DURATION["5m"]
) -> dict[datetime, Decimal]:
    """Последние `window` доходностей close-to-close, выровненных по `ts_open`.

    Требует полное окно `window` БЕЗ дыр (соседние свечи хвоста ровно `bar_duration`
    друг от друга) — пропущенный бар между двумя свечами исказил бы многобаровую
    доходность как однобаровую. При разрыве возвращает пустой словарь (недостаточно
    достоверного окна, а не частичный набор возвратов).
    """
    if len(candles) < window + 1:
        return {}
    tail = candles[-(window + 1) :]
    for i in range(1, len(tail)):
        if tail[i].ts_open - tail[i - 1].ts_open != bar_duration:
            return {}
    result: dict[datetime, Decimal] = {}
    for i in range(1, len(tail)):
        prev_close = tail[i - 1].close
        if prev_close == 0:
            continue
        result[tail[i].ts_open] = tail[i].close / prev_close - Decimal(1)
    return result


def pearson_correlation(xs: Sequence[Decimal], ys: Sequence[Decimal]) -> Decimal | None:
    """Pearson-корреляция двух выровненных рядов; `None` при вырожденной дисперсии."""
    n = len(xs)
    if n != len(ys) or n < 2:
        return None
    mean_x = sum(xs, Decimal(0)) / Decimal(n)
    mean_y = sum(ys, Decimal(0)) / Decimal(n)
    cov = sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)), Decimal(0))
    var_x = sum(((x - mean_x) ** 2 for x in xs), Decimal(0))
    var_y = sum(((y - mean_y) ** 2 for y in ys), Decimal(0))
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / (var_x * var_y).sqrt()


def max_abs_correlation_with_open(
    *,
    current_returns: Mapping[datetime, Decimal],
    open_returns: Mapping[str, Mapping[datetime, Decimal]],
) -> Decimal:
    """Максимум |corr| с доходностями открытых позиций; `0`, только если позиций нет.

    Отказ: `InsufficientFeedDataError`, если для какой-то открытой позиции нет
    хотя бы двух общих выровненных баров или дисперсия вырождена — нет открытой
    позиции без посчитанной (не фиктивной) корреляции.
    """
    if not open_returns:
        return Decimal(0)
    correlations: list[Decimal] = []
    for ticker, other in open_returns.items():
        common = sorted(set(current_returns) & set(other))
        if len(common) < 2:
            raise InsufficientFeedDataError(
                f"недостаточно общих 5m-баров для корреляции с открытой позицией {ticker}"
            )
        xs = [current_returns[ts] for ts in common]
        ys = [other[ts] for ts in common]
        corr = pearson_correlation(xs, ys)
        if corr is None:
            raise InsufficientFeedDataError(
                f"корреляция с открытой позицией {ticker} не определена (нулевая дисперсия)"
            )
        correlations.append(abs(corr))
    return max(correlations)


def _closed(candles: Sequence[Candle], *, now: datetime, duration: timedelta) -> list[Candle]:
    """Хронологически упорядоченные закрытые бары строго до `now` (без будущего).

    Отказ: `ValueError` при наивном `ts_open`, при дублирующемся `ts_open` среди
    закрытых баров, или при невалидном OHLCV уже закрытой свечи (цены/объём
    должны быть положительными конечными, high/low должны охватывать open/close) —
    это признак битой истории шлюза, который нельзя молча сортировать/схлопывать/
    пропускать в breadth/снимок счёта/индикаторы. Не проверяет отсутствие внутренних
    дыр в сетке баров (известное остаточное ограничение: корреляция/средний объём
    требуют строгую смежность отдельно — см. `_returns_by_ts`).
    """
    for candle in candles:
        if candle.ts_open.tzinfo is None or candle.ts_open.utcoffset() is None:
            raise ValueError("свеча должна иметь tz-aware ts_open")
    closed = [c for c in candles if c.is_complete is True and c.ts_open + duration <= now]
    closed.sort(key=lambda c: c.ts_open)
    for i in range(1, len(closed)):
        if closed[i].ts_open == closed[i - 1].ts_open:
            raise ValueError(f"дублирующийся ts_open в истории свечей: {closed[i].ts_open}")
    for candle in closed:
        prices = (candle.open, candle.high, candle.low, candle.close)
        if (
            any(not p.is_finite() or p <= 0 for p in prices)
            or candle.volume < 0
            or candle.high < max(candle.open, candle.close, candle.low)
            or candle.low > min(candle.open, candle.close, candle.high)
        ):
            raise ValueError(f"невалидный OHLCV у закрытой свечи ts_open={candle.ts_open}")
    return closed


def _regular_session_expected(now: datetime, known_holidays: frozenset[date]) -> bool:
    """Календарная (без RPC) проверка «сейчас обычная торговая сессия MOEX».

    Не заменяет `utils.can_trade_now` (нет проверки guard-окна и фактического
    `get_trading_status`) — используется только чтобы отличить легитимное отсутствие
    5m-истории вне сессии (валидно `None`) от «история должна быть, но её нет/
    она протухла» (`SourceRefusal`, docs/03_STRATEGY.md «фильтр 1»).
    """
    return session_calendar_decision(now=now, known_holidays=known_holidays).allowed


def _make_refusal(
    ticker: str,
    now: datetime,
    source_bar_ts: datetime | None,
    *,
    stage: str,
    error_type: str,
    action: Literal["skip", "stop"],
) -> SourceRefusal:
    """Строит безопасный `SourceRefusal`: только имя класса исключения, без сырого текста."""
    return SourceRefusal(
        ticker=ticker,
        stage=stage,
        observed_at=now.astimezone(UTC),
        source_bar_ts=source_bar_ts,
        action=action,
        error_type=error_type,
    )


@dataclass(frozen=True)
class _ClosedBarFreshness:
    timeframe: Timeframe
    now: datetime
    latest_open: datetime | None
    expected_open: datetime | None
    reason: str

    @property
    def fresh(self) -> bool:
        """Whether the latest closed bar is exactly the expected session bucket."""
        return self.reason == "latest_expected_closed_bar"


def _higher_timeframe_freshness(
    candles: Sequence[Candle],
    *,
    timeframe: Timeframe,
    now: datetime,
    known_holidays: frozenset[date],
) -> _ClosedBarFreshness:
    """Require the last fully closed bucket on today's confirmed regular-session grid.

    No delivery allowance can authorize a missing bucket. Before the first full
    bucket of this session there is no expected bar, not an overnight permission.
    The closing auction never creates another regular-session bucket.
    """
    local = now.astimezone(MSK)
    opening = datetime.combine(local.date(), SESSION_TRADING_START, MSK)
    closing = datetime.combine(local.date(), SESSION_CLOSING_AUCTION_START, MSK)
    latest = candles[-1].ts_open if candles else None
    expected = None
    if not session_calendar_decision(now=opening, known_holidays=known_holidays).allowed:
        reason = "calendar_blocked"
    else:
        duration = _TIMEFRAME_DURATION[timeframe]
        full_bars = (min(local, closing) - opening) // duration
        if full_bars < 1:
            reason = "no_expected_closed_bar"
        else:
            expected = (opening + (full_bars - 1) * duration).astimezone(UTC)
            if latest is None:
                reason = "empty_history"
            elif latest == expected:
                reason = "latest_expected_closed_bar"
            else:
                reason = "not_latest_expected_closed_bar"
    return _ClosedBarFreshness(timeframe, now, latest, expected, reason)


def _log_higher_timeframe_freshness(
    freshness: _ClosedBarFreshness,
    *,
    ticker: str,
    instrument_id: str,
    source_bar_ts: datetime,
    action: str,
) -> None:
    """Emit safe, structured evidence of the exact bucket policy, without SDK payloads."""
    duration = _TIMEFRAME_DURATION[freshness.timeframe]
    latest = freshness.latest_open
    expected = freshness.expected_open
    fields = {
        "ticker": ticker,
        "instrument_id": instrument_id,
        "timeframe": freshness.timeframe,
        "now": freshness.now.isoformat(),
        "source_5m_bar": source_bar_ts.isoformat(),
        "latest_open": latest.isoformat() if latest is not None else None,
        "latest_close": (latest + duration).isoformat() if latest is not None else None,
        "expected_open": expected.isoformat() if expected is not None else None,
        "expected_close": (expected + duration).isoformat() if expected is not None else None,
        "lag_seconds": (
            (expected - latest).total_seconds()
            if expected is not None and latest is not None
            else None
        ),
        "policy": "latest_fully_closed_regular_session_bucket",
        "reason": freshness.reason,
        "action": action,
    }
    logger.log(
        logging.INFO if freshness.fresh else logging.WARNING,
        "event=higher_timeframe_freshness " + " ".join(f"{key}=%s" for key in fields),
        *fields.values(),
        extra=fields,
    )


@dataclass(frozen=True)
class _TickerSnapshot:
    share: ShareMetadata
    candles_5m: list[Candle]
    candles_15m: list[Candle]
    candles_1h: list[Candle]


class PaperTickFeed:
    """Собирает реальный `executor.PaperTick` из шлюза/снимка счёта/классификатора.

    См. докстринг модуля — особенно контракт `next_paper_tick()`/`ack()` и
    разницу между «новый бар найден, но отказ» (исключение) и «неполный
    контекст пула/индекса» (журналируемая пауза через `MarketFilterResult`).
    """

    def __init__(
        self,
        *,
        gateway: InvestGateway,
        account_source: PaperAccountSource,
        regime_classifier: MarketRegimeClassifier,
        strategy_config: StrategyConfig,
        approved_tickers: Sequence[str],
        index_instrument_id: str,
        clock: Callable[[], datetime],
        max_bar_staleness: timedelta,
        max_quote_staleness: timedelta,
        known_holidays: frozenset[date] = frozenset(),
        monitoring_tickers: Sequence[str] = (),
        regime_refresh_min: int = DEFAULT_REGIME_REFRESH_MIN,
        correlation_window: int = 60,
        volume_avg_window: int = 20,
        index_timeframe: Timeframe = "15m",
        lookback_5m: timedelta | None = None,
        lookback_15m: timedelta | None = None,
        lookback_1h: timedelta | None = None,
        index_lookback: timedelta | None = None,
        liquidity_workers: int = 4,
    ) -> None:
        """Явные зависимости (ничего не читается напрямую из конфига/БД/SDK здесь).

        `approved_tickers` — selected bootstrap-пул для входов и breadth;
        `monitoring_tickers` — открытые позиции вне пула: получают полноценный
        контекст/статус/тики, но никогда разрешение новых входов или место в breadth.

        `lookback_*`/`index_lookback` опциональны: по умолчанию считаются из
        констант сессии MOEX и периодов `strategy_config`/
        `regime_classifier.config` (см. `required_bars_*`). Явно переданное
        значение меньше вычисленного минимума — `ValueError` при создании.

        `max_bar_staleness`/`max_quote_staleness` обязательны и без дефолта —
        не угаданные в коде "безопасные" числа, а явное решение вызывающего
        кода/оператора: на сколько последний закрытый 5m-бар/стакан может
        отставать от wall clock, оставаясь «свежим». Для 15m/1h и индекса
        требуется именно последний ожидаемый полностью закрытый бар сетки
        текущей обычной сессии; пустая история тикера дойдёт до журналируемого
        отказа стратегии `insufficient_history`. Конкретная задержка доставки
        истории/стакана у брокера не подтверждена API и не задана здесь дефолтом.
        """
        if max_bar_staleness <= timedelta(0):
            raise ValueError("max_bar_staleness должен быть положительным")
        if max_quote_staleness <= timedelta(0):
            raise ValueError("max_quote_staleness должен быть положительным")
        if not approved_tickers:
            raise ValueError("approved_tickers не может быть пустым")
        if len(set(approved_tickers)) != len(approved_tickers):
            raise ValueError("approved_tickers не должен содержать дубликаты")
        if not index_instrument_id:
            raise ValueError("index_instrument_id не может быть пустым")
        if correlation_window < 2:
            raise ValueError("correlation_window должен быть >= 2")
        if volume_avg_window < 1:
            raise ValueError("volume_avg_window должен быть положительным")
        if index_timeframe not in _TIMEFRAME_DURATION:
            raise ValueError(f"неподдерживаемый index_timeframe: {index_timeframe!r}")

        if not 1 <= liquidity_workers <= 32:
            raise ValueError("liquidity_workers must be between 1 and 32")
        self._liquidity_workers = liquidity_workers
        self._gateway = gateway
        self._account_source = account_source
        self._regime_classifier = regime_classifier
        self._strategy_config = strategy_config
        self._pool_tickers = tuple(approved_tickers)
        # Outside-pool positions still receive source/status/account/journal monitoring.
        # They never join breadth or receive permission to open another position.
        self._tickers = tuple(dict.fromkeys((*approved_tickers, *monitoring_tickers)))
        self._index_instrument_id = index_instrument_id
        self._clock = clock
        self._max_bar_staleness = max_bar_staleness
        self._max_quote_staleness = max_quote_staleness
        self._known_holidays = known_holidays
        self._correlation_window = correlation_window
        self._volume_avg_window = volume_avg_window
        self._index_timeframe: Timeframe = index_timeframe

        self._lookback_5m = self._resolve_lookback(
            lookback_5m,
            minimum=_default_lookback_5m(
                strategy_config,
                correlation_window=correlation_window,
                volume_avg_window=volume_avg_window,
            ),
            label="lookback_5m",
        )
        self._lookback_15m = self._resolve_lookback(
            lookback_15m, minimum=_default_lookback_15m(strategy_config), label="lookback_15m"
        )
        self._lookback_1h = self._resolve_lookback(
            lookback_1h, minimum=_default_lookback_1h(strategy_config), label="lookback_1h"
        )
        self._index_lookback = self._resolve_lookback(
            index_lookback,
            minimum=_default_index_lookback(regime_classifier, timeframe=index_timeframe),
            label="index_lookback",
        )

        self._pinned_metadata: dict[str, ShareMetadata] = {}
        self._pool_gate: PoolGate | None = None
        self._universe: Universe | None = None
        self._cursor = 0
        self._last_bar: dict[str, datetime] = {}
        self._bucket: datetime | None = None
        self._ticker_cache: dict[str, _TickerSnapshot] = {}
        self._local_regimes = LocalRegimeSchedule(
            strategy_config, refresh_minutes=regime_refresh_min
        )
        self._regime_poll_bucket: datetime | None = None
        self._regime_poll_slot: datetime | None = None

    def set_pool_gate(self, gate: PoolGate) -> None:
        """Install the permanent slot-aware entry gate before runtime polling."""
        self._pool_gate = gate

    def apply_universe(self, universe: Universe) -> None:
        """Atomically replace active identities/breadth, retaining every acknowledged bar."""
        self._pool_tickers = universe.selected
        self._tickers = tuple(dict.fromkeys((*universe.selected, *universe.monitoring)))
        self._pinned_metadata = dict(universe.metadata)
        self._index_instrument_id = universe.index_id
        self._universe = universe
        self._ticker_cache.clear()
        self._cursor = 0
        self._regime_poll_bucket = None
        self._regime_poll_slot = None

    def _share(self, ticker: str) -> ShareMetadata:
        if self._universe is not None:
            return self._pinned_metadata[ticker]
        return self._gateway.get_share_metadata(ticker=ticker)

    @staticmethod
    def _resolve_lookback(value: timedelta | None, *, minimum: timedelta, label: str) -> timedelta:
        if value is None:
            return minimum
        if value < minimum:
            raise ValueError(
                f"{label}={value} меньше рассчитанного минимума {minimum} для заданных периодов"
            )
        return value

    def local_regime(self, ticker: str, now: datetime) -> ScheduledRegime:
        """Read the scheduled local regime; absent heartbeat context fails closed."""
        return self._local_regimes.current(ticker, now)

    def drain_regime_events(self) -> tuple[RegimeChanged, ...]:
        """Transfer typed changes to an optional transport; delivery is not implied."""
        return self._local_regimes.drain_events()

    def _regime_observation(self, ticker: str, now: datetime) -> RegimeObservation:
        """Read 15m history without the 5m cache, quotes, status or account access."""
        share = self._share(ticker)
        candles = self._closed_history(share.instrument_id, "15m", now, self._lookback_15m)
        return self._regime_context(candles, now)

    def _regime_context(self, candles: list[Candle], now: datetime) -> RegimeObservation:
        """Validate current candidate history as well as heartbeat history without classifying."""
        if not candles:
            raise InsufficientFeedDataError("no closed local regime history")
        local = now.astimezone(MSK)
        opening = datetime.combine(local.date(), SESSION_TRADING_START, MSK)
        duration = _TIMEFRAME_DURATION["15m"]
        if local < opening + duration:
            previous_day = local.date() - timedelta(days=1)
            while previous_day.weekday() >= 5 or previous_day in self._known_holidays:
                previous_day -= timedelta(days=1)
            previous_open = datetime.combine(previous_day, SESSION_TRADING_START, MSK)
            if not session_calendar_decision(
                now=previous_open, known_holidays=self._known_holidays
            ).allowed:
                raise InsufficientFeedDataError("previous session is outside calendar coverage")
            previous_close = datetime.combine(previous_day, SESSION_CLOSING_AUCTION_START, MSK)
            full_bars = (previous_close - previous_open) // duration
            expected = previous_open + (full_bars - 1) * duration
            valid_until = opening + duration
        else:
            expected = opening + (((local - opening) // duration) - 1) * duration
            valid_until = candles[-1].ts_open + 2 * duration + self._max_bar_staleness
        if candles[-1].ts_open != expected or valid_until <= now:
            raise StaleDataError("local regime history is not the latest closed 15m bar")
        return RegimeObservation(candles, valid_until.astimezone(UTC))

    def poll_regimes(self) -> None:
        """Heartbeat before `next_paper_tick`, including 09:50 without any new 5m bar.

        At most one 15m history read per ticker per 5m poll bucket. Actual candidate
        history reads also revalidate context and revoke permission on source failure.
        Classification itself runs only on a new configured slot.
        Failures log and invalidate local context; they never close positions or alter risk.
        """
        self._gateway.check_cancelled()
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock() должен возвращать tz-aware время")
        bucket = self._floor_5m(now)
        active_tickers = self._active_tickers(now)
        if not active_tickers:
            return
        slot = self._local_regimes.current(active_tickers[0], now).slot
        if bucket == self._regime_poll_bucket and slot == self._regime_poll_slot:
            return
        self._regime_poll_bucket = bucket
        self._regime_poll_slot = slot
        local = now.astimezone(MSK)
        day_check = session_calendar_decision(
            now=datetime.combine(local.date(), SESSION_TRADING_START, MSK),
            known_holidays=self._known_holidays,
        )
        active = day_check.allowed and (
            SESSION_ACCEPT_ORDERS_START <= local.time() < SESSION_CLOSING_AUCTION_START
        )
        for ticker in active_tickers:
            self._gateway.check_cancelled()
            observation = None
            if active:
                try:
                    observation = self._regime_observation(ticker, now)
                except (GatewayError, ValueError, PaperFeedError, ArithmeticError) as exc:
                    logger.warning(
                        "event=local_regime_source_unavailable ticker=%s ts=%s "
                        "error_type=%s action=pause transport=unavailable",
                        ticker,
                        now.isoformat(),
                        type(exc).__name__,
                    )
            self._local_regimes.update(ticker, now, observation)

    # -- такты -----------------------------------------------------------------

    def _active_tickers(self, now: datetime) -> tuple[str, ...]:
        if self._pool_gate is not None and not self._pool_gate.check(now).allowed:
            return tuple(sorted(self._account_source.open_tickers()))
        return self._tickers

    def next_paper_tick(self) -> PaperTick | SourceRefusal | None:
        """Один закрытый 5m-тик по одному тикеру пула, `SourceRefusal` по нем, либо `None`.

        После успешного `PaperCycle.run_tick(tick)` с полученным `PaperTick` вызывающий
        код обязан вызвать `self.ack(tick)` — иначе тот же бар будет предложен
        снова (см. докстринг модуля). Каждый вызов обрабатывает ровно одного
        кандидата (round-robin по `self._cursor`, который в любом случае сдвигается на
        следующий тикер): честный отказ по этому тикеру возвращается сразу как
        `SourceRefusal`, а не проглатывается молча в попытке выбрать другой тикер в
        этом же вызове; следующий вызов обслужит остальных. Валидное «ничего
        нового у этого тикера» (уже подтверждённый бар, легальный офф-сессионный
        пробел) не считается отказом: цикл продолжает перебирать следующих тикеров
        в этом же вызове, возвращая `None`, если весь пул исчерпан без нового бара.
        """
        self._gateway.check_cancelled()
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock() должен возвращать tz-aware время")
        calendar = session_calendar_decision(now=now, known_holidays=self._known_holidays)
        if not calendar.allowed:
            logger.warning(
                "event=paper_feed_calendar_closed ts=%s reason=%s action=skip",
                now.isoformat(),
                calendar.reason,
            )
            return None
        self._refresh_bucket(now)

        active_tickers = self._active_tickers(now)
        for _ in range(len(active_tickers)):
            self._gateway.check_cancelled()
            self._cursor %= len(active_tickers)
            ticker = active_tickers[self._cursor]
            self._cursor = (self._cursor + 1) % len(active_tickers)

            try:
                snap = self._ensure_ticker(ticker, now)
            except GatewayError as exc:
                self._local_regimes.update(ticker, now, None)
                logger.error(
                    "event=paper_feed_ticker_unavailable ticker=%s ts=%s stage=ticker_snapshot "
                    "error_type=%s action=skip",
                    ticker,
                    now.isoformat(),
                    type(exc).__name__,
                )
                return _make_refusal(
                    ticker,
                    now,
                    None,
                    stage="ticker_snapshot",
                    error_type=type(exc).__name__,
                    action="skip",
                )
            except ValueError as exc:
                self._local_regimes.update(ticker, now, None)
                # Битая история (дублирующийся ts_open/невалидный OHLCV) из `_closed` —
                # признак сломанной истории шлюза для ЭТОГО кандидата, не краш.
                logger.error(
                    "event=paper_feed_ticker_history_invalid ticker=%s ts=%s "
                    "stage=ticker_history_invalid error_type=%s action=skip",
                    ticker,
                    now.isoformat(),
                    type(exc).__name__,
                )
                return _make_refusal(
                    ticker,
                    now,
                    None,
                    stage="ticker_history_invalid",
                    error_type=type(exc).__name__,
                    action="skip",
                )

            self._update_local_regime(ticker, now, snap.candles_15m)
            regular_session = _regular_session_expected(now, self._known_holidays)

            if not snap.candles_5m:
                if regular_session:
                    logger.error(
                        "event=paper_feed_bar_empty ticker=%s ts=%s stage=bar_empty action=skip",
                        ticker,
                        now.isoformat(),
                    )
                    return _make_refusal(
                        ticker,
                        now,
                        None,
                        stage="bar_empty",
                        error_type=EmptyBarHistoryError.__name__,
                        action="skip",
                    )
                continue  # легальное отсутствие 5m-истории вне обычной сессии

            latest_5m = snap.candles_5m[-1]
            if now - latest_5m.ts_open > self._max_bar_staleness:
                if regular_session:
                    logger.error(
                        "event=paper_feed_bar_stale ticker=%s ts=%s bar_ts=%s stage=bar_stale "
                        "action=skip",
                        ticker,
                        now.isoformat(),
                        latest_5m.ts_open.isoformat(),
                    )
                    return _make_refusal(
                        ticker,
                        now,
                        latest_5m.ts_open,
                        stage="bar_stale",
                        error_type=StaleDataError.__name__,
                        action="skip",
                    )
                logger.warning(
                    "event=paper_feed_bar_stale_off_session ticker=%s ts=%s bar_ts=%s "
                    "action=continue",
                    ticker,
                    now.isoformat(),
                    latest_5m.ts_open.isoformat(),
                )
                continue
            if self._last_bar.get(ticker) == latest_5m.ts_open:
                continue  # этот бар уже подтверждён ack() — не дублируем

            # С этой точки бар новый, свежий и найден: отказ дальше — не тихий пропуск.
            result = self._build_tick(ticker, now, snap)
            if (
                self._pool_gate is not None
                and not self._pool_gate.check(self._clock()).allowed
                and ticker not in self._account_source.open_tickers()
            ):
                return None
            if isinstance(result, SourceRefusal):
                logger.error(
                    "event=paper_feed_new_bar_failed ticker=%s ts=%s bar_ts=%s stage=%s "
                    "error_type=%s action=stop",
                    ticker,
                    now.isoformat(),
                    latest_5m.ts_open.isoformat(),
                    result.stage,
                    result.error_type,
                )
            return result
        return None

    def ack(self, tick: PaperTick) -> None:
        """Подтверждает, что `tick` успешно прошёл `PaperCycle.run_tick()`.

        Обязателен после каждого успешного такта — без него тот же закрытый
        бар снова будет предложен на следующем опросе того же тикера.
        """
        if not tick.strategy.candles_5m:
            raise ValueError("tick без свечей не может быть подтверждён")
        self._last_bar[tick.strategy.ticker] = tick.strategy.candles_5m[-1].ts_open

    def _refresh_bucket(self, now: datetime) -> None:
        """Чистит только кэш свечей/метаданных тикеров на новом 5m-бакете.

        Снимок счёта намеренно не кэшируется ни здесь, ни где-либо ещё —
        он пересобирается на каждый вызов `_account_state()`, чтобы исполнение
        предыдущего тикера в том же бакете (paper fill) было видно следующему.
        """
        bucket = self._floor_5m(now)
        if bucket == self._bucket:
            return
        self._bucket = bucket
        self._ticker_cache.clear()

    @staticmethod
    def _floor_5m(now: datetime) -> datetime:
        now_utc = now.astimezone(UTC)
        minute = (now_utc.minute // 5) * 5
        return now_utc.replace(minute=minute, second=0, microsecond=0)

    # -- данные по тикеру --------------------------------------------------------

    def _ensure_ticker(self, ticker: str, now: datetime) -> _TickerSnapshot:
        cached = self._ticker_cache.get(ticker)
        if cached is not None:
            return cached
        share = self._share(ticker)
        snapshot = _TickerSnapshot(
            share=share,
            candles_5m=self._closed_history(share.instrument_id, "5m", now, self._lookback_5m),
            candles_15m=self._closed_history(share.instrument_id, "15m", now, self._lookback_15m),
            candles_1h=self._closed_history(share.instrument_id, "1h", now, self._lookback_1h),
        )
        self._ticker_cache[ticker] = snapshot
        return snapshot

    def _closed_history(
        self, instrument_id: str, timeframe: Timeframe, now: datetime, lookback: timedelta
    ) -> list[Candle]:
        raw = self._gateway.get_candles_history(
            instrument_id=instrument_id, timeframe=timeframe, from_=now - lookback, to=now
        )
        return _closed(raw, now=now, duration=_TIMEFRAME_DURATION[timeframe])

    def _update_local_regime(self, ticker: str, now: datetime, candles: list[Candle]) -> None:
        """Revalidate each candidate's actual history without retaining missing permission."""
        try:
            observation = self._regime_context(candles, now)
        except (ValueError, PaperFeedError) as exc:
            logger.warning(
                "event=local_regime_context_unavailable ticker=%s ts=%s "
                "error_type=%s action=pause",
                ticker,
                now.isoformat(),
                type(exc).__name__,
            )
            observation = None
        self._local_regimes.update(ticker, now, observation)

    def _build_tick(
        self, ticker: str, now: datetime, snap: _TickerSnapshot
    ) -> PaperTick | SourceRefusal:
        """Строит `PaperTick` по уже подтверждённому новому 5m-бару или `SourceRefusal`.

        Вызывается только после того, как `next_paper_tick()` уже подтвердил свежий
        новый бар — любой отказ здесь идёт с `action="stop"` и с `source_bar_ts` этого
        уже известного бара (он найден, но дальше честно построить тик не удалось).
        """
        bar_ts = snap.candles_5m[-1].ts_open
        decision = can_trade_now(
            self._gateway,
            instrument_id=snap.share.instrument_id,
            now=now,
            session_guard_min=self._strategy_config.session_guard_min,
            known_holidays=self._known_holidays,
        )
        trading_status = decision.trading_status or "STATUS_UNKNOWN"
        histories: tuple[tuple[Timeframe, list[Candle]], ...] = (
            ("15m", snap.candles_15m),
            ("1h", snap.candles_1h),
        )
        for timeframe, candles in histories:
            if candles:
                freshness = _higher_timeframe_freshness(
                    candles,
                    timeframe=timeframe,
                    now=now,
                    known_holidays=self._known_holidays,
                )
                _log_higher_timeframe_freshness(
                    freshness,
                    ticker=ticker,
                    instrument_id=snap.share.instrument_id,
                    source_bar_ts=bar_ts,
                    action="continue" if freshness.fresh else "stop",
                )
                if not freshness.fresh:
                    return _make_refusal(
                        ticker,
                        now,
                        bar_ts,
                        stage="higher_timeframe_stale",
                        error_type=StaleDataError.__name__,
                        action="stop",
                    )
        self._gateway.check_cancelled()
        try:
            request_started_at = self._clock()
            quote = self._gateway.get_order_book_quote(instrument_id=snap.share.instrument_id)
            response_received_at = self._clock()
        except GatewayError as exc:
            self._gateway.check_cancelled()
            return _make_refusal(
                ticker, now, bar_ts, stage="quote", error_type=type(exc).__name__, action="stop"
            )
        self._gateway.check_cancelled()
        timestamps = {
            "tick_started_at": now,
            "request_started_at": request_started_at,
            "response_received_at": response_received_at,
            "quote_observed_at": quote.observed_at,
        }
        safe_timestamps: dict[str, str] = {}
        valid_timestamps = True
        for name, timestamp in timestamps.items():
            try:
                safe_timestamps[name] = timestamp.isoformat()
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    valid_timestamps = False
            except (TypeError, ValueError, OverflowError):
                safe_timestamps[name] = "invalid"
                valid_timestamps = False
        quote_age: timedelta | None = None
        reason = "fresh"
        if not valid_timestamps:
            reason = "invalidtimestamp"
        elif request_started_at < now or response_received_at < request_started_at:
            reason = "clock_rollback"
        else:
            quote_age = response_received_at - quote.observed_at
            if quote_age < timedelta(0):
                reason = "future"
            elif quote_age > self._max_quote_staleness:
                reason = "stale"
            # Keep the coherent tick-start snapshot, but never carry its old
            # permission across expiry during the quote RPC. No extra SDK reads.
            elif (
                decision.allowed
                and not session_calendar_decision(
                    now=response_received_at, known_holidays=self._known_holidays
                ).allowed
            ):
                reason = "session_closed"
            elif decision.allowed and session_guard_active(
                now=response_received_at, guard_min=self._strategy_config.session_guard_min
            ):
                reason = "session_guard"
            elif response_received_at - bar_ts > self._max_bar_staleness:
                reason = "source_bar_stale"
            elif any(
                candles
                and not _higher_timeframe_freshness(
                    candles,
                    timeframe=timeframe,
                    now=response_received_at,
                    known_holidays=self._known_holidays,
                ).fresh
                for timeframe, candles in histories
            ):
                reason = "higher_timeframe_stale"
        action = "continue" if reason == "fresh" else "stop"
        fields = {
            **safe_timestamps,
            "ticker": ticker,
            "instrument_id": snap.share.instrument_id,
            "source_bar_ts": bar_ts.isoformat(),
            "age_sec": quote_age.total_seconds() if quote_age is not None else None,
            "max_age_sec": self._max_quote_staleness.total_seconds(),
            "reason": reason,
            "action": action,
        }
        logger.log(
            logging.INFO if reason == "fresh" else logging.WARNING,
            "event=quote_freshness "
            + " ".join(f"{name}={value}" for name, value in fields.items()),
            extra=fields,
        )
        if reason != "fresh":
            return _make_refusal(
                ticker,
                now,
                bar_ts,
                stage="quote",
                error_type=StaleDataError.__name__,
                action="stop",
            )
        try:
            spread_bps = spread_bps_from_quote(quote)
        except ValueError as exc:
            return _make_refusal(
                ticker, now, bar_ts, stage="quote", error_type=type(exc).__name__, action="stop"
            )

        try:
            account = self._account_state(now, bar_ts=bar_ts, candidate_ticker=ticker)
        except (
            GatewayError,
            PaperAccountError,
            PaperFeedError,
            InsufficientFeedDataError,
            ValueError,
        ) as exc:
            return _make_refusal(
                ticker,
                now,
                bar_ts,
                stage="account_snapshot",
                error_type=type(exc).__name__,
                action="stop",
            )

        market_filter = self._market_filter(now=now, bar_ts=bar_ts)
        if ticker not in self._pool_tickers:
            market_filter = replace(
                market_filter,
                allow_long=False,
                allow_short=False,
                paused=True,
                reason="monitoring_only: outside selected pool; " + market_filter.reason,
            )

        current_returns = _returns_by_ts(snap.candles_5m, window=self._correlation_window)
        open_returns: dict[str, Mapping[datetime, Decimal]] = {}
        try:
            for position in account.open_positions:
                if position.ticker == ticker:
                    continue
                other_snap = self._ensure_ticker(position.ticker, now)
                open_returns[position.ticker] = _returns_by_ts(
                    other_snap.candles_5m, window=self._correlation_window
                )
            max_correlation = max_abs_correlation_with_open(
                current_returns=current_returns, open_returns=open_returns
            )
        except (GatewayError, ValueError, InsufficientFeedDataError, ArithmeticError) as exc:
            return _make_refusal(
                ticker,
                now,
                bar_ts,
                stage="correlation",
                error_type=type(exc).__name__,
                action="stop",
            )

        average_volume_lots = average_volume_prior(
            [c.volume for c in snap.candles_5m], self._volume_avg_window
        )
        gap_result = compute_gap(snap.candles_5m, now=now, config=self._strategy_config)

        if self._pool_gate is not None:
            permission = self._pool_gate.check(self._clock())
            audit = (
                f"pool_epoch generation={permission.generation} "
                f"slot={permission.slot.isoformat()} gate={permission.reason}; "
            )
            market_filter = replace(market_filter, reason=audit + market_filter.reason)
            if not permission.allowed:
                market_filter = replace(
                    market_filter, allow_long=False, allow_short=False, paused=True
                )

        strategy_inputs = StrategyInputs(
            ticker=ticker,
            candles_5m=snap.candles_5m,
            candles_15m=snap.candles_15m,
            candles_1h=snap.candles_1h,
            config=self._strategy_config,
            now=now,
            is_session_open=decision.allowed or decision.guard_active,
            session_reason=decision.reason,
            session_refusal_filter=decision.refusal_filter,
            short_enabled=snap.share.short_enabled,
            session_guard_active=decision.guard_active,
            gap_result=gap_result,
            market_filter=market_filter,
            local_regime=self.local_regime(ticker, now),
        )
        market_context = PaperMarketContext(
            sector=snap.share.sector,
            lot_size=snap.share.lot_size,
            min_price_increment=snap.share.min_price_increment,
            average_volume_lots=average_volume_lots,
            currency=snap.share.currency,
            spread_bps=spread_bps,
            trading_status=trading_status,
            max_correlation_with_open=max_correlation,
        )
        return PaperTick(strategy=strategy_inputs, account=account, market=market_context)

    # -- снимок счёта: пересчитывается на каждый вызов, без кэша по бакету -------

    def _account_state(
        self, now: datetime, *, bar_ts: datetime, candidate_ticker: str
    ) -> AccountState:
        """Собирает `AccountState` заново из `account_source.open_tickers()`.

        Никакого кэша: исполнение по предыдущему тикеру в том же 5m-бакете
        должно быть сразу видно следующему. Цена каждой открытой позиции
        должна относиться к тому же закрытому бару, что и решение кандидата.
        """
        trading_day = bar_ts.astimezone(MSK).date()
        candidate = self._ensure_ticker(candidate_ticker, now)
        confirmed = (
            trading_day == now.astimezone(MSK).date()
            and _regular_session_expected(now, self._known_holidays)
            and _session_open_bar(candidate.candles_5m, trading_day) is not None
        )
        open_tickers = self._account_source.open_tickers()
        prices: dict[str, Decimal] = {}
        opening_prices: dict[str, Decimal] = {}
        sectors: dict[str, str] = {}
        for ticker in open_tickers:
            snap = self._ensure_ticker(ticker, now)
            if not snap.candles_5m:
                raise PaperFeedError(f"нет закрытых 5m-свечей для открытой позиции {ticker}")
            if snap.candles_5m[-1].ts_open != bar_ts:
                raise InsufficientFeedDataError(
                    f"нет цены открытой позиции {ticker} на закрытом баре {bar_ts.isoformat()}"
                )
            prices[ticker] = snap.candles_5m[-1].close
            opening_bar = _session_open_bar(snap.candles_5m, trading_day)
            if opening_bar is not None:
                opening_prices[ticker] = opening_bar.open
            sectors[ticker] = snap.share.sector
        return self._account_source.snapshot(
            now,
            prices,
            opening_prices,
            sectors,
            trading_day=trading_day,
            trading_day_confirmed=confirmed,
        )

    # -- рыночный контекст пула/индекса ------------------------------------------

    @staticmethod
    def _pause_reason(
        breadth: Decimal | None, regime_bar: RegimeBar | None, atr_rising: bool | None
    ) -> str:
        if breadth is None:
            return "breadth пула неполон/недоступен — безопасная пауза"
        if regime_bar is None:
            return "режим индекса недоступен — безопасная пауза (market_regime=chaos)"
        if regime_bar.is_chaos:
            return "индекс в состоянии chaos (MarketRegimeClassifier) — пауза"
        return "направление ATR индекса не определено — безопасная пауза"

    def _market_filter(self, *, now: datetime, bar_ts: datetime) -> MarketFilterResult:
        """Строит `MarketFilterResult`, заполняя C_t/T_t/режим из `RegimeBar`.

        Неполный breadth (не по всем `approved_tickers`), недоступный индекс
        или неопределённое направление ATR индекса — не числовой факт и не
        усредняется по доступному: пауза с `market_regime="chaos"`, если
        индекс недоступен (docs/03_STRATEGY.md — хаос это безопасный дефолт).
        """
        pairs: dict[str, tuple[Decimal, Decimal]] = {}
        for ticker in self._pool_tickers:
            try:
                snap = self._ensure_ticker(ticker, now)
            except (GatewayError, ValueError) as exc:
                # ValueError тоже сюда (битая история этого тикера из `_closed`) —
                # это тикер пула для breadth, не текущий кандидат: неполный
                # breadth — легальное состояние (пауза), а не незаписанное падение до тика.
                logger.warning(
                    "event=pool_breadth_ticker_unavailable ticker=%s ts=%s error_type=%s",
                    ticker,
                    now.isoformat(),
                    type(exc).__name__,
                )
                continue
            if (
                len(snap.candles_5m) >= 2
                and snap.candles_5m[-1].ts_open == bar_ts
                and snap.candles_5m[-1].ts_open - snap.candles_5m[-2].ts_open
                == _TIMEFRAME_DURATION["5m"]
                and now - snap.candles_5m[-1].ts_open <= self._max_bar_staleness
            ):
                pairs[ticker] = (snap.candles_5m[-1].close, snap.candles_5m[-2].close)
        breadth = pool_breadth(pairs) if pairs and len(pairs) == len(self._pool_tickers) else None

        regime_bar: RegimeBar | None = None
        atr_rising: bool | None = None
        try:
            index_candles = self._closed_history(
                self._index_instrument_id, self._index_timeframe, now, self._index_lookback
            )
            freshness = _higher_timeframe_freshness(
                index_candles,
                timeframe=self._index_timeframe,
                now=now,
                known_holidays=self._known_holidays,
            )
            _log_higher_timeframe_freshness(
                freshness,
                ticker="IMOEX",
                instrument_id=self._index_instrument_id,
                source_bar_ts=bar_ts,
                action="classify" if freshness.fresh else "chaos",
            )
            if freshness.fresh:
                regime_bar = self._regime_classifier.evaluate(index_candles)
                atr_rising = index_atr_rising(index_candles, self._regime_classifier.config.period)
        except (GatewayError, ValueError) as exc:
            # ValueError тоже сюда — битая/недостаточная история индекса из `_closed`
            # (не ошибка кандидата): docs/03_STRATEGY.md §«хаос» (111-118) требует fail-closed
            # chaos при недостатке/битой истории индекса, а не `SourceRefusal`.
            logger.warning(
                "event=index_context_unavailable ts=%s error_type=%s",
                now.isoformat(),
                type(exc).__name__,
            )
            regime_bar = None
            atr_rising = None
            unavailable = _higher_timeframe_freshness(
                [],
                timeframe=self._index_timeframe,
                now=now,
                known_holidays=self._known_holidays,
            )
            _log_higher_timeframe_freshness(
                replace(unavailable, reason="index_context_unavailable"),
                ticker="IMOEX",
                instrument_id=self._index_instrument_id,
                source_bar_ts=bar_ts,
                action="chaos",
            )

        if breadth is None or regime_bar is None or regime_bar.is_chaos or atr_rising is None:
            return MarketFilterResult(
                allow_long=False,
                allow_short=False,
                paused=True,
                breadth=breadth,
                reason=self._pause_reason(breadth, regime_bar, atr_rising),
                chaos_score=regime_bar.chaos_score if regime_bar is not None else None,
                chaos_threshold=regime_bar.threshold if regime_bar is not None else None,
                market_regime=regime_bar.regime if regime_bar is not None else "chaos",
            )

        # Полный, определённый, не-хаотичный контекст: та же формула
        # breadth/ATR, что `strategy.evaluate_market_breadth`, без её
        # std-ветки (см. докстринг модуля — эта метрика здесь не измеряется).
        config = self._strategy_config
        allow_long = not (breadth < config.breadth_bearish and atr_rising)
        allow_short = not (breadth > config.breadth_bullish and atr_rising)
        unrestricted = allow_long and allow_short
        reason = (
            f"breadth={breadth}, атр_imoex_растёт={atr_rising}: "
            f"{'без ограничений' if unrestricted else 'ограничена сторона сигнала'}"
        )
        return MarketFilterResult(
            allow_long=allow_long,
            allow_short=allow_short,
            paused=False,
            breadth=breadth,
            reason=reason,
            chaos_score=regime_bar.chaos_score,
            chaos_threshold=regime_bar.threshold,
            market_regime=regime_bar.regime,
        )

    # -- сырые данные для pool.recommend_pool (не решает, что утверждено) --------

    def daily_volume_lots(
        self, candidates: Sequence[str], *, sessions: int
    ) -> dict[str, list[int]]:
        """Объём в лотах по последним завершённым `sessions` торговым сессиям.

        Только сырые проверенные данные для `pool.recommend_pool`.
        Bootstrap применяет рекомендацию отдельно; этот метод не меняет пул.
        Тикер без ровно `sessions` последних календарно подтверждённых
        завершённых сессий получает укороченный список —
        `pool.recommend_pool` сам отбраковывает его по длине. Полной считается
        только непрерывная сетка обычных 5m-баров от 10:00 до 23:35 МСК.
        """
        if sessions <= 0:
            raise ValueError("sessions должен быть положительным")
        self._gateway.check_cancelled()
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock() должен возвращать tz-aware время")
        lookback = _lookback_for_bars(sessions * int(_bars_per_session(5)), timeframe_minutes=5)
        day = (now - lookback).astimezone(MSK).date()
        expected_sessions: list[date] = []
        while day <= now.astimezone(MSK).date():
            opening = datetime.combine(day, SESSION_TRADING_START, tzinfo=MSK)
            closing = datetime.combine(day, SESSION_CLOSING_AUCTION_START, tzinfo=MSK)
            if (
                closing <= now
                and session_calendar_decision(
                    now=opening, known_holidays=self._known_holidays
                ).allowed
            ):
                expected_sessions.append(day)
            day += timedelta(days=1)
        expected_sessions = expected_sessions[-sessions:]
        if len(expected_sessions) != sessions:
            raise InsufficientFeedDataError("недостаточно подтверждённых календарём сессий")
        # Resolve metadata only on the owner thread. Worker closures contain just
        # the gateway and immutable request values, never account_source/SQLite/feed.
        requests: dict[str, Callable[[], list[Candle]]] = {}
        for ticker in candidates:
            self._gateway.check_cancelled()
            share = self._gateway.get_share_metadata(ticker=ticker)
            requests[ticker] = partial(
                self._gateway.get_candles_history,
                instrument_id=share.instrument_id,
                timeframe="5m",
                from_=now - lookback,
                to=now,
            )
        result: dict[str, list[int]] = {}
        workers = HistoryWorkers(self._gateway, self._liquidity_workers)
        try:
            for ticker, raw in workers.histories(requests):
                self._gateway.check_cancelled()
                try:
                    if isinstance(raw, ValueError):
                        raise raw
                    candles = _closed(raw, now=now, duration=_TIMEFRAME_DURATION["5m"])
                except ValueError as exc:
                    logger.warning(
                        "event=pool_candidate_unavailable ticker=%s ts=%s error_type=%s "
                        "action=exclude remedy=check_candidate_history_and_metadata",
                        ticker,
                        now.isoformat(),
                        type(exc).__name__,
                    )
                    result[ticker] = []
                    continue
                by_session: dict[date, list[Candle]] = {}
                for candle in candles:
                    msk_ts = candle.ts_open.astimezone(MSK)
                    if (
                        msk_ts.date() in expected_sessions
                        and SESSION_TRADING_START <= msk_ts.time() < SESSION_CLOSING_AUCTION_START
                    ):
                        by_session.setdefault(msk_ts.date(), []).append(candle)
                complete_volumes: dict[date, int] = {}
                for session, session_candles in by_session.items():
                    times = sorted(c.ts_open.astimezone(MSK) for c in session_candles)
                    first = datetime.combine(session, SESSION_TRADING_START, tzinfo=MSK)
                    expected_count = int(_bars_per_session(5))
                    if len(times) != expected_count or any(
                        ts != first + timedelta(minutes=5 * index) for index, ts in enumerate(times)
                    ):
                        continue
                    complete_volumes[session] = sum(c.volume for c in session_candles)
                result[ticker] = [
                    complete_volumes[session]
                    for session in expected_sessions
                    if session in complete_volumes
                ]
                logger.info(
                    "event=pool_liquidity_candidate ticker=%s completed=%s total=%s "
                    "action=complete",
                    ticker,
                    len(result),
                    len(candidates),
                )
        finally:
            workers.close()
        self._gateway.check_cancelled()
        return {ticker: result[ticker] for ticker in candidates}
