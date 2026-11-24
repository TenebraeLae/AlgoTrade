"""docs/08_TINVEST_API.md, протокол VERIFY.

Обновление 2026-09-26: пакет `tinkoff-investments` НЕ ставится с PyPI (404 у
`pip install tinkoff-investments`; похоже, tinkoff*-пакеты убраны из индекса), но
реально ставится и импортируется из GitHub-источника:
`pip install "tinkoff-investments @ git+https://github.com/RussianInvestments/invest-python.git@0.2.0-beta117"`
(см. requirements.txt, CHANGELOG_AI.md). Импорт/поля SDK проверяются локально без
сетевых запросов (docs/11_TESTING.md); исторический ручной gRPC-зонд записан в журнале.
Неподтверждённые свойства остаются красными TODO(VERIFY), а не зелёными допущениями.
"""

from __future__ import annotations

import pytest
from tinkoff.invest import AsyncClient, Client
from tinkoff.invest.constants import INVEST_GRPC_API, INVEST_GRPC_API_SANDBOX
from tinkoff.invest.exceptions import RequestError
from tinkoff.invest.schemas import Account, AccountStatus, AccountType


def test_verify_1_package_is_installed_from_github_source() -> None:
    """VERIFY №1: имя пакета подтверждено, PyPI не отдаёт его — ставится с GitHub.

    Точная версия зафиксирована тегом `0.2.0-beta117` в requirements.txt.
    """
    assert Client is not None
    assert AsyncClient is not None


def test_verify_2_grpc_targets_are_real_hostnames() -> None:
    """VERIFY №2: контуры — реальные строки-хосты, не заглушки."""
    assert INVEST_GRPC_API == "invest-public-api.tinkoff.ru"
    assert INVEST_GRPC_API_SANDBOX == "sandbox-invest-public-api.tinkoff.ru"


def test_verify_2_sdk_request_error_type_exists() -> None:
    """VERIFY №2: тип ошибки SDK импортируется без сетевого вызова.

    Семантика неверного токена остаётся красным TODO(VERIFY) ниже: тесты из
    docs/11_TESTING.md не должны обращаться к сети, даже с заведомо неверным токеном.
    Историческая ручная проверка gRPC зафиксирована в CHANGELOG_AI.md.
    """
    assert issubclass(RequestError, Exception)


@pytest.mark.red
def test_verify_2_exact_auth_failure_semantics_not_confirmed() -> None:
    """VERIFY №2: точный код/сообщение при НЕВЕРНОМ, но валидно оформленном токене
    (не при полном обрыве сети, как в историческом ручном зонде) не подтверждён — нужен реальный
    токен в песочнице T-Invest, которого в этой сессии нет.
    """
    pytest.fail(
        "TODO(VERIFY) п.2: нужен реальный токен песочницы, чтобы отличить "
        "UNAUTHENTICATED от UNAVAILABLE — не проверено в этой сессии"
    )


def test_verify_3_account_schema_fields_confirmed() -> None:
    """VERIFY №3: поля `Account` подтверждены интроспекцией дата-класса SDK.

    Важная поправка к первой версии документа: поля `currency` у `Account` НЕТ.
    """
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(Account)}
    assert field_names == {
        "id",
        "type",
        "name",
        "status",
        "opened_date",
        "closed_date",
        "access_level",
    }
    assert AccountStatus.ACCOUNT_STATUS_OPEN.name == "ACCOUNT_STATUS_OPEN"
    assert AccountType.ACCOUNT_TYPE_TINKOFF.name == "ACCOUNT_TYPE_TINKOFF"


def test_sandbox_account_setup_symbols_exist_in_installed_sdk() -> None:
    """Сверяет методы и поля ответа для разрешённого создания счёта песочницы."""
    import dataclasses
    import inspect

    from tinkoff.invest.sandbox.client import SandboxClient
    from tinkoff.invest.schemas import Account, GetAccountsResponse, OpenSandboxAccountResponse
    from tinkoff.invest.services import SandboxService, UsersService

    assert "name" in inspect.signature(SandboxService.open_sandbox_account).parameters
    assert list(inspect.signature(UsersService.get_accounts).parameters) == ["self"]
    with SandboxClient("sdk-symbol-check-only") as client:
        assert isinstance(client.sandbox, SandboxService)
        assert isinstance(client.users, UsersService)
        assert callable(client.users.get_accounts)
    assert "accounts" in {field.name for field in dataclasses.fields(GetAccountsResponse)}
    assert "account_id" in {field.name for field in dataclasses.fields(OpenSandboxAccountResponse)}
    assert {"id", "type", "status"} <= {field.name for field in dataclasses.fields(Account)}


