"""Automatic paper discovery contract, exercised without network or secrets."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from trading_bot.api_client import GatewayError, InvestGateway
from trading_bot.config import Settings


def _share(ticker: str, **changes: object) -> SimpleNamespace:
    values: dict[str, object] = dict(
        ticker=ticker,
        figi=f"id-{ticker}",
        lot=10,
        min_price_increment=SimpleNamespace(units=0, nano=10000000),
        currency="rub",
        sector="financial",
        short_enabled_flag=True,
        trading_status=SimpleNamespace(name="SECURITY_TRADING_STATUS_NORMAL_TRADING"),
        real_exchange=SimpleNamespace(name="REAL_EXCHANGE_MOEX"),
        otc_flag=False,
        api_trade_available_flag=True,
        buy_available_flag=True,
        sell_available_flag=True,
        for_qual_investor_flag=False,
        share_type=SimpleNamespace(name="SHARE_TYPE_COMMON"),
    )
    values.update(changes)
    return SimpleNamespace(**values)


def _index(**changes: object) -> SimpleNamespace:
    values: dict[str, object] = dict(
        ticker="IMOEX",
        uid="index-uid",
        figi="",
        instrument_type="index",
        instrument_kind=SimpleNamespace(name="INSTRUMENT_TYPE_INDEX"),
    )
    values.update(changes)
    return SimpleNamespace(**values)


def _gateway(shares: list[SimpleNamespace], indices: list[SimpleNamespace]) -> InvestGateway:
    client = MagicMock()
    client.__enter__.return_value = client
    client.instruments.shares.return_value.instruments = shares
    client.instruments.find_instrument.return_value.instruments = indices
    return InvestGateway("synthetic", target="sandbox", client_factory=lambda _t, _h: client)


@pytest.mark.parametrize("legacy", ["", "SBER,SBER,INVALID"])
def test_balance_alone_enables_paper_source_and_legacy_is_not_authority(legacy: str) -> None:
    settings = Settings(
        paper_starting_balance=Decimal("100000"),
        paper_candidate_tickers=legacy,
        paper_approved_tickers=legacy,
        paper_index_id="ETF-is-not-an-index",
        _env_file=None,
    )
    assert settings.paper_source_enabled
    assert not settings.model_copy(update={"paper_starting_balance": None}).paper_source_enabled
    assert not settings.model_copy(update={"trading_mode": "live"}).paper_source_enabled


def test_catalog_filters_and_excludes_ambiguous_tickers_before_lookup() -> None:
    gateway = _gateway(
        [
            _share("GOOD"),
            _share("DUP"),
            _share("DUP", figi="other"),
            _share("USD", currency="usd"),
            _share("OTC", otc_flag=True),
            _share("FOREIGN", real_exchange=SimpleNamespace(name="REAL_EXCHANGE_RTS")),
            _share("NOAPI", api_trade_available_flag=False),
            _share("QUAL", for_qual_investor_flag=True),
            _share("NOSECTOR", sector=""),
            _share("NOLOT", lot=0),
        ],
        [],
    )
    catalog = gateway.discover_paper_shares()
    assert tuple(catalog.shares) == ("GOOD",)
    assert catalog.shares["GOOD"].currency == "RUB"
    assert "ambiguous" in catalog.excluded["DUP"]
    assert len(catalog.excluded) == 8
    assert gateway.get_share_metadata(ticker="GOOD") == catalog.shares["GOOD"]
    with pytest.raises(GatewayError):
        gateway.get_share_metadata(ticker="DUP")


def test_real_imoex_discovery_ignores_etf_futures_and_similar_tickers() -> None:
    gateway = _gateway(
        [],
        [
            _index(instrument_kind=SimpleNamespace(name="INSTRUMENT_TYPE_ETF")),
            _index(ticker="IMOEX2"),
            _index(),
        ],
    )
    assert gateway.resolve_imoex_id() == "index-uid"


@pytest.mark.parametrize(
    "indices", [[], [_index(), _index(uid="other")], [_index(uid="", figi="")]]
)
def test_missing_ambiguous_or_unidentified_index_fails_closed(
    indices: list[SimpleNamespace],
) -> None:
    with pytest.raises(GatewayError):
        _gateway([], indices).resolve_imoex_id()


def test_discovery_excludes_depositary_receipts() -> None:
    catalog = _gateway(
        [
            _share("ADR", share_type=SimpleNamespace(name="SHARE_TYPE_ADR")),
            _share("PREF", share_type=SimpleNamespace(name="SHARE_TYPE_PREFERRED")),
        ],
        [],
    ).discover_paper_shares()
    assert tuple(catalog.shares) == ("PREF",)
    assert catalog.excluded["ADR"] == "not_common_or_preferred_share"


def test_duplicate_search_rows_with_same_uid_are_one_index() -> None:
    assert _gateway([], [_index(), _index()]).resolve_imoex_id() == "index-uid"


def test_index_enum_is_authority_when_legacy_instrument_type_is_empty() -> None:
    """The verified INDEX enum must not depend on an undocumented string value."""
    assert _gateway([], [_index(instrument_type="")]).resolve_imoex_id() == "index-uid"


def test_discovery_failure_does_not_log_raw_sdk_error(caplog: pytest.LogCaptureFixture) -> None:
    gateway = _gateway([], [])
    client = gateway._make_client()
    client.instruments.shares.side_effect = RuntimeError("sensitive-transport-text")
    with pytest.raises(GatewayError):
        gateway.discover_paper_shares()
    assert "stage=share_catalog" in caplog.text
    assert "sensitive-transport-text" not in caplog.text
