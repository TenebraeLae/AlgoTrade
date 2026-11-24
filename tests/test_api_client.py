"""Тесты api_client.py: InvestGateway на моке клиента, без сети и без реального SDK."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_bot.api_client import (
    NORMAL_TRADING_STATUS,
    AccountInfo,
    Candle,
    GatewayError,
    InvestGateway,
    SdkUnavailableError,
    production_target,
    sandbox_target,
)


class _FakeRequestError(Exception):
    """Локальная замена tinkoff.invest.exceptions.RequestError для теста ошибки."""


def _fake_account(account_id: str) -> SimpleNamespace:
    """Минимальная имитация `tinkoff.invest.schemas.Account` (id/type/status подтверждены,
    см. tests/test_sdk_symbols_exist.py::test_verify_3_account_schema_fields_confirmed).
    """
    return SimpleNamespace(
        id=account_id,
        type=SimpleNamespace(name="ACCOUNT_TYPE_TINKOFF"),
        status=SimpleNamespace(name="ACCOUNT_STATUS_OPEN"),
    )


def _fake_quotation(units: int, nano: int = 0) -> SimpleNamespace:
    """Имитация `tinkoff.invest.schemas.Quotation` (поля `units`/`nano` подтверждены,
    см. tests/test_sdk_symbols_exist.py).
    """
    return SimpleNamespace(units=units, nano=nano)


def _fake_historic_candle(price: int, volume: int, ts: datetime) -> SimpleNamespace:
    """Имитация `tinkoff.invest.schemas.HistoricCandle` с одинаковым OHLC для простоты."""
    return SimpleNamespace(
        open=_fake_quotation(price),
        high=_fake_quotation(price),
        low=_fake_quotation(price),
        close=_fake_quotation(price),
        volume=volume,
        time=ts,
        is_complete=True,
    )


def _fake_stream_candle(price: int, volume: int, ts: datetime) -> SimpleNamespace:
    """Имитация `MarketDataResponse.candle` (стрим) — без `is_complete`,
    в отличие от `HistoricCandle` (см. tests/test_sdk_symbols_exist.py п.6).
    """
    return SimpleNamespace(
        open=_fake_quotation(price),
        high=_fake_quotation(price),
        low=_fake_quotation(price),
        close=_fake_quotation(price),
        volume=volume,
        time=ts,
    )


class _FakeStream:
    """Имитация `MarketDataStreamManager`: `.candles.subscribe(...)`, `.stop()`, итерация."""

    def __init__(self, events: list[object], error: Exception | None) -> None:
        self._events = list(events)
        self._error = error
        self.stopped = False
        self.subscribed: object | None = None

    @property
    def candles(self) -> SimpleNamespace:
        def subscribe(instruments: object) -> None:
            self.subscribed = instruments

        return SimpleNamespace(subscribe=subscribe)

    def stop(self) -> None:
        self.stopped = True

    def __iter__(self) -> _FakeStream:
        return self

    def __next__(self) -> object:
        if self._events:
            return self._events.pop(0)
        if self._error is not None:
            raise self._error
        raise StopIteration


class _FakeClient:
    def __init__(
        self,
        accounts: list[str] | None,
        error: Exception | None,
        candles: list[SimpleNamespace] | None = None,
        trading_status: str | None = None,
        stream_events: list[object] | None = None,
        stream_error: Exception | None = None,
        sandbox_accounts: list[str] | None = None,
        sandbox_open_id: str = "sandbox-new",
        use_sandbox_users: bool = False,
    ) -> None:
        self._accounts = accounts or []
        self._sandbox_accounts = sandbox_accounts or []
        self._users_accounts = self._sandbox_accounts if use_sandbox_users else self._accounts
        self._sandbox_open_id = sandbox_open_id
        self.sandbox_open_calls = 0
        self.sandbox_users_list_calls = 0
        self.deprecated_sandbox_list_calls = 0
        self._error = error
        self._candles = candles or []
        self.history_calls: list[dict[str, object]] = []
        self._trading_status = trading_status
        self._stream_events = stream_events or []
        self._stream_error = stream_error
        self.order_book: SimpleNamespace | None = None
        self.last_stream: _FakeStream | None = None

    def __enter__(self) -> _FakeClient:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def get_all_candles(self, **kwargs: object) -> list[SimpleNamespace]:
        self.history_calls.append(kwargs)
        if self._error is not None:
            raise self._error
        return self._candles

    @property
    def users(self) -> SimpleNamespace:
        def get_accounts() -> SimpleNamespace:
            if self._error is not None:
                raise self._error
            self.sandbox_users_list_calls += int(self._users_accounts is self._sandbox_accounts)
            return SimpleNamespace(accounts=[_fake_account(i) for i in self._users_accounts])

        return SimpleNamespace(get_accounts=get_accounts)

    @property
    def sandbox(self) -> SimpleNamespace:
        def get_sandbox_accounts() -> SimpleNamespace:
            self.deprecated_sandbox_list_calls += 1
            raise AssertionError("deprecated GetSandboxAccounts must not be called")

        def open_sandbox_account() -> SimpleNamespace:
            self.sandbox_open_calls += 1
            if self._error is not None:
                raise self._error
            return SimpleNamespace(account_id=self._sandbox_open_id)

        return SimpleNamespace(
            get_sandbox_accounts=get_sandbox_accounts,
            open_sandbox_account=open_sandbox_account,
        )

    @property
    def market_data(self) -> SimpleNamespace:
        def get_candles(**kwargs: object) -> SimpleNamespace:
            self.history_calls.append(kwargs)
            if self._error is not None:
                raise self._error
            return SimpleNamespace(candles=self._candles)

        def get_trading_status(**kwargs: object) -> SimpleNamespace:
            if self._error is not None:
                raise self._error
            return SimpleNamespace(trading_status=SimpleNamespace(name=self._trading_status))

        def get_order_book(**kwargs: object) -> SimpleNamespace:
            if self._error is not None:
                raise self._error
            assert self.order_book is not None
            return self.order_book

        return SimpleNamespace(
            get_candles=get_candles,
            get_trading_status=get_trading_status,
            get_order_book=get_order_book,
        )

    def create_market_data_stream(self) -> _FakeStream:
        self.last_stream = _FakeStream(list(self._stream_events), self._stream_error)
        return self.last_stream


def _factory(
    accounts: list[str] | None = None,
    error: Exception | None = None,
    candles: list[SimpleNamespace] | None = None,
    trading_status: str | None = None,
    stream_events: list[object] | None = None,
    stream_error: Exception | None = None,
    clients: list[_FakeClient] | None = None,
    sandbox_accounts: list[str] | None = None,
    sandbox_open_id: str = "sandbox-new",
) -> Callable[[str, str], _FakeClient]:
    def factory(token: str, target: str) -> _FakeClient:
        client = _FakeClient(
            accounts,
            error,
            candles,
            trading_status,
            stream_events,
            stream_error,
            sandbox_accounts,
            sandbox_open_id,
            use_sandbox_users=target == sandbox_target(),
        )
        if clients is not None:
            clients.append(client)
        return client

    return factory


def test_constructor_rejects_empty_token() -> None:
    with pytest.raises(ValueError):
        InvestGateway("", target="grpc://example")


def test_constructor_rejects_empty_target() -> None:
    with pytest.raises(ValueError):
        InvestGateway("token", target="")


def test_list_accounts_returns_confirmed_fields() -> None:
    gateway = InvestGateway(
        "t.token", target="grpc://sandbox", client_factory=_factory(["acc-1", "acc-2"])
    )

    accounts = gateway.list_accounts()

    assert accounts == [
        AccountInfo(id="acc-1", type="ACCOUNT_TYPE_TINKOFF", status="ACCOUNT_STATUS_OPEN"),
        AccountInfo(id="acc-2", type="ACCOUNT_TYPE_TINKOFF", status="ACCOUNT_STATUS_OPEN"),
    ]


def test_list_accounts_wraps_transport_error() -> None:
    gateway = InvestGateway(
        "t.token",
        target="grpc://sandbox",
        client_factory=_factory(error=_FakeRequestError("unauthenticated")),
    )

    with pytest.raises(GatewayError):
        gateway.list_accounts()


def test_list_sandbox_accounts_uses_supported_sandbox_users_service() -> None:
    clients: list[_FakeClient] = []
    gateway = InvestGateway(
        "t.token",
        target=sandbox_target(),
        client_factory=_factory(accounts=[], sandbox_accounts=["sandbox-1"], clients=clients),
    )

    assert gateway.list_sandbox_accounts() == [
        AccountInfo(id="sandbox-1", type="ACCOUNT_TYPE_TINKOFF", status="ACCOUNT_STATUS_OPEN")
    ]
    assert clients[0].sandbox_users_list_calls == 1
    assert clients[0].deprecated_sandbox_list_calls == 0


def test_list_sandbox_accounts_constructs_sdk_sandbox_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Реальный путь создания не должен подменяться обычным Client на sandbox target."""
    from tinkoff.invest.sandbox import client as sandbox_module

    created: list[str] = []

    def sandbox_client(token: str) -> _FakeClient:
        created.append(token)
        return _FakeClient([], None, sandbox_accounts=["sandbox-1"], use_sandbox_users=True)

    def regular_client() -> None:
        raise AssertionError("для списка sandbox нужен SandboxClient, не Client")

    monkeypatch.setattr(sandbox_module, "SandboxClient", sandbox_client)
    monkeypatch.setattr("trading_bot.api_client._import_client", regular_client)
    gateway = InvestGateway("test-token", target=sandbox_target())

    assert [account.id for account in gateway.list_sandbox_accounts()] == ["sandbox-1"]
    assert created == ["test-token"]


