"""Тесты paper.py: PaperBroker без gRPC, симуляция исполнения (docs/00_PLAN.md §8)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trading_bot.broker.paper import (
    PaperBroker,
    should_cancel_remainder,
)


def _broker(fee_rate: str = "0.003", slippage_assumed: str = "0.0005") -> PaperBroker:
    return PaperBroker(fee_rate=Decimal(fee_rate), slippage_assumed=Decimal(slippage_assumed))


# --- Архитектурная гарантия: нет gRPC/InvestGateway --------------------------


def test_paper_broker_constructor_has_no_gateway_or_token_parameter() -> None:
    """docs/13_SECURITY.md, п.1: PaperBroker физически не может отправить заявку —
    у него негде хранить токен/клиент, потому что конструктор их не принимает.
    """
    params = set(inspect.signature(PaperBroker.__init__).parameters)
    assert params == {"self", "fee_rate", "slippage_assumed"}


def test_paper_broker_instance_has_no_gateway_attribute() -> None:
    broker = _broker()
    attribute_names = {name for name in vars(broker)}
    forbidden_fragments = ("gateway", "client", "token", "grpc", "channel")
    for name in attribute_names:
        lowered = name.lower()
        assert not any(fragment in lowered for fragment in forbidden_fragments), name


def test_paper_module_does_not_import_tinkoff() -> None:
    import trading_bot.broker.paper as paper_module

    source = inspect.getsource(paper_module)
    assert "tinkoff" not in source.lower()


# --- fill_market_order ---------------------------------------------------------


def test_fill_market_order_long_pays_slippage_above_reference() -> None:
    broker = _broker(fee_rate="0.003", slippage_assumed="0.0005")
    fill = broker.fill_market_order(
        side="long", requested_lots=10, lot_size=10, reference_price=Decimal("100")
    )
    # price = 100 * 1.0005 = 100.05
    assert fill.price == Decimal("100.05")
    assert fill.filled_lots == 10
    assert fill.remaining_lots == 0
    # notional = 100.05 * 100 = 10005; fee = 10005*0.003 = 30.015
    assert fill.fee == Decimal("30.015")
    # slippage_cost = |100.05-100| * 100 = 5.0
    assert fill.slippage_cost == Decimal("5.0")


def test_fill_market_order_short_sells_below_reference() -> None:
    broker = _broker(fee_rate="0.003", slippage_assumed="0.0005")
    fill = broker.fill_market_order(
        side="short", requested_lots=10, lot_size=10, reference_price=Decimal("100")
    )
    # price = 100 * 0.9995 = 99.95
    assert fill.price == Decimal("99.95")
    assert fill.slippage_cost == Decimal("5.0")


def test_fill_market_order_partial_fill_when_liquidity_insufficient() -> None:
    broker = _broker()
    fill = broker.fill_market_order(
        side="long",
        requested_lots=100,
        lot_size=1,
        reference_price=Decimal("50"),
        available_lots=30,
    )
    assert fill.filled_lots == 30
    assert fill.remaining_lots == 70


def test_fill_market_order_available_lots_greater_than_requested_fills_all() -> None:
    broker = _broker()
    fill = broker.fill_market_order(
        side="long",
        requested_lots=10,
        lot_size=1,
        reference_price=Decimal("50"),
        available_lots=1000,
    )
    assert fill.filled_lots == 10
    assert fill.remaining_lots == 0


def test_fill_market_order_zero_available_lots_gives_zero_fill() -> None:
    broker = _broker()
    fill = broker.fill_market_order(
        side="long",
        requested_lots=10,
        lot_size=1,
        reference_price=Decimal("50"),
        available_lots=0,
    )
    assert fill.filled_lots == 0
    assert fill.remaining_lots == 10
    assert fill.fee == Decimal("0")


def test_fill_market_order_rejects_non_positive_requested_lots() -> None:
    broker = _broker()
    with pytest.raises(ValueError):
        broker.fill_market_order(
            side="long", requested_lots=0, lot_size=1, reference_price=Decimal("50")
        )


def test_paper_broker_rejects_negative_fee_rate() -> None:
    with pytest.raises(ValueError):
        PaperBroker(fee_rate=Decimal("-0.01"), slippage_assumed=Decimal("0.0005"))


def test_paper_broker_rejects_negative_slippage() -> None:
    with pytest.raises(ValueError):
        PaperBroker(fee_rate=Decimal("0.003"), slippage_assumed=Decimal("-0.01"))


# --- should_cancel_remainder (60с) ----------------------------------------------


def test_should_cancel_remainder_false_before_60_seconds() -> None:
    placed_at = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    now = placed_at + timedelta(seconds=59)
    assert should_cancel_remainder(placed_at=placed_at, now=now) is False


def test_should_cancel_remainder_true_at_exactly_60_seconds() -> None:
    placed_at = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    now = placed_at + timedelta(seconds=60)
    assert should_cancel_remainder(placed_at=placed_at, now=now) is True


def test_should_cancel_remainder_true_well_after_60_seconds() -> None:
    placed_at = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    now = placed_at + timedelta(seconds=120)
    assert should_cancel_remainder(placed_at=placed_at, now=now) is True


def test_should_cancel_remainder_respects_custom_timeout() -> None:
    placed_at = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    now = placed_at + timedelta(seconds=10)
    assert should_cancel_remainder(placed_at=placed_at, now=now, timeout_sec=5) is True
    assert should_cancel_remainder(placed_at=placed_at, now=now, timeout_sec=20) is False
