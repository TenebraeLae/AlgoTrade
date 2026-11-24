"""Время и торговая сессия MOEX (слой L0, docs/01_ARCHITECTURE.md).

Разрешено: константы торговой сессии MOEX (docs/00_PLAN.md §4), парсинг фолбэк-календаря
праздников из `data_seed/holidays_ru.md`, единая проверка «можно ли торговать сейчас»
через `InvestGateway.get_trading_status` (первичный источник) с консервативным фолбэком.
Запрещено: сам решать, что торговать и как — это `strategy.py`/`risk_manager.py` выше
по стеку; хардкодить статус вместо запроса к API, если API доступен.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from trading_bot.api_client import NORMAL_TRADING_STATUS, GatewayError, InvestGateway

logger = logging.getLogger(__name__)

#: Отображение времени — МСК; хранение — UTC (docs/02_CODING_RULES.md).
MSK = ZoneInfo("Europe/Moscow")

#: Торговая сессия MOEX, подтверждена docs/00_PLAN.md §4 (документ проекта, не API):
#: приём заявок с 09:50, торги 10:00–23:50, аукцион открытия 09:50–10:00,
#: аукцион закрытия 23:40–23:50. Здесь — не магическое число в стратегии, а
#: именованная константа торгового календаря MOEX.
SESSION_ACCEPT_ORDERS_START = time(9, 50)
SESSION_TRADING_START = time(10, 0)
SESSION_CLOSING_AUCTION_START = time(23, 40)
SESSION_TRADING_END = time(23, 50)

#: Подтверждено только расписание 2026: https://www.moex.com/n96571 (30.12.2025).
#: Покрытие не выводится из дат в denylist; новый год требует проверки MOEX и seed.
_CALENDAR_SUPPORTED_YEARS = frozenset({2026})

#: Все строки данных (включая повреждённые даты) проверяются, а не молча пропускаются.
_HOLIDAY_ROW = re.compile(r"^\|\s*\d[^\n]*$", re.MULTILINE)
_BLOCKED_DAY_STATUSES = frozenset({"нерабочий день", "специальная сессия (запрет)"})


def load_known_holidays(path: Path) -> set[date]:
    """Загружает запреты дней MOEX, включая неподдержанные специальные сессии.

    Возврат: множество дат, запрещённых политикой проекта, не список всех закрытий
    биржи: статус «специальная сессия (запрет)» также блокирует день целиком.
    Покрытие задано `_CALENDAR_SUPPORTED_YEARS`, а не угадано по датам таблицы.
    Отсутствующий, пустой, нечитаемый или некорректный календарь вызывает `ValueError`
    с WARNING. Runtime обязан остановить запуск, не заменять ошибку пустым набором.
    """
    try:
        text = path.read_text(encoding="utf-8")
        holidays: set[date] = set()
        for match in _HOLIDAY_ROW.finditer(text):
            cells = [cell.strip() for cell in match.group().split("|")]
            if len(cells) < 6:
                raise ValueError("неполная строка таблицы")
            day = date.fromisoformat(cells[1])
            status = cells[2]
            if day.year not in _CALENDAR_SUPPORTED_YEARS:
                raise ValueError("год вне подтверждённого покрытия")
            if status not in _BLOCKED_DAY_STATUSES and status != "рабочий день":
                raise ValueError("неизвестный статус дня")
            if status in _BLOCKED_DAY_STATUSES:
                holidays.add(day)
        if not holidays:
            raise ValueError("нет подтверждённых запрещённых дат")
    except (OSError, ValueError) as exc:
        logger.warning(
            "event=trading_calendar_invalid path=%s error_type=%s action=stop",
            path,
            type(exc).__name__,
        )
        raise ValueError("Торговый календарь не прочитан или некорректен") from None
    return holidays


@dataclass(frozen=True)
class TradingDecision:
    """Результат проверки сессии и фактически прочитанный статус инструмента.

    `trading_status=None` означает, что API не опрашивался (календарный/guard
    отказ) либо не ответил; это не подтверждение нормальных торгов.
    """

    allowed: bool
    reason: str
    guard_active: bool = False
    trading_status: str | None = None
    refusal_filter: Literal["session_open", "instrument_status_normal"] = "session_open"


def session_guard_active(*, now: datetime, guard_min: int) -> bool:
    """Защитные первые/последние `guard_min` минут основной сессии (docs/03_STRATEGY.md,
    фильтр №2 «охранное окно»; docs/04_RISK.md, п.2: «вне защитных 10-минуток»).

    Границы отсчитываются от начала обычных торгов (`SESSION_TRADING_START`) и от
    начала закрывающего аукциона (`SESSION_CLOSING_AUCTION_START`). По решению
    оператора 2026-10-14 последние 10 минут — это 23:30–23:40, не сам аукцион.

    Возврат: `True`, если `now` (по MSK) лежит в первые или последние
    `guard_min` минут обычных торгов. Ноль отключает защиту; отрицательное значение,
    наивное время или перекрывающиеся окна, закрывающие всю сессию, вызывают `ValueError`.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now должен быть tz-aware")
    if guard_min < 0:
        raise ValueError("guard_min должен быть неотрицательным")
    if guard_min == 0:
        return False
    msk_now = now.astimezone(MSK)
    anchor_date = msk_now.date()
    regular_open = datetime.combine(anchor_date, SESSION_TRADING_START)
    regular_close = datetime.combine(anchor_date, SESSION_CLOSING_AUCTION_START)
    if guard_min * 2 >= (regular_close - regular_open) // timedelta(minutes=1):
        raise ValueError("guard_min windows overlap regular trading session")
    guard_end_of_open = (regular_open + timedelta(minutes=guard_min)).time()
    guard_start_of_close = (regular_close - timedelta(minutes=guard_min)).time()
    current_time = msk_now.time()
    if not (SESSION_TRADING_START <= current_time < SESSION_CLOSING_AUCTION_START):
        return False
    return bool(current_time < guard_end_of_open or current_time >= guard_start_of_close)