def test_open_sandbox_account_returns_id_and_opens_once() -> None:
    clients: list[_FakeClient] = []
    gateway = InvestGateway(
        "t.token", target=sandbox_target(), client_factory=_factory(clients=clients)
    )

    assert gateway.open_sandbox_account() == "sandbox-new"
    assert clients[0].sandbox_open_calls == 1


def test_open_sandbox_account_empty_id_is_not_reported_as_success() -> None:
    gateway = InvestGateway(
        "t.token", target=sandbox_target(), client_factory=_factory(sandbox_open_id="")
    )

    with pytest.raises(GatewayError, match="проверьте список"):
        gateway.open_sandbox_account()


def test_open_sandbox_account_transport_error_does_not_retry() -> None:
    clients: list[_FakeClient] = []
    gateway = InvestGateway(
        "t.token",
        target=sandbox_target(),
        client_factory=_factory(error=_FakeRequestError("timeout"), clients=clients),
    )

    with pytest.raises(GatewayError, match="результат неизвестен.*проверьте список"):
        gateway.open_sandbox_account()
    assert clients[0].sandbox_open_calls == 1


@pytest.mark.parametrize("method", ["list_sandbox_accounts", "open_sandbox_account"])
def test_sandbox_account_methods_refuse_production_target_without_client(method: str) -> None:
    clients: list[_FakeClient] = []
    gateway = InvestGateway(
        "t.token", target=production_target(), client_factory=_factory(clients=clients)
    )

    with pytest.raises(ValueError, match="sandbox"):
        getattr(gateway, method)()
    assert not clients


