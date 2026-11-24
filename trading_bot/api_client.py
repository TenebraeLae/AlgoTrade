"""Единственная точка входа к T-Invest API v2 (gRPC). Слой L1, docs/08_TINVEST_API.md.

Разрешено сейчас (M2+M3): инициализация клиента, проверка токена, счета,
явно запрошенное создание виртуального счёта только в sandbox, свечи (`get_candles`),
статус торговли инструмента (`get_trading_status`), стрим свечей (`stream_candles`),
read-only каталог MOEX RUB акций и строгий поиск настоящего IMOEX INDEX.
Запрещено: любая заявка (выставление/отмена) — это
M8 и требует отдельного письменного подтверждения оператора (tasks/MILESTONES.md); поэтому
в этом модуле нет ни одного метода, даже стаба, связанного с заявками. Ни один другой
модуль бота не имеет права импортировать `tinkoff.invest` — только этот файл (проверяется
тестом `tests/test_no_direct_sdk_import.py`, M2 acceptance).

Статус пакета в этой сессии (см. CHANGELOG_AI.md): пакет `tinkoff-investments` НЕ ставится
с PyPI (`pip install tinkoff-investments` -> 404, похоже на удаление tinkoff*-пакетов из
индекса), но ставится и реально импортируется из GitHub-источника:
`pip install "tinkoff-investments @ git+https://github.com/RussianInvestments/invest-python.git@0.2.0-beta117"`.
В этой сессии в `.venv` реально проверены (см. CHANGELOG_AI.md): `Client`/`AsyncClient`,
`client.users.get_accounts()`, `INVEST_GRPC_API`/`INVEST_GRPC_API_SANDBOX`
(= `invest-public-api.tinkoff.ru`/`sandbox-invest-public-api.tinkoff.ru`),
`tinkoff.invest.exceptions.RequestError`, поля `Account.{id,type,status}`,
`client.market_data.get_candles(...)`/`get_trading_status(...)`, `HistoricCandle`,
`Quotation`(`units`+`nano`), `SecurityTradingStatus`, `client.create_market_data_stream()`
(`.candles.subscribe([...])`, итерация по `MarketDataResponse.candle` — без `is_complete`,
в отличие от `HistoricCandle`) — и даже живой gRPC-вызов с невалидным токеном
(получен настоящий `RequestError`, а не догадка).
TODO(VERIFY): подтвердить, что тот же `pip install` отработает в целевом окружении деплоя
(другая сеть/прокси) перед M8; точный максимум свечей за запрос и глубина истории
(docs/08 п.6), а также реальная реконнект-семантика стрима при реальном обрыве связи
не исследованы — нужен реальный токен и большой запрос.
"""

from __future__ import annotations

import logging
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event, Lock, local
from time import monotonic, sleep
from typing import TYPE_CHECKING, Literal, TypeVar

from trading_bot.cancellation import OperationCancelled
from trading_bot.config import CandleRequestPolicy

if TYPE_CHECKING:  # импорт только для типов, не тянет SDK во время тестов конфига/секретов
    from tinkoff.invest import Client
    from tinkoff.invest.exceptions import RequestError
    from tinkoff.invest.schemas import Share

logger = logging.getLogger(__name__)
_Result = TypeVar("_Result")