def session_clock_decision(*, now: datetime) -> TradingDecision:
    """Проверяет только день недели и обычное торговое время MOEX по МСК.

    Не заменяет проверку праздников, защитного окна и актуального статуса инструмента:
    `allowed=True` здесь означает только допустимое календарное время, не допуск к заявке.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now должен быть tz-aware")
    msk_now = now.astimezone(MSK)
    if msk_now.weekday() >= 5:
        return TradingDecision(allowed=False, reason="выходной день (сб/вс)")
    current_time = msk_now.time()
    if SESSION_ACCEPT_ORDERS_START <= current_time < SESSION_TRADING_START:
        return TradingDecision(
            allowed=False,
            reason=(
                f"аукцион открытия MOEX ({SESSION_ACCEPT_ORDERS_START}"
                f"–{SESSION_TRADING_START} МСК): текущее время {current_time}"
            ),
        )
    if SESSION_CLOSING_AUCTION_START <= current_time < SESSION_TRADING_END:
        return TradingDecision(
            allowed=False,
            reason=(
                f"аукцион закрытия MOEX ({SESSION_CLOSING_AUCTION_START}"
                f"–{SESSION_TRADING_END} МСК): текущее время {current_time}"
            ),
        )
    if not (SESSION_TRADING_START <= current_time < SESSION_CLOSING_AUCTION_START):
        return TradingDecision(
            allowed=False,
            reason=(
                f"вне торгового окна MOEX ({SESSION_TRADING_START}"
                f"–{SESSION_CLOSING_AUCTION_START} МСК): текущее время {current_time}"
            ),
        )
    return TradingDecision(allowed=True, reason="основное торговое время MOEX")


def session_calendar_decision(*, now: datetime, known_holidays: frozenset[date]) -> TradingDecision:
    """Единый календарный фильтр runtime: покрытие, дни-запреты и часы по МСК.

    `known_holidays` должен быть результатом `frozenset(load_known_holidays(path))`.
    Подтверждён только 2026 год (`_CALENDAR_SUPPORTED_YEARS`); другие годы запрещены
    даже при NORMAL_TRADING. Выходные и специальные сессии seed запрещены целиком
    политикой проекта до отдельной поддержки расписания. Защитное окно и API-статус
    проверяются отдельно; `allowed=True` здесь не является разрешением на заявку.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now должен быть tz-aware")
    msk_now = now.astimezone(MSK)
    if msk_now.year not in _CALENDAR_SUPPORTED_YEARS:
        return TradingDecision(
            allowed=False, reason=f"год вне подтверждённого покрытия календаря: {msk_now.year}"
        )
    if msk_now.weekday() >= 5:
        return TradingDecision(allowed=False, reason="выходной день (сб/вс)")
    if msk_now.date() in known_holidays:
        return TradingDecision(
            allowed=False,
            reason=f"праздничный/специальный день запрещён политикой data_seed: {msk_now.date()}",
        )
    return session_clock_decision(now=now)