@pytest.mark.parametrize("method", ["list_sandbox_accounts", "open_sandbox_account"])
def test_sandbox_account_methods_wrap_sdk_errors(method: str) -> None:
    gateway = InvestGateway(
        "t.token",
        target=sandbox_target(),
        client_factory=_factory(error=_FakeRequestError("unavailable")),
    )

    with pytest.raises(GatewayError, match="sandbox"):
        getattr(gateway, method)()


def test_list_sandbox_accounts_logs_context_without_token_on_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = InvestGateway(
        "private-test-token",
        target=sandbox_target(),
        client_factory=_factory(error=_FakeRequestError("private-test-token unavailable")),
    )

    with pytest.raises(GatewayError, match="sandbox") as error:
        gateway.list_sandbox_accounts()

    assert "sandbox" in caplog.text
    assert "get_accounts" in caplog.text
    assert "private-test-token" not in caplog.text
    assert "private-test-token" not in str(error.value)


def test_check_token_true_on_success() -> None:
    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=_factory(["acc-1"]))

    assert gateway.check_token() is True


def test_check_token_false_on_gateway_error() -> None:
    gateway = InvestGateway(
        "t.token",
        target="grpc://sandbox",
        client_factory=_factory(error=_FakeRequestError("unauthenticated")),
    )

    assert gateway.check_token() is False


def test_list_accounts_propagates_sdk_unavailable_without_wrapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """В действительности SDK установлен в `.venv` этой сессии (см. requirements.txt),
    но код должен оставаться корректным и в окружении, где его нет. Имитируется
    отсутствие через `_import_client`, а не через реальное (удаление пакета, чтобы
    тест не зависел от того, установлен SDK в текущем окружении или нет.
    """

    def _raise_unavailable() -> tuple[type, type]:
        raise SdkUnavailableError("имитация отсутствия SDK в тесте")

    monkeypatch.setattr("trading_bot.api_client._import_client", _raise_unavailable)
    gateway = InvestGateway("t.token", target="grpc://real")

    with pytest.raises(SdkUnavailableError):
        gateway.list_accounts()


