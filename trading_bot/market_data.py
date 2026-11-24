"""Кэш свечей, лимитер запросов, реконнект стрима (слой L1, docs/01_ARCHITECTURE.md).

Разрешено: обёртывать `api_client.InvestGateway` кэшем с TTL по таймфрейму,
token-bucket лимитером с приоритетами и реконнектом стрима с exponential backoff
(docs/08_TINVEST_API.md, docs/00_PLAN.md §8–§9). Запрещено: знать о стратегии/риске/
портфеле выше по стеку и импортировать `tinkoff.invest` напрямую — только через
`api_client.InvestGateway`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum

from trading_bot.api_client import Candle, GatewayError, InvestGateway, Timeframe

logger = logging.getLogger(__name__)

#: TTL свечи по таймфрейму (docs/00_PLAN.md §9: «кэш свечей с TTL по таймфрейму»).
#: Смысл: пока текущая свеча не закрылась, повторный запрос того же диапазона не
#: должен идти в сеть — она физически не может измениться до конца периода.
_TTL_BY_TIMEFRAME: dict[Timeframe, timedelta] = {
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
}


def _utcnow() -> datetime:
    """Единая точка получения текущего времени — tz-aware UTC (docs/02_CODING_RULES.md)."""
    return datetime.now(tz=UTC)


@dataclass
class _CacheEntry:
    candles: list[Candle]
    fetched_at: datetime
    ttl: timedelta

    def is_fresh(self, *, now: datetime) -> bool:
        return now - self.fetched_at < self.ttl


class CandleCache:
    """Кэш свечей в памяти, ключ — (instrument_id, timeframe, from_, to).

    Тест приёмки M3: повторный запрос 1h-свечей внутри часа не идёт в сеть.
    """

    def __init__(self, gateway: InvestGateway, *, clock: Callable[[], datetime] = _utcnow) -> None:
        self._gateway = gateway
        self._clock = clock
        self._entries: dict[tuple[str, str, datetime, datetime | None], _CacheEntry] = {}
        self._lock = threading.Lock()

    def get_candles(
        self,
        *,
        instrument_id: str,
        timeframe: Timeframe,
        from_: datetime,
        to: datetime | None = None,
    ) -> list[Candle]:
        """Возвращает свечи из кэша, если он свежий по TTL таймфрейма, иначе запрашивает.

        Возврат: список `Candle` (см. `api_client.Candle`).
        Отказ: как у `InvestGateway.get_candles` (`GatewayError`/`SdkUnavailableError`).
        """
        key = (instrument_id, timeframe, from_, to)
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry.is_fresh(now=now):
                return entry.candles

        candles = self._gateway.get_candles(
            instrument_id=instrument_id, timeframe=timeframe, from_=from_, to=to
        )
        ttl = _TTL_BY_TIMEFRAME[timeframe]
        with self._lock:
            self._entries[key] = _CacheEntry(candles=candles, fetched_at=now, ttl=ttl)
        return candles


class Priority(IntEnum):
    """Приоритеты запросов из docs/00_PLAN.md §9: позиции > заявки > свечи > пул.

    Меньшее значение — выше приоритет (обрабатывается первым/не режется).
    """

    POSITIONS = 0
    ORDERS = 1
    CANDLES = 2
    POOL = 3


#: Порог из docs/00_PLAN.md §9 / docs/08_TINVEST_API.md: на 80% исчерпания бюджета
#: несрочные (низкий приоритет) запросы режутся.
_SOFT_LIMIT_RATIO = 0.8

#: Приоритеты, которые считаются «несрочными» и режутся первыми на пороге.
_NON_URGENT_PRIORITIES = frozenset({Priority.CANDLES, Priority.POOL})


@dataclass
class RateLimiter:
    """Token bucket с приоритетами запросов и мягким порогом на несрочные запросы.

    `capacity` — размер бюджета за `refill_period`; `refill_period` — интервал
    полного восполнения токенов (упрощённая модель: восполняется целиком раз в
    период, а не непрерывно — точная rps-формула не подтверждена, docs/08 п.13
    VERIFY). Несрочные (`CANDLES`/`POOL`) запросы отклоняются, когда свободно
    менее `(1 - 0.8) * capacity` токенов — «на 80% исчерпания лимита».
    """

    capacity: int
    refill_period: timedelta
    clock: Callable[[], datetime] = _utcnow
    _tokens: int = field(init=False)
    _window_start: datetime = field(init=False)

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity должен быть положительным")
        self._tokens = self.capacity
        self._window_start = self.clock()

    def _refill_if_needed(self) -> None:
        now = self.clock()
        if now - self._window_start >= self.refill_period:
            self._tokens = self.capacity
            self._window_start = now

    def try_acquire(self, priority: Priority) -> bool:
        """Пытается занять один токен с учётом приоритета.

        Возврат: `True`, если запрос допущен; `False`, если лимит исчерпан
        (срочные) или превышен мягкий порог 80% (несрочные) — вызывающий код
        обязан интерпретировать `False` как «данных нет, пропускаем», не как
        исключение транспорта (docs/08_TINVEST_API.md, «Ограничения»).
        """
        self._refill_if_needed()
        if self._tokens <= 0:
            logger.warning("Лимит запросов исчерпан, приоритет=%s", priority.name)
            return False
        if priority in _NON_URGENT_PRIORITIES:
            used_ratio = 1 - (self._tokens / self.capacity)
            if used_ratio >= _SOFT_LIMIT_RATIO:
                logger.warning(
                    "80%% бюджета исчерпано, несрочный запрос отложен: приоритет=%s",
                    priority.name,
                )
                return False
        self._tokens -= 1
        return True


class StreamReconnector:
    """Обёртывает `InvestGateway.stream_candles` реконнектом с exponential backoff
    (docs/00_PLAN.md §8: «повтор каждые 5 секунд, exponential backoff, до 10 попыток»).

    После исчерпания `max_attempts` бросает `GatewayError` дальше. Синхронизация
    позиций после восстановления связи (docs/00_PLAN.md §10) — задача выше по стеку
    (executor/portfolio, реализуется в M8), этот класс только восстанавливает поток свечей.
    """

    def __init__(
        self,
        gateway: InvestGateway,
        *,
        backoff_base_sec: int,
        max_attempts: int,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if backoff_base_sec <= 0:
            raise ValueError("backoff_base_sec должен быть положительным")
        if max_attempts <= 0:
            raise ValueError("max_attempts должен быть положительным")
        self._gateway = gateway
        self._backoff_base_sec = backoff_base_sec
        self._max_attempts = max_attempts
        self._sleep = sleep

    def candles(self, *, instrument_ids: list[str], timeframe: Timeframe) -> Iterator[Candle]:
        """Свечи из стрима с автоматическим переподключением при обрыве связи.

        Возврат: генератор `Candle`, никогда не завершается сам по себе (только при
        явной остановке вызывающим).
        Отказ: `GatewayError`, если после `max_attempts` связь не восстановилась;
        `SdkUnavailableError`/`ValueError` пробрасываются без ретрая — это не ошибка сети.
        """
        attempt = 0
        while True:
            try:
                for candle in self._gateway.stream_candles(
                    instrument_ids=instrument_ids, timeframe=timeframe
                ):
                    if attempt:
                        logger.info("Стрим свечей восстановлен после %s попыток", attempt)
                        attempt = 0
                    yield candle
                return
            except GatewayError as exc:
                attempt += 1
                if attempt > self._max_attempts:
                    logger.error(
                        "Стрим свечей не восстановлен после %s попыток: %s",
                        self._max_attempts,
                        exc,
                    )
                    raise
                delay = self._backoff_base_sec * (2 ** (attempt - 1))
                logger.warning(
                    "Обрыв стрима свечей (попытка %s/%s), повтор через %sс: %s",
                    attempt,
                    self._max_attempts,
                    delay,
                    exc,
                )
                self._sleep(delay)