class _CandleSdkLogFilter(logging.Filter):
    """Sanitize verified SDK unary logs emitted before RequestError reaches us."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if (
            isinstance(args, tuple)
            and len(args) >= 2
            and args[1] in ("GetCandles", "GetTradingStatus", "GetOrderBook")
        ):
            record.msg = "event=sdk_market_data_request method=%s"
            record.args = (args[1],)
        return True


_SDK_CANDLE_LOG_FILTER = _CandleSdkLogFilter()

#: Имя enum `SecurityTradingStatus` для открытой торговли (docs/08_TINVEST_API.md п.5,
#: подтверждено интроспекцией SDK). Сравнивать статус торговли с этим
#: именем, а не с литералом в модуле-потребителе.
NORMAL_TRADING_STATUS = "SECURITY_TRADING_STATUS_NORMAL_TRADING"

#: Поддерживаемые таймфреймы из docs/07_CONFIG_SPEC.md (`TF_SIGNAL/TF_CONFIRM/TF_TREND`),
#: отображены на имена `CandleInterval`, подтверждённые интроспекцией (docs/08 п.6).
Timeframe = Literal["5m", "15m", "1h"]
_TIMEFRAME_TO_INTERVAL_NAME: dict[str, str] = {
    "5m": "CANDLE_INTERVAL_5_MIN",
    "15m": "CANDLE_INTERVAL_15_MIN",
    "1h": "CANDLE_INTERVAL_HOUR",
}

#: То же для стрима: `SubscriptionInterval`, подтверждённый интроспекцией
#: (docs/08 п.8) — отдельный enum от `CandleInterval`, используется только в стриме.
_TIMEFRAME_TO_SUBSCRIPTION_INTERVAL_NAME: dict[str, str] = {
    "5m": "SUBSCRIPTION_INTERVAL_FIVE_MINUTES",
    "15m": "SUBSCRIPTION_INTERVAL_FIFTEEN_MINUTES",
    "1h": "SUBSCRIPTION_INTERVAL_ONE_HOUR",
}


def _quotation_to_decimal(units: int, nano: int) -> Decimal:
    """Строит `Decimal` из подтверждённого интроспекцией `Quotation`/`MoneyValue`
    (поля `units`, `nano`) — никакого `float` на пути (docs/02_CODING_RULES.md).
    """
    return Decimal(units) + Decimal(nano) / Decimal(1_000_000_000)


class GatewayError(Exception):
    """Ошибка обращения к брокеру через gRPC (сеть, авторизация, ответ API).

    Часть будущей иерархии `TBotError` (docs/02_CODING_RULES.md); переносится туда,
    когда появится `trading_bot/errors.py`.
    """


class CandleRequestError(GatewayError):
    """Systemic candle transport/auth/response failure; never missing history."""


class CandleQuotaExhaustedError(CandleRequestError):
    """GetCandles quota retry attempts or bounded wait budget exhausted."""


@dataclass
class _MarketDataDispatch:
    deadline: float
    rpc_started: float = 0.0


@dataclass(frozen=True)
class CandleRequestTiming:
    """Inject monotonic time and interruptible sleep, independently of market time."""

    monotonic: Callable[[], float] = monotonic
    sleep: Callable[[float], None] = sleep
    should_stop: Callable[[], bool] = lambda: False


class SdkUnavailableError(GatewayError):
    """SDK `tinkoff.invest` не установлен/не резолвится в этом окружении.

    См. TODO(VERIFY) в docstring модуля — не путать с ошибкой авторизации.
    """


@dataclass(frozen=True)
class AccountInfo:
    """Локальный, суженный контракт счёта — не protobuf-объект наружу.

    Подтверждено реальным вводом/интроспекцией установленного SDK в этой сессии
    (docs/08_TINVEST_API.md п.3): `tinkoff.invest.schemas.Account` имеет поля
    `id, type, name, status, opened_date, closed_date, access_level` — **валюты счёта в
    ответе НЕТ**, это ошибочное предположение из первой версии документа. `type`/`status` —
    строковые имена реальных enum `AccountType`/`AccountStatus` (не сам тип enum —
    чтобы `AccountInfo` оставался используемым и без установленного SDK, например в мок-тестах).
    """

    id: str
    type: str
    status: str


@dataclass(frozen=True)
class ShareMetadata:
    """Проверенные поля акции SDK; идентификатор — FIGI, цены — Decimal."""

    ticker: str
    instrument_id: str
    lot_size: int
    min_price_increment: Decimal
    currency: str
    sector: str
    short_enabled: bool
    trading_status: str


@dataclass(frozen=True)
class PaperShareCatalog:
    """Unambiguous eligible MOEX RUB shares and explicit rejection reasons."""

    shares: dict[str, ShareMetadata]
    excluded: dict[str, str]


@dataclass(frozen=True)
class OrderBookQuote:
    """Лучшие непересекающиеся bid/ask и время получения стакана из API."""

    best_bid: Decimal
    best_ask: Decimal
    observed_at: datetime


@dataclass(frozen=True)
class Candle:
    """Местный контракт свечи (docs/01_ARCHITECTURE.md: `open, high, low, close, volume, ts_open`).

    Подтверждено интроспекцией `tinkoff.invest.schemas.HistoricCandle` (docs/08_TINVEST_API.md п.6):
    поля `open/high/low/close/volume/time/is_complete/candle_source`. `is_complete` добавлен
    сверх минимального контракта из 01_ARCHITECTURE — без него стратегия рискует считать
    сигнал по ещё не закрытой свече.

    Стреаминговые свечи (`MarketDataResponse.candle`) не несут `is_complete` — в отличие
    от `HistoricCandle` из `get_candles`; в `Candle.is_complete` из стрима всегда `None`
    (неизвестно), а не угаданное `True`/`False`.
    """

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    ts_open: datetime
    is_complete: bool | None


def _import_client() -> tuple[type[Client], type[RequestError]]:
    """Ленивый импорт SDK: остальной код бота не должен падать при его отсутствии.

    Отказ: `SdkUnavailableError` с понятной причиной вместо голого `ImportError`
    где-то в середине торгового цикла.
    """
    try:
        from tinkoff.invest import Client
        from tinkoff.invest.exceptions import RequestError
    except ImportError as exc:
        raise SdkUnavailableError(
            "пакет tinkoff-investments не установлен. PyPI-версия его не раздаётся — "
            'ставьте `pip install "tinkoff-investments @ '
            'git+https://github.com/RussianInvestments/invest-python.git@0.2.0-beta117"` '
            "(подтверждено работающим в этой сессии, см. CHANGELOG_AI.md)"
        ) from exc
    return Client, RequestError


def production_target() -> str:
    """Боевой контур T-Invest API. Подтверждено в этой сессии живым импортом:
    `INVEST_GRPC_API == "invest-public-api.tinkoff.ru"`.

    Возврат: адрес gRPC-хоста как строка.
    Отказ: `SdkUnavailableError`, если SDK недоступен.
    """
    try:
        from tinkoff.invest.constants import INVEST_GRPC_API
    except ImportError as exc:
        raise SdkUnavailableError(
            "пакет tinkoff-investments не установлен, см. requirements.txt"
        ) from exc
    return str(INVEST_GRPC_API)


def sandbox_target() -> str:
    """Контур песочницы T-Invest API. Подтверждено в этой сессии живым импортом:
    `INVEST_GRPC_API_SANDBOX == "sandbox-invest-public-api.tinkoff.ru"`.

    Возврат: адрес gRPC-хоста как строка.
    Отказ: `SdkUnavailableError`, если SDK недоступен.
    """
    try:
        from tinkoff.invest.constants import INVEST_GRPC_API_SANDBOX
    except ImportError as exc:
        raise SdkUnavailableError(
            "пакет tinkoff-investments не установлен, см. requirements.txt"
        ) from exc
    return str(INVEST_GRPC_API_SANDBOX)


class InvestGateway:
    """Обёртка над gRPC-клиентом T-Invest API v2 без методов выставления заявок.

    Токен передаётся уже полученным из `security.get_token()` — этот класс сам
    не читает keyring и не знает о его существовании (разделение ответственности).
    """

    def __init__(
        self,
        token: str,
        *,
        target: str,
        client_factory: Callable[[str, str], Client] | None = None,
        candle_policy: CandleRequestPolicy | None = None,
        candle_timing: CandleRequestTiming | None = None,
    ) -> None:
        """Создаёт шлюз. `client_factory` — точка подмены клиента в тестах (мок)."""
        if not token:
            raise ValueError("token не может быть пустым")
        if not target:
            raise ValueError("target не может быть пустым")
        self._token = token
        self._target = target
        self._client_factory = client_factory
        self._discovered_shares: dict[str, ShareMetadata] = {}
        self._candle_policy = candle_policy or CandleRequestPolicy()
        self._candle_timing = candle_timing or CandleRequestTiming()
        self._candle_lock = Lock()
        self._next_candle_rpc = 0.0
        self._market_data_leases: set[object] = set()
        self._market_data_completions: deque[float] = deque()
        self._quota_cooldown = 0.0
        self._worker_stop = local()
        # SDK _errors logs raw details before wrapping grpc.Call; retain an event,
        # but never forward its tracking ID/details through application handlers.
        logging.getLogger("tinkoff.invest.logging").addFilter(_SDK_CANDLE_LOG_FILTER)

    def check_cancelled(self) -> None:
        """Stop cooperatively before further work; an in-flight sync RPC may finish.

        Cancellation is control flow, not a broker failure. No SDK future/cancel
        API is assumed: the shared flag is checked at each supported boundary.
        """
        stop = getattr(self._worker_stop, "event", None)
        if self._candle_timing.should_stop() or (stop is not None and stop.is_set()):
            raise OperationCancelled

    @contextmanager
    def history_worker(self, stop: Event) -> Iterator[None]:
        """Bind a work-scoped stop flag; no database or feed state enters workers."""
        self._worker_stop.event = stop
        try:
            self.check_cancelled()
            yield
        finally:
            self._worker_stop.event = None

    def _request_sleep(self, seconds: float) -> None:
        # Work-scoped cancellation must also wake abandoned/failed refresh jobs.
        if getattr(self._worker_stop, "event", None) is None:
            self._candle_timing.sleep(seconds)
        else:
            until = self._candle_timing.monotonic() + seconds
            while (remaining := until - self._candle_timing.monotonic()) > 0:
                self.check_cancelled()
                self._candle_timing.sleep(min(remaining, 0.1))
        self.check_cancelled()

    def _admit_market_data(self, deadline: float) -> tuple[object, float]:
        """Reserve a lease which cannot age out before a delayed callback starts."""
        lease, deadline = self._wait_market_data(deadline, reserve=True)
        assert lease is not None
        return lease, deadline

    def _wait_market_data(self, deadline: float, *, reserve: bool) -> tuple[object | None, float]:
        """Check local budget or dispatch pacing/cooldown under one short lock.

        Active leases plus completions in the last60s never exceed the budget.
        Retaining credits until completion+60s is deliberately conservative: RPC
        latency/scheduling gaps cannot move an actual start outside accounting.
        No lock is held during a wait or callback. Dispatch is linearized at the
        final locked check; already dispatched work may finish after a new reset.
        """
        timing = self._candle_timing
        while True:
            self.check_cancelled()
            with self._candle_lock:
                now = timing.monotonic()
                completions = self._market_data_completions
                while completions and completions[0] <= now - 60:
                    completions.popleft()
                quota_wait = max(0.0, self._quota_cooldown - now)
                full = reserve and len(self._market_data_leases) + len(completions) >= (
                    self._candle_policy.market_data_requests_per_minute
                )
                # All credits may belong to still-pending/in-flight calls. Their
                # completion time is unknown; poll without ageing their leases.
                window = (completions[0] + 60 if completions else now + 0.1) if full else now
                delay = quota_wait or max(0.0, self._next_candle_rpc - now, window - now)
                if not quota_wait and (delay > max(0.0, deadline - now) or now > deadline):
                    raise CandleRequestError("MarketData transport wait budget exhausted")
                if not delay:
                    self.check_cancelled()
                    if reserve:
                        lease = object()
                        self._market_data_leases.add(lease)
                        return lease, deadline
                    self._next_candle_rpc = now + self._candle_policy.min_interval_sec
                    return None, deadline
            before = timing.monotonic()
            self._request_sleep(delay)
            if quota_wait:
                deadline += timing.monotonic() - before

    @staticmethod
    def _quota_retry_delay(error: RequestError) -> float:
        reset = getattr(error.metadata, "ratelimit_reset", None)
        hint = reset if isinstance(reset, int) and not isinstance(reset, bool) else 0
        return max(1.0, hint)

    @contextmanager
    def _market_data_dispatch(self, state: _MarketDataDispatch) -> Iterator[None]:
        from grpc import StatusCode  # type: ignore[import-untyped]  # SDK dependency, no stubs
        from tinkoff.invest.exceptions import RequestError

        lease, state.deadline = self._admit_market_data(state.deadline)
        try:
            # A lease may have waited behind the scheduler while another call
            # received a server reset. Recheck immediately before dispatch.
            _, state.deadline = self._wait_market_data(state.deadline, reserve=False)
            state.rpc_started = self._candle_timing.monotonic()
            try:
                yield
            except RequestError as exc:
                if exc.code == StatusCode.RESOURCE_EXHAUSTED:
                    with self._candle_lock:
                        self._quota_cooldown = max(
                            self._quota_cooldown,
                            self._candle_timing.monotonic() + self._quota_retry_delay(exc),
                        )
                raise
        finally:
            with self._candle_lock:
                self._market_data_leases.remove(lease)
                self._market_data_completions.append(self._candle_timing.monotonic())

    def _make_client(self) -> Client:
        self.check_cancelled()
        if self._client_factory is not None:
            client = self._client_factory(self._token, self._target)
        else:
            client_cls, _ = _import_client()
            client = client_cls(self._token, target=self._target)
        self.check_cancelled()
        return client

    def _make_sandbox_client(self) -> Client:
        """Создаёт поддерживаемый SDK-клиент для чтения счетов песочницы."""
        if self._client_factory is not None:
            return self._client_factory(self._token, self._target)
        try:
            from tinkoff.invest.sandbox.client import SandboxClient
        except ImportError as exc:
            raise SdkUnavailableError(
                "SandboxClient недоступен в установленном tinkoff-investments"
            ) from exc
        return SandboxClient(self._token)

    def list_accounts(self) -> list[AccountInfo]:
        """Возвращает счёта через подтверждённый `client.users.get_accounts()`.

        Возврат: список `AccountInfo` (`id`, `type`, `status` — все подтверждены, валюты
        в ответе счёта нет — docs/08_TINVEST_API.md п.3).
        Отказ: `GatewayError` — сеть/авторизация/ответ API; `SdkUnavailableError`,
        если пакет SDK не установлен.
        """
        try:
            with self._make_client() as client:
                response = client.users.get_accounts()
        except (SdkUnavailableError, OperationCancelled):
            raise
        except Exception as exc:
            # Единственная осознанная граница адаптера: любая ошибка транспорта/SDK
            # конвертируется в типизированный GatewayError, а не утекает наружу как
            # исключение библиотеки (docs/08_TINVEST_API.md, «что запрещено»).
            raise GatewayError(f"не удалось получить список счетов: {exc}") from exc
        return [
            AccountInfo(id=account.id, type=account.type.name, status=account.status.name)
            for account in response.accounts
        ]

    def _require_sandbox_target(self) -> None:
        """Не допускает операций со счётом песочницы на другом gRPC-контуре."""
        if self._target != sandbox_target():
            raise ValueError("операции sandbox-счёта разрешены только для sandbox target")

    def list_sandbox_accounts(self) -> list[AccountInfo]:
        """Читает виртуальные счета через `SandboxClient.users.get_accounts()`.

        На другом контуре отказывает до создания клиента; при сетевой ошибке бросает
        `GatewayError`, при отсутствии SDK — `SdkUnavailableError`.
        """
        self._require_sandbox_target()
        try:
            with self._make_sandbox_client() as client:
                response = client.users.get_accounts()
            return [
                AccountInfo(id=account.id, type=account.type.name, status=account.status.name)
                for account in response.accounts
            ]
        except (SdkUnavailableError, OperationCancelled):
            raise
        except Exception as exc:
            logger.error(
                "sandbox users.get_accounts не выполнен; target=%s time=%s error_type=%s; "
                "пропустить чтение счетов",
                self._target,
                datetime.now(tz=UTC).isoformat(),
                type(exc).__name__,
            )
            raise GatewayError(
                f"не удалось получить sandbox-счета ({type(exc).__name__}); "
                "проверьте транспорт и авторизацию"
            ) from exc

    def open_sandbox_account(self) -> str:
        """Явно создаёт один виртуальный счёт только в sandbox и возвращает его ID.

        Метод не вызывается автоматически и не создаёт реальных брокерских счетов.
        При ошибке SDK/транспорта бросает `GatewayError`; без SDK —
        `SdkUnavailableError`. Если результат неоднозначен, не повторять вызов
        вслепую: сначала запросить `list_sandbox_accounts()`.
        """
        self._require_sandbox_target()
        try:
            with self._make_client() as client:
                account_id: str = client.sandbox.open_sandbox_account().account_id
            if not account_id:
                logger.error(
                    "Пустой account_id после открытия sandbox-счёта; target=%s", self._target
                )
                raise GatewayError(
                    "открытие sandbox-счёта вернуло пустой account_id; проверьте список "
                    "sandbox-счетов перед повтором"
                )
            return account_id
        except (SdkUnavailableError, OperationCancelled):
            raise
        except GatewayError:
            raise
        except Exception as exc:
            logger.error("Неизвестный результат открытия sandbox-счёта; target=%s", self._target)
            raise GatewayError(
                "открытие sandbox-счёта не подтверждено: результат неизвестен; "
                "проверьте список sandbox-счетов перед повтором"
            ) from exc

    def check_token(self) -> bool:
        """Проверяет валидность токена вызовом `list_accounts()`.

        Возврат: `True`, если запрос счетов прошёл без ошибки транспорта/авторизации.
        Отказ: не бросает — при `GatewayError` возвращает `False` (само по себе не
        различает «неверный токен» от «нет сети»: docs/08_TINVEST_API.md п.2,13 —
        TODO(VERIFY) точные коды ошибок для различения этих случаев.
        """
        try:
            self.list_accounts()
        except GatewayError as exc:
            logger.warning("Проверка токена не прошла: %s", exc)
            return False
        return True

    def get_candles(
        self,
        *,
        instrument_id: str,
        timeframe: Timeframe,
        from_: datetime,
        to: datetime | None = None,
        limit: int | None = None,
    ) -> list[Candle]:
        """Свечи через подтверждённый `client.market_data.get_candles(...)`.

        `timeframe` — одно из `"5m"/"15m"/"1h"` (docs/07_CONFIG_SPEC.md), внутри мапится
        на реальный `CandleInterval` (docs/08_TINVEST_API.md п.6). `from_`/`to` — только
        tz-aware (docs/02_CODING_RULES.md).

        Возврат: свечи в виде `Candle` (цены/объём — `Decimal`, без `float`).
        Отказ: `ValueError` на неверный таймфрейм/наивные даты; `GatewayError` —
        сеть/ответ API; `SdkUnavailableError`, если пакет SDK не установлен.
        """
        self.check_cancelled()
        if timeframe not in _TIMEFRAME_TO_INTERVAL_NAME:
            raise ValueError(
                f"неподдерживаемый таймфрейм: {timeframe!r}, ожидается один из "
                f"{sorted(_TIMEFRAME_TO_INTERVAL_NAME)}"
            )
        if from_.tzinfo is None or from_.utcoffset() is None:
            raise ValueError("from_ должен быть tz-aware (UTC)")
        if to is not None and (to.tzinfo is None or to.utcoffset() is None):
            raise ValueError("to должен быть tz-aware (UTC)")

        try:
            if self._client_factory is not None:
                # В мок-режиме (тесты) реальный enum не нужен — фейк-клиент принимает
                # имя как есть; реальный SDK требует именно `CandleInterval`.
                interval: object = _TIMEFRAME_TO_INTERVAL_NAME[timeframe]
            else:
                from tinkoff.invest.schemas import CandleInterval

                interval = getattr(CandleInterval, _TIMEFRAME_TO_INTERVAL_NAME[timeframe])
            with self._make_client() as client:
                self.check_cancelled()

                def request() -> list[Candle]:
                    self.check_cancelled()
                    response = client.market_data.get_candles(
                        instrument_id=instrument_id,
                        from_=from_,
                        to=to,
                        interval=interval,  # type: ignore[arg-type]
                        limit=limit,
                    )
                    self.check_cancelled()
                    return [
                        Candle(
                            open=_quotation_to_decimal(c.open.units, c.open.nano),
                            high=_quotation_to_decimal(c.high.units, c.high.nano),
                            low=_quotation_to_decimal(c.low.units, c.low.nano),
                            close=_quotation_to_decimal(c.close.units, c.close.nano),
                            volume=c.volume,
                            ts_open=c.time,
                            is_complete=c.is_complete,
                        )
                        for c in response.candles
                    ]

                candles = self._request_candles(request, instrument_id, timeframe)
                self.check_cancelled()
            self.check_cancelled()
            return candles
        except (SdkUnavailableError, CandleRequestError, OperationCancelled):
            raise
        except ImportError as exc:
            raise SdkUnavailableError("пакет tinkoff-investments недоступен") from exc
        except Exception as exc:
            self.check_cancelled()
            logger.error(
                "event=candle_request_failed instrument_id=%s timeframe=%s "
                "error_type=%s action=stop",
                instrument_id,
                timeframe,
                type(exc).__name__,
            )
            raise CandleRequestError("GetCandles unavailable; check transport and SDK") from exc

    def get_candles_history(
        self, *, instrument_id: str, timeframe: Timeframe, from_: datetime, to: datetime
    ) -> list[Candle]:
        """Fetch bounded history with paced, independently retried GetCandles pages.

        Page durations come from installed SDK MAX_INTERVALS. Adjacent requests
        overlap at the boundary, avoiding the SDK helper's extra interval step
        for unaligned dates. Return sorted, timestamp-distinct candles within
        the inclusive requested range; conflicting duplicates stop the request.
        Empty successful history remains distinct from transport/quota failure.
        """
        history: list[Candle] = []
        for page in self.iter_candle_pages(
            instrument_id=instrument_id, timeframe=timeframe, from_=from_, to=to
        ):
            self.check_cancelled()
            history.extend(page)
        self.check_cancelled()
        return sorted(history, key=lambda candle: candle.ts_open)

    def iter_candle_pages(
        self, *, instrument_id: str, timeframe: Timeframe, from_: datetime, to: datetime
    ) -> Iterator[list[Candle]]:
        """Yield one sorted, timestamp-distinct GetCandles page per advancement.

        Lazy validation and page bounds match get_candles_history, including its
        inclusive range and SDK MAX_INTERVALS. Each next fetches at most one page
        through get_candles (bounded retries of that page may perform more RPCs).
        Empty successful pages yield []; no prefetch or lock survives a yield.
        Identical duplicates are omitted, including across adjacent boundaries;
        conflicting duplicates or naive timestamps raise CandleRequestError.
        Cancellation and request failures propagate: callers must stage all pages
        and publish only after successful exhaustion, never publish a prefix.
        """
        self.check_cancelled()
        if timeframe not in _TIMEFRAME_TO_INTERVAL_NAME:
            raise ValueError(f"неподдерживаемый таймфрейм {timeframe!r}")
        if (
            from_.tzinfo is None
            or from_.utcoffset() is None
            or to.tzinfo is None
            or to.utcoffset() is None
            or to <= from_
        ):
            raise ValueError("границы истории должны быть упорядоченными tz-aware датами")
        try:
            from tinkoff.invest.schemas import CandleInterval
            from tinkoff.invest.utils import MAX_INTERVALS
        except ImportError as exc:
            raise SdkUnavailableError("пакет tinkoff-investments недоступен") from exc
        interval = getattr(CandleInterval, _TIMEFRAME_TO_INTERVAL_NAME[timeframe])
        page_duration = MAX_INTERVALS[interval]
        history: dict[datetime, Candle] = {}
        page_start = from_
        logger.info(
            "event=candle_history_start instrument_id=%s timeframe=%s from=%s to=%s",
            instrument_id,
            timeframe,
            from_.isoformat(),
            to.isoformat(),
        )
        while page_start < to:
            self.check_cancelled()
            page_end = min(page_start + page_duration, to)
            page = self.get_candles(
                instrument_id=instrument_id, timeframe=timeframe, from_=page_start, to=page_end
            )
            self.check_cancelled()
            distinct: dict[datetime, Candle] = {}
            for candle in page:
                self.check_cancelled()
                if candle.ts_open.tzinfo is None or candle.ts_open.utcoffset() is None:
                    raise CandleRequestError("GetCandles returned a naive timestamp")
                if not from_ <= candle.ts_open <= to:
                    continue
                previous = history.get(candle.ts_open)
                if previous is not None and previous != candle:
                    raise CandleRequestError("GetCandles returned conflicting duplicate candles")
                if previous is None:
                    distinct[candle.ts_open] = candle
                history[candle.ts_open] = candle
            logger.info(
                "event=candle_history_page instrument_id=%s timeframe=%s from=%s to=%s "
                "candles=%s action=continue",
                instrument_id,
                timeframe,
                page_start.isoformat(),
                page_end.isoformat(),
                len(page),
            )
            page_start = page_end
            result = [distinct[ts] for ts in sorted(distinct)]
            self.check_cancelled()
            yield result
        self.check_cancelled()

    def _request_candles(
        self, request: Callable[[], _Result], instrument_id: str, timeframe: str
    ) -> _Result:
        """Admit all unary MarketData attempts, independently bounding quota/transport.

        RequestError.code and relative metadata.ratelimit_reset are verified SDK
        attributes. No raw exception/details/tracking metadata is logged.
        """
        from grpc import StatusCode
        from tinkoff.invest.exceptions import RequestError

        policy, timing = self._candle_policy, self._candle_timing
        deadline = timing.monotonic() + policy.max_retry_wait_sec
        total_attempt = transport_attempt = 0
        while True:
            self.check_cancelled()
            dispatch = _MarketDataDispatch(deadline)
            total_attempt += 1
            try:
                with self._market_data_dispatch(dispatch):
                    result = request()
                    self.check_cancelled()
                return result
            except RequestError as exc:
                self.check_cancelled()
                deadline = dispatch.deadline
                quota = exc.code == StatusCode.RESOURCE_EXHAUSTED
                if not quota and exc.code != StatusCode.UNAVAILABLE:
                    logger.error(
                        "event=candle_request_failed instrument_id=%s timeframe=%s "
                        "status=%s action=stop",
                        instrument_id,
                        timeframe,
                        exc.code.name,
                    )
                    raise CandleRequestError("MarketData transport/auth failure") from exc
                if quota:
                    # Quota may wait beyond transport elapsed, but never beyond
                    # the overall attempt cap (mixed status attempts also count).
                    deadline += timing.monotonic() - dispatch.rpc_started
                    delay = self._quota_retry_delay(exc)
                    exhausted = total_attempt >= policy.quota_max_attempts
                    attempt = total_attempt
                else:
                    transport_attempt += 1
                    delay = policy.retry_base_sec
                    with self._candle_lock:
                        self._next_candle_rpc = max(
                            self._next_candle_rpc, timing.monotonic() + delay
                        )
                    exhausted = (
                        total_attempt >= policy.quota_max_attempts
                        or transport_attempt >= policy.max_attempts
                        or delay > max(0.0, deadline - timing.monotonic())
                    )
                    attempt = transport_attempt
                logger.warning(
                    "event=%s instrument_id=%s timeframe=%s attempt=%s wait_sec=%s action=%s",
                    "candle_request_quota" if quota else "candle_request_unavailable",
                    instrument_id,
                    timeframe,
                    attempt,
                    delay,
                    "stop" if exhausted else "retry_current_page",
                )
                if exhausted:
                    error = CandleQuotaExhaustedError if quota else CandleRequestError
                    raise error("MarketData retry attempts/wait budget exhausted") from exc

    def get_trading_status(self, *, instrument_id: str) -> str:
        """Статус торговли инструмента через подтверждённый
        `client.market_data.get_trading_status(instrument_id=...)`.

        Возврат: имя реального enum `SecurityTradingStatus` (например,
        `NORMAL_TRADING_STATUS` при открытой торговле — docs/08_TINVEST_API.md п.5).
        Отказ: `GatewayError` — сеть/ответ API; `SdkUnavailableError`, если пакет
        SDK не установлен.
        """
        self.check_cancelled()
        try:
            with self._make_client() as client:
                self.check_cancelled()
                response = self._request_candles(
                    lambda: client.market_data.get_trading_status(instrument_id=instrument_id),
                    instrument_id,
                    "get_trading_status",
                )
                self.check_cancelled()
            self.check_cancelled()
        except (SdkUnavailableError, GatewayError, OperationCancelled):
            raise
        except Exception as exc:
            self.check_cancelled()
            logger.error(
                "event=trading_status_failed instrument_id=%s error_type=%s action=stop",
                instrument_id,
                type(exc).__name__,
            )
            raise GatewayError(f"не удалось получить статус торговли {instrument_id}") from exc
        return str(response.trading_status.name)

    @staticmethod
    def _share_info(share: Share) -> ShareMetadata:
        info = ShareMetadata(
            ticker=share.ticker,
            instrument_id=share.figi,
            lot_size=share.lot,
            min_price_increment=_quotation_to_decimal(
                share.min_price_increment.units, share.min_price_increment.nano
            ),
            currency=share.currency.upper(),
            sector=share.sector,
            short_enabled=share.short_enabled_flag,
            trading_status=share.trading_status.name,
        )
        if (
            not info.ticker
            or not info.instrument_id
            or info.lot_size <= 0
            or not info.min_price_increment.is_finite()
            or info.min_price_increment <= 0
            or not info.currency
            or not info.sector.strip()
        ):
            raise GatewayError("incomplete share metadata")
        return info

    def discover_paper_shares(self, *, pin: bool = True) -> PaperShareCatalog:
        """Read shares once; reject duplicate tickers and non-tradable/non-MOEX assets.

        Static trading_status is not an eligibility gate (bootstrap may run at night).
        Fresh status is still required by the feed immediately before each paper tick.
        Qualified-only shares are excluded: no account eligibility is assumed.
        pin=True preserves bootstrap identity pinning; pin=False returns a fresh
        filtered catalog without replacing or mutating previously pinned shares.
        """
        self.check_cancelled()
        try:
            with self._make_client() as client:
                self.check_cancelled()
                shares = client.instruments.shares().instruments
                self.check_cancelled()
            self.check_cancelled()
            counts: dict[str, int] = {}
            for share in shares:
                self.check_cancelled()
                counts[share.ticker] = counts.get(share.ticker, 0) + 1
            accepted: dict[str, ShareMetadata] = {}
            excluded: dict[str, str] = {}
            for share in shares:
                self.check_cancelled()
                ticker = share.ticker
                reason = ""
                if not ticker or counts[ticker] != 1:
                    reason = "ambiguous_or_empty_ticker"
                elif share.real_exchange.name != "REAL_EXCHANGE_MOEX" or share.otc_flag:
                    reason = "not_regular_moex_share"
                elif share.currency.lower() != "rub":
                    reason = "not_rub"
                elif share.share_type.name not in {"SHARE_TYPE_COMMON", "SHARE_TYPE_PREFERRED"}:
                    reason = "not_common_or_preferred_share"
                elif not (
                    share.api_trade_available_flag
                    and share.buy_available_flag
                    and share.sell_available_flag
                ):
                    reason = "not_api_buy_sell_available"
                elif share.for_qual_investor_flag:
                    reason = "qualified_investor_only"
                if not reason:
                    try:
                        accepted[ticker] = self._share_info(share)
                    except GatewayError:
                        reason = "incomplete_metadata"
                if reason:
                    excluded[ticker] = reason
            # Freeze identity for this process; never rediscover selected ticker by name.
            self.check_cancelled()
            if pin:
                self._discovered_shares = accepted.copy()
            return PaperShareCatalog(dict(sorted(accepted.items())), dict(sorted(excluded.items())))
        except (SdkUnavailableError, OperationCancelled):
            raise
        except Exception as exc:
            self.check_cancelled()
            logger.error(
                "event=paper_bootstrap_failed stage=share_catalog ts=%s error_type=%s "
                "action=stop remedy=check_catalog_transport_and_sdk",
                datetime.now(tz=UTC).isoformat(),
                type(exc).__name__,
            )
            raise GatewayError("share catalog unavailable; check transport and SDK") from exc

    def resolve_imoex_id(self) -> str:
        """Resolve exactly one exact IMOEX of SDK kind INDEX, never an ETF/substitute.

        No assertion that a broker actually provides this index: empty or ambiguous
        responses fail closed. UID is preferred because indices may have no FIGI.
        """
        self.check_cancelled()
        try:
            from tinkoff.invest.schemas import InstrumentType

            with self._make_client() as client:
                self.check_cancelled()
                instruments = client.instruments.find_instrument(
                    query="IMOEX", instrument_kind=InstrumentType.INSTRUMENT_TYPE_INDEX
                ).instruments
                self.check_cancelled()
            self.check_cancelled()
            matches = [
                item
                for item in instruments
                if item.ticker == "IMOEX" and item.instrument_kind.name == "INSTRUMENT_TYPE_INDEX"
            ]
            identifiers = {item.uid or item.figi for item in matches}
            if len(identifiers) != 1 or "" in identifiers:
                raise GatewayError("exact IMOEX index missing or ambiguous")
            self.check_cancelled()
            return str(identifiers.pop())
        except (SdkUnavailableError, OperationCancelled):
            raise
        except Exception as exc:
            self.check_cancelled()
            logger.error(
                "event=paper_bootstrap_failed stage=imoex_discovery ts=%s error_type=%s "
                "action=stop remedy=verify_exact_IMOEX_INDEX_catalog_availability",
                datetime.now(tz=UTC).isoformat(),
                type(exc).__name__,
            )
            raise GatewayError("exact IMOEX index unavailable; no substitute permitted") from exc

    def get_share_metadata(self, *, ticker: str) -> ShareMetadata:
        """Читает поля акции через подтверждённый SDK `instruments.shares()`.

        Не выбирает один из нескольких инструментов с одинаковым тикером: без
        class_code соответствие неоднозначно, поэтому отказывает до торговли.
        """
        self.check_cancelled()
        if not ticker:
            raise ValueError("ticker не может быть пустым")
        if ticker in self._discovered_shares:
            return self._discovered_shares[ticker]
        try:
            with self._make_client() as client:
                self.check_cancelled()
                shares = client.instruments.shares().instruments
                self.check_cancelled()
            self.check_cancelled()
            matches = [share for share in shares if share.ticker == ticker]
            if len(matches) != 1:
                reason = "не найден" if not matches else "найдено несколько"
                raise GatewayError(f"инструмент {ticker}: {reason} совпадений")
            info = self._share_info(matches[0])
            self.check_cancelled()
            return info
        except (SdkUnavailableError, GatewayError, OperationCancelled):
            raise
        except Exception as exc:
            self.check_cancelled()
            logger.error(
                "event=share_metadata_failed ticker=%s ts=%s error_type=%s action=stop",
                ticker,
                datetime.now(tz=UTC).isoformat(),
                type(exc).__name__,
            )
            raise GatewayError(f"не удалось получить параметры акции {ticker}") from exc

    def get_order_book_quote(self, *, instrument_id: str) -> OrderBookQuote:
        """Запрашивает стакан глубиной 1 и отказывает при пустом/битом bid-ask.

        Не полагается на порядок уровней в SDK: лучший bid=max, ask=min.
        """
        self.check_cancelled()
        if not instrument_id:
            raise ValueError("instrument_id не может быть пустым")
        try:
            with self._make_client() as client:
                self.check_cancelled()
                book = self._request_candles(
                    lambda: client.market_data.get_order_book(instrument_id=instrument_id, depth=1),
                    instrument_id,
                    "get_order_book",
                )
                self.check_cancelled()
            self.check_cancelled()
            bids = [
                _quotation_to_decimal(order.price.units, order.price.nano)
                for order in book.bids
                if order.quantity > 0
            ]
            asks = [
                _quotation_to_decimal(order.price.units, order.price.nano)
                for order in book.asks
                if order.quantity > 0
            ]
            if not bids or not asks:
                raise GatewayError(f"стакан {instrument_id}: нет двух сторон")
            bid, ask = max(bids), min(asks)
            if (
                not bid.is_finite()
                or not ask.is_finite()
                or bid <= 0
                or ask <= bid
                or book.orderbook_ts is None
                or book.orderbook_ts.tzinfo is None
                or book.orderbook_ts.utcoffset() is None
            ):
                raise GatewayError(f"стакан {instrument_id}: цены или время некорректны")
            self.check_cancelled()
            return OrderBookQuote(best_bid=bid, best_ask=ask, observed_at=book.orderbook_ts)
        except (SdkUnavailableError, GatewayError, OperationCancelled):
            raise
        except Exception as exc:
            self.check_cancelled()
            logger.error(
                "event=order_book_failed instrument_id=%s ts=%s error_type=%s action=stop",
                instrument_id,
                datetime.now(tz=UTC).isoformat(),
                type(exc).__name__,
            )
            raise GatewayError(f"не удалось получить стакан {instrument_id}") from exc

    def stream_candles(
        self, *, instrument_ids: list[str], timeframe: Timeframe
    ) -> Iterator[Candle]:
        """Стрим свечей через подтверждённый `client.create_market_data_stream()`
        (`.candles.subscribe([...])`, итерация по `MarketDataResponse.candle`).

        Генератор держит gRPC-соединение открытым, пока его итерируют; при
        ошибке/закрытии стрим останавливается (`stream.stop()`). Переподключение
        при обрыве — задача вызывающего (`market_data.StreamReconnector`), не этого метода.

        возврат: генератор `Candle` (`is_complete` всегда `None` — стрим этот флаг не даёт,
        docs/08_TINVEST_API.md п.6/8).
        Отказ: `ValueError` на неверный таймфрейм/пустой список инструментов; `GatewayError` при
        обрыве стрима (не перевыкидывает сам — это делает `StreamReconnector`);
        `SdkUnavailableError`, если пакет SDK не установлен.
        """
        if timeframe not in _TIMEFRAME_TO_SUBSCRIPTION_INTERVAL_NAME:
            raise ValueError(
                f"неподдерживаемый таймфрейм: {timeframe!r}, ожидается один из "
                f"{sorted(_TIMEFRAME_TO_SUBSCRIPTION_INTERVAL_NAME)}"
            )
        if not instrument_ids:
            raise ValueError("instrument_ids не могут быть пустым")

        try:
            if self._client_factory is not None:
                instruments: list[object] = [
                    {
                        "instrument_id": instrument_id,
                        "interval": _TIMEFRAME_TO_SUBSCRIPTION_INTERVAL_NAME[timeframe],
                    }
                    for instrument_id in instrument_ids
                ]
            else:
                from tinkoff.invest.schemas import CandleInstrument, SubscriptionInterval

                interval = getattr(
                    SubscriptionInterval, _TIMEFRAME_TO_SUBSCRIPTION_INTERVAL_NAME[timeframe]
                )
                instruments = [
                    CandleInstrument(instrument_id=instrument_id, interval=interval)
                    for instrument_id in instrument_ids
                ]
        except ImportError as exc:
            raise SdkUnavailableError(
                "пакет tinkoff-investments не установлен, см. requirements.txt"
            ) from exc

        try:
            with self._make_client() as client:
                stream = client.create_market_data_stream()
                stream.candles.subscribe(instruments)  # type: ignore[arg-type]
                try:
                    for response in stream:
                        candle = getattr(response, "candle", None)
                        if candle is None:
                            continue
                        yield Candle(
                            open=_quotation_to_decimal(candle.open.units, candle.open.nano),
                            high=_quotation_to_decimal(candle.high.units, candle.high.nano),
                            low=_quotation_to_decimal(candle.low.units, candle.low.nano),
                            close=_quotation_to_decimal(candle.close.units, candle.close.nano),
                            volume=candle.volume,
                            ts_open=candle.time,
                            is_complete=None,
                        )
                finally:
                    stream.stop()
        except (SdkUnavailableError, OperationCancelled):
            raise
        except Exception as exc:
            raise GatewayError(f"стрим свечей прерван: {exc}") from exc