def test_get_candles_converts_quotation_to_decimal_and_tz_aware_time() -> None:
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    candle = _fake_historic_candle(price=250, volume=1000, ts=ts)
    gateway = InvestGateway(
        "t.token", target="grpc://sandbox", client_factory=_factory(candles=[candle])
    )

    candles = gateway.get_candles(instrument_id="BBG004730N88", timeframe="5m", from_=ts)

    assert candles == [
        Candle(
            open=Decimal(250),
            high=Decimal(250),
            low=Decimal(250),
            close=Decimal(250),
            volume=1000,
            ts_open=ts,
            is_complete=True,
        )
    ]


def test_get_candles_rejects_unsupported_timeframe() -> None:
    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=_factory())

    with pytest.raises(ValueError):
        gateway.get_candles(
            instrument_id="BBG004730N88",
            timeframe="3m",  # type: ignore[arg-type]
            from_=datetime.now(tz=UTC),
        )


def test_get_candles_rejects_naive_datetime() -> None:
    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=_factory())

    with pytest.raises(ValueError):
        gateway.get_candles(
            instrument_id="BBG004730N88",
            timeframe="5m",
            from_=datetime(2026, 1, 5, 10, 0),  # noqa: DTZ001
        )


def test_get_candles_wraps_transport_error() -> None:
    gateway = InvestGateway(
        "t.token",
        target="grpc://sandbox",
        client_factory=_factory(error=_FakeRequestError("unavailable")),
    )

    with pytest.raises(GatewayError):
        gateway.get_candles(
            instrument_id="BBG004730N88", timeframe="5m", from_=datetime.now(tz=UTC)
        )


def test_get_candles_history_pages_actual_rpc_and_preserves_complete_flag() -> None:
    start = datetime(2026, 1, 5, 10, tzinfo=UTC)
    end = datetime(2026, 1, 10, 10, tzinfo=UTC)
    clients: list[_FakeClient] = []
    gateway = InvestGateway(
        "t.token",
        target="grpc://sandbox",
        client_factory=_factory(candles=[_fake_historic_candle(100, 20, start)], clients=clients),
    )

    candles = gateway.get_candles_history(
        instrument_id="BBG004730N88", timeframe="5m", from_=start, to=end
    )

    assert candles[0].volume == 20
    assert candles[0].is_complete is True
    assert clients[0].history_calls[0]["instrument_id"] == "BBG004730N88"
    assert clients[0].history_calls[0]["from_"] == start
    assert clients[0].history_calls[0]["to"] == datetime(2026, 1, 6, 10, tzinfo=UTC)
    assert clients[-1].history_calls[0]["to"] == end
    assert len(clients) == 5


def test_get_candles_history_rejects_unbounded_or_naive_dates() -> None:
    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=_factory())
    start = datetime(2026, 1, 5, 10, tzinfo=UTC)
    with pytest.raises(ValueError):
        gateway.get_candles_history(instrument_id="SBER", timeframe="5m", from_=start, to=start)
    with pytest.raises(ValueError):
        gateway.get_candles_history(
            instrument_id="SBER",
            timeframe="5m",
            from_=start,
            to=datetime(2026, 1, 6, 10),  # noqa: DTZ001
        )


def test_get_trading_status_returns_enum_name() -> None:
    gateway = InvestGateway(
        "t.token",
        target="grpc://sandbox",
        client_factory=_factory(trading_status="SECURITY_TRADING_STATUS_NORMAL_TRADING"),
    )

    status = gateway.get_trading_status(instrument_id="BBG004730N88")

    assert status == "SECURITY_TRADING_STATUS_NORMAL_TRADING"


def test_get_trading_status_wraps_transport_error() -> None:
    gateway = InvestGateway(
        "t.token",
        target="grpc://sandbox",
        client_factory=_factory(error=_FakeRequestError("unavailable")),
    )

    with pytest.raises(GatewayError):
        gateway.get_trading_status(instrument_id="BBG004730N88")


def test_stream_candles_yields_candles_and_skips_non_candle_events() -> None:
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    events: list[object] = [
        SimpleNamespace(candle=None),  # ping/инфо-событие, не свеча
        SimpleNamespace(candle=_fake_stream_candle(price=100, volume=5, ts=ts)),
    ]
    clients: list[_FakeClient] = []
    gateway = InvestGateway(
        "t.token",
        target="grpc://sandbox",
        client_factory=_factory(stream_events=events, clients=clients),
    )

    received = list(gateway.stream_candles(instrument_ids=["BBG004730N88"], timeframe="5m"))

    assert received == [
        Candle(
            open=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            volume=5,
            ts_open=ts,
            is_complete=None,
        )
    ]
    assert clients[0].last_stream is not None
    assert clients[0].last_stream.stopped is True