def test_verify_4_share_schema_has_lot_min_increment_trading_status() -> None:
    """VERIFY №4: имя поля — `Share.trading_status` (НЕ `real_trading_status`, как было
    ошибочно предположено в документе). Глубина истории/пагинация `shares()`
    отдельно не исследованы (нужен реальный токен).
    """
    import dataclasses
    import inspect

    from tinkoff.invest.schemas import Share, SharesResponse
    from tinkoff.invest.services import InstrumentsService

    field_names = {f.name for f in dataclasses.fields(Share)}
    assert {
        "lot",
        "min_price_increment",
        "trading_status",
        "short_enabled_flag",
        "figi",
        "sector",
        "currency",
        "ticker",
    } <= field_names
    assert "instruments" in {f.name for f in dataclasses.fields(SharesResponse)}
    assert "self" in inspect.signature(InstrumentsService.shares).parameters
    assert "real_trading_status" not in field_names
    assert "lot_size" not in field_names


def test_automatic_paper_catalog_and_index_discovery_symbols() -> None:
    """Offline symbol proof, not proof of actual IMOEX availability at the broker."""
    import dataclasses
    import inspect

    from tinkoff.invest.schemas import (
        FindInstrumentResponse,
        InstrumentShort,
        InstrumentType,
        RealExchange,
        Share,
        ShareType,
    )
    from tinkoff.invest.services import InstrumentsService

    assert {"query", "instrument_kind"} <= set(
        inspect.signature(InstrumentsService.find_instrument).parameters
    )
    assert "instruments" in {f.name for f in dataclasses.fields(FindInstrumentResponse)}
    assert {"ticker", "uid", "figi", "instrument_kind", "instrument_type"} <= {
        f.name for f in dataclasses.fields(InstrumentShort)
    }
    assert InstrumentType.INSTRUMENT_TYPE_INDEX.name == "INSTRUMENT_TYPE_INDEX"
    assert RealExchange.REAL_EXCHANGE_MOEX.name == "REAL_EXCHANGE_MOEX"
    assert ShareType.SHARE_TYPE_COMMON.name == "SHARE_TYPE_COMMON"
    assert ShareType.SHARE_TYPE_PREFERRED.name == "SHARE_TYPE_PREFERRED"
    assert {
        "real_exchange",
        "otc_flag",
        "api_trade_available_flag",
        "buy_available_flag",
        "sell_available_flag",
        "for_qual_investor_flag",
        "share_type",
    } <= {f.name for f in dataclasses.fields(Share)}


def test_verify_5_normal_trading_status_enum_value_confirmed() -> None:
    """VERIFY №5: полный набор значений enum и имя для «открытой торговли»."""
    from tinkoff.invest.schemas import SecurityTradingStatus

    from trading_bot.api_client import NORMAL_TRADING_STATUS

    names = {s.name for s in SecurityTradingStatus}
    assert NORMAL_TRADING_STATUS in names
    assert len(names) == 17


def test_verify_6_get_candles_signature_and_historic_candle_fields() -> None:
    """VERIFY №6 (частично): метод/поля подтверждены, максимум свечей за запрос — нет."""
    import dataclasses
    import inspect

    from tinkoff.invest.schemas import CandleInterval, HistoricCandle

    with Client("sdk-symbol-check-only", target=INVEST_GRPC_API_SANDBOX) as client:
        params = inspect.signature(client.market_data.get_candles).parameters
        history_params = inspect.signature(client.get_all_candles).parameters
    assert {"figi", "from_", "to", "interval", "instrument_id", "limit"} <= set(params)
    assert {"from_", "to", "interval", "instrument_id"} <= set(history_params)
    assert {f.name for f in dataclasses.fields(HistoricCandle)} == {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "time",
        "is_complete",
        "candle_source",
    }
    assert CandleInterval.CANDLE_INTERVAL_5_MIN.name == "CANDLE_INTERVAL_5_MIN"
    assert CandleInterval.CANDLE_INTERVAL_15_MIN.name == "CANDLE_INTERVAL_15_MIN"
    assert CandleInterval.CANDLE_INTERVAL_HOUR.name == "CANDLE_INTERVAL_HOUR"


@pytest.mark.red
def test_verify_6_max_candles_per_request_not_confirmed() -> None:
    pytest.fail(
        "TODO(VERIFY) п.6: точный максимум свечей за запрос и глубина истории не "
        "подтверждены — нужен реальный токен и большой запрос"
    )