def can_trade_now(
    gateway: InvestGateway,
    *,
    instrument_id: str,
    now: datetime,
    session_guard_min: int,
    known_holidays: frozenset[date] = frozenset(),
) -> TradingDecision:
    """Проверяет, можно ли торговать инструментом прямо сейчас.

    Порядок проверки (docs/00_PLAN.md §4, docs/04_RISK.md п.2, AGENTS.md §4.6 — статус только
    непосредственно перед действием, не «в начале часа»):
    1. Год вне подтверждённого покрытия или выходной (сб/вс) — отказ без API.
    2. Запрещённый день из `known_holidays` (праздник/спецсессия seed) — отказ.
       Runtime обязан передать загруженный набор; пустой default сохранён только
       для совместимости явных тестовых/инъецированных контекстов.
    3. Аукционы открытия 09:50–10:00 и закрытия 23:40–23:50 МСК — отказ.
    4. Вне основного торгового окна 10:00–23:40 МСК — отказ.
    5. В защитных `session_guard_min` минутах от начала/конца обычных торгов — отказ
       (`session_guard_active`, docs/04_RISK.md п.2); аргумент обязателен и берётся
       вызывающим кодом из конфига, чтобы защиту нельзя было забыть по умолчанию.
    6. Иначе — первичный источник: `gateway.get_trading_status(...)`. Статус,
       отличный от `NORMAL_TRADING_STATUS`, или недоступность API — отказ
       (docs/08_TINVEST_API.md, `data_seed/holidays_ru.md`: «никогда не открывать
       позиции на всякий случай при неизвестном статусе дня»).

    Возврат: `TradingDecision` с причиной и прочитанным статусом (если он
    действительно был получен), а не голый `bool` или угаданный статус.
    """
    calendar_decision = session_calendar_decision(now=now, known_holidays=known_holidays)
    if not calendar_decision.allowed:
        return calendar_decision

    if session_guard_active(now=now, guard_min=session_guard_min):
        return TradingDecision(
            allowed=False,
            reason=(f"защитное окно {session_guard_min} мин у открытия/закрытия сессии"),
            guard_active=True,
        )

    try:
        status = gateway.get_trading_status(instrument_id=instrument_id)
    except GatewayError as exc:
        # Недоступность API — отказ без сырого текста SDK-исключения в логе/journal.
        logger.warning(
            "event=trading_status_unavailable instrument_id=%s ts=%s error_type=%s "
            "action=refuse",
            instrument_id,
            now.astimezone(UTC).isoformat(),
            type(exc).__name__,
        )
        return TradingDecision(
            allowed=False,
            reason="статус торгов недоступен через API",
            refusal_filter="instrument_status_normal",
        )

    if status != NORMAL_TRADING_STATUS:
        return TradingDecision(
            allowed=False,
            reason=f"статус инструмента: {status}",
            trading_status=status,
            refusal_filter="instrument_status_normal",
        )
    return TradingDecision(allowed=True, reason=NORMAL_TRADING_STATUS, trading_status=status)


def utcnow() -> datetime:
    """Единая точка получения текущего времени — tz-aware UTC (docs/02_CODING_RULES.md)."""
    return datetime.now(tz=UTC)