def test_stream_candles_rejects_empty_instrument_list() -> None:
    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=_factory())

    with pytest.raises(ValueError):
        list(gateway.stream_candles(instrument_ids=[], timeframe="5m"))


def test_stream_candles_rejects_unsupported_timeframe() -> None:
    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=_factory())

    with pytest.raises(ValueError):
        list(
            gateway.stream_candles(
                instrument_ids=["BBG004730N88"], timeframe="3m"  # type: ignore[arg-type]
            )
        )


def test_stream_candles_wraps_transport_error_mid_stream() -> None:
    ts = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    events: list[object] = [SimpleNamespace(candle=_fake_stream_candle(100, 5, ts))]
    gateway = InvestGateway(
        "t.token",
        target="grpc://sandbox",
        client_factory=_factory(
            stream_events=events, stream_error=_FakeRequestError("connection lost")
        ),
    )

    with pytest.raises(GatewayError):
        list(gateway.stream_candles(instrument_ids=["BBG004730N88"], timeframe="5m"))


def test_get_share_metadata_uses_confirmed_share_fields() -> None:
    share = SimpleNamespace(
        ticker="SBER",
        figi="BBG004730N88",
        lot=10,
        min_price_increment=_fake_quotation(0, 100_000_000),
        currency="rub",
        sector="financial",
        short_enabled_flag=True,
        trading_status=SimpleNamespace(name="SECURITY_TRADING_STATUS_NORMAL_TRADING"),
    )
    client = _FakeClient([], None)
    client.instruments = SimpleNamespace(
        shares=lambda: SimpleNamespace(instruments=[share, SimpleNamespace(ticker="VTBR")])
    )
    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=lambda *_: client)

    instrument = gateway.get_share_metadata(ticker="SBER")

    assert instrument.ticker == "SBER"
    assert instrument.instrument_id == "BBG004730N88"
    assert instrument.lot_size == 10
    assert instrument.min_price_increment == Decimal("0.1")
    # The domain/risk contract uses ISO uppercase, not the SDK's lowercase spelling.
    assert instrument.currency == "RUB"
    assert instrument.sector == "financial"
    assert instrument.short_enabled is True
    assert instrument.trading_status == NORMAL_TRADING_STATUS


def test_get_share_metadata_rejects_missing_and_ambiguous_tickers() -> None:
    for shares in ([], [SimpleNamespace(ticker="SBER"), SimpleNamespace(ticker="SBER")]):
        client = _FakeClient([], None)
        client.instruments = SimpleNamespace(
            shares=lambda items=shares: SimpleNamespace(instruments=items)
        )
        gateway = InvestGateway(
            "t.token", target="grpc://sandbox", client_factory=lambda *_, instance=client: instance
        )
        with pytest.raises(GatewayError, match="не найден|несколько"):
            gateway.get_share_metadata(ticker="SBER")


def test_get_order_book_quote_selects_best_bid_and_ask_without_order_assumption() -> None:
    client = _FakeClient([], None)
    client.order_book = SimpleNamespace(
        bids=[
            SimpleNamespace(price=_fake_quotation(99), quantity=5),
            SimpleNamespace(price=_fake_quotation(100), quantity=2),
        ],
        asks=[
            SimpleNamespace(price=_fake_quotation(102), quantity=3),
            SimpleNamespace(price=_fake_quotation(101), quantity=4),
        ],
        orderbook_ts=datetime(2026, 1, 5, 10, 0, tzinfo=UTC),
    )
    gateway = InvestGateway("t.token", target="grpc://sandbox", client_factory=lambda *_: client)

    quote = gateway.get_order_book_quote(instrument_id="BBG004730N88")

    assert quote.best_bid == Decimal(100)
    assert quote.best_ask == Decimal(101)
    assert quote.observed_at == datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def test_get_order_book_quote_rejects_crossed_or_empty_book() -> None:
    for bids, asks in (([], []), ([(101, 1)], [(100, 1)])):
        client = _FakeClient([], None)
        client.order_book = SimpleNamespace(
            bids=[SimpleNamespace(price=_fake_quotation(p), quantity=q) for p, q in bids],
            asks=[SimpleNamespace(price=_fake_quotation(p), quantity=q) for p, q in asks],
            orderbook_ts=datetime(2026, 1, 5, 10, 0, tzinfo=UTC),
        )
        gateway = InvestGateway(
            "t.token", target="grpc://sandbox", client_factory=lambda *_, instance=client: instance
        )
        with pytest.raises(GatewayError, match="стакан"):
            gateway.get_order_book_quote(instrument_id="BBG004730N88")