def test_verify_7_market_data_orderbook_service() -> None:
    """SDK подтверждает поля двух сторон стакана; поведение live отдельно не проверено."""
    import dataclasses
    import inspect

    from tinkoff.invest.schemas import GetOrderBookResponse, Order
    from tinkoff.invest.services import MarketDataService

    assert {"instrument_id", "depth"} <= set(
        inspect.signature(MarketDataService.get_order_book).parameters
    )
    assert {"bids", "asks", "orderbook_ts"} <= {
        field.name for field in dataclasses.fields(GetOrderBookResponse)
    }
    assert {"price", "quantity"} <= {field.name for field in dataclasses.fields(Order)}


@pytest.mark.red
def test_verify_8_stream_subscription_limits() -> None:
    pytest.fail(
        "TODO(VERIFY) п.8: метод подписки на стрим, лимит подписок на соединение " "не подтверждены"
    )


@pytest.mark.red
def test_verify_9_order_placement_fields() -> None:
    pytest.fail(
        "TODO(VERIFY) п.9: типы/поля заявки не подтверждены — код заявок не пишется "
        "до M8 и письменного подтверждения оператора (tasks/MILESTONES.md)"
    )


@pytest.mark.red
def test_verify_10_cancel_and_list_orders() -> None:
    pytest.fail("TODO(VERIFY) п.10: отмена заявки, список активных заявок не подтверждены")


@pytest.mark.red
def test_verify_11_portfolio_and_positions_fields() -> None:
    pytest.fail(
        "TODO(VERIFY) п.11: получение позиций/портфеля, доступность P&L от брокера не "
        "подтверждены (это отдельный от счётов вопрос — портфель/позиции, не Account)"
    )


@pytest.mark.red
def test_verify_12_margin_attributes() -> None:
    pytest.fail("TODO(VERIFY) п.12: маржинальные показатели не подтверждены")


def test_candle_page_durations_and_quota_error_contract_confirmed() -> None:
    """Verify installed SDK pagination constants and wrapped status/reset attributes."""
    from datetime import timedelta

    from grpc import StatusCode
    from tinkoff.invest.exceptions import RequestError
    from tinkoff.invest.logging import Metadata
    from tinkoff.invest.schemas import CandleInterval
    from tinkoff.invest.utils import MAX_INTERVALS

    for name, duration in (
        ("CANDLE_INTERVAL_5_MIN", timedelta(days=1)),
        ("CANDLE_INTERVAL_15_MIN", timedelta(days=1)),
        ("CANDLE_INTERVAL_HOUR", timedelta(days=7)),
    ):
        assert MAX_INTERVALS[getattr(CandleInterval, name)] == duration
    metadata = Metadata(None, "100", 0, 12, None)
    error = RequestError(StatusCode.RESOURCE_EXHAUSTED, "synthetic", metadata)
    assert error.code is StatusCode.RESOURCE_EXHAUSTED
    assert error.metadata.ratelimit_reset == 12
    assert error.metadata.ratelimit_remaining == 0
    assert RequestError(StatusCode.UNAUTHENTICATED, "synthetic", None).metadata is None


@pytest.mark.red
def test_verify_13_rate_limits() -> None:
    pytest.fail("TODO(VERIFY) п.13: точные rps-лимиты и поведение при 429 не подтверждены")


def test_verify_14_share_has_short_enabled_flag() -> None:
    """VERIFY №4 (частично): у `Share` есть поле-флаг доступности short.

    Не вызывалось живьём на конкретном инструменте/счёте — только наличие поля.
    """
    import dataclasses

    from tinkoff.invest.schemas import Share

    assert "short_enabled_flag" in {f.name for f in dataclasses.fields(Share)}


@pytest.mark.red
def test_verify_14_short_availability_on_real_account_not_confirmed() -> None:
    pytest.fail(
        "TODO(VERIFY) п.14: значение short_enabled_flag на реальных инструментах и влияние "
        "тарифа счёта не подтверждены — нужен реальный токен"
    )


@pytest.mark.red
def test_verify_15_imoex_benchmark_symbols() -> None:
    pytest.fail("TODO(VERIFY) п.15: тикеры IMOEX через API для бенчмарка не подтверждены")


@pytest.mark.red
def test_verify_16_trading_calendar_source() -> None:
    pytest.fail("TODO(VERIFY) п.16: календарь торговых дней через API не подтверждён")


@pytest.mark.red
def test_verify_17_fee_calculation_source() -> None:
    pytest.fail("TODO(VERIFY) п.17: берётся ли комиссия из API или считается вручную")


@pytest.mark.red
def test_verify_18_corporate_actions_availability() -> None:
    pytest.fail("TODO(VERIFY) п.18: доступность сплитов/дивидендов в API не подтверждена")
