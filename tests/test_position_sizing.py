"""Независимые сценарии новой формулы M5 и проверка через реальные paper/SQLite."""

from __future__ import annotations

import sqlite3
from decimal import Decimal

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from tests.test_risk_manager import _account, _config, _instrument
from trading_bot import database
from trading_bot.broker.paper import PaperBroker
from trading_bot.portfolio import Portfolio
from trading_bot.risk_manager import OpenPositionState, calculate_position_size, check


@pytest.mark.parametrize(
    ("case", "account_args", "instrument_args", "config_args", "side", "expected", "binding"),
    [
        (
            "capital_cap",
            {},
            {
                "price": Decimal("250"),
                "atr": Decimal("1"),
                "stop_price": Decimal("248.5"),
                "lot_size": 10,
            },
            {},
            "long",
            6,
            "cap",
        ),
        (
            "risk",
            {},
            {
                "price": Decimal("100"),
                "atr": Decimal("5"),
                "stop_price": Decimal("90"),
                "lot_size": 1,
            },
            {"max_position_pct": Decimal("1")},
            "long",
            93,
            "risk",
        ),
        (
            "funds",
            {
                "balance_free": Decimal("10000"),
                "daily_start_balance": Decimal("10000"),
                "starting_equity": Decimal("10000"),
            },
            {
                "price": Decimal("100"),
                "atr": Decimal("0.5"),
                "stop_price": Decimal("99.9"),
                "lot_size": 1,
            },
            {"max_position_pct": Decimal("1"), "risk_per_trade": Decimal("0.02")},
            "long",
            49,
            "funds",
        ),
        (
            "liquidity",
            {},
            {
                "price": Decimal("50"),
                "atr": Decimal("0.5"),
                "stop_price": Decimal("49"),
                "lot_size": 2,
                "average_volume_lots": Decimal("80"),
            },
            {"max_position_pct": Decimal("1")},
            "long",
            4,
            "liquidity",
        ),
        (
            "daily_headroom",
            {"balance_free": Decimal("97100"), "daily_pnl": Decimal("-2900")},
            {
                "price": Decimal("100"),
                "atr": Decimal("5"),
                "stop_price": Decimal("110"),
                "lot_size": 1,
            },
            {"max_position_pct": Decimal("1")},
            "short",
            9,
            "daily_headroom",
        ),
    ],
)
def test_five_golden_sizing_constraints(
    case: str,
    account_args: dict[str, object],
    instrument_args: dict[str, object],
    config_args: dict[str, object],
    side: str,
    expected: int,
    binding: str,
) -> None:
    """Пять вручную рассчитанных ограничений; стоп для риска НЕ выводится из ATR."""
    account = _account(**account_args)
    instrument = _instrument(**instrument_args)
    config = _config(**config_args)
    verdict = check(side=side, account=account, instrument=instrument, config=config)  # type: ignore[arg-type]
    assert verdict.allowed, case
    assert verdict.max_lots == expected, case
    assert (
        calculate_position_size(side=side, account=account, instrument=instrument, config=config)
        == expected
    )  # type: ignore[arg-type]
    assert f"связывающий потолок: {binding}" in verdict.reason


def test_multiple_fifo_parts_same_ticker_are_valued_without_counting_extra_positions() -> None:
    first = OpenPositionState(
        ticker="OTHER",
        side="long",
        lots=30,
        lot_size=1,
        stop_price=Decimal("248.5"),
        market_price=Decimal("250"),
    )
    account = _account(
        balance_free=Decimal("85000"),
        open_positions=(first, first),
        open_positions_count=1,
        open_tickers=frozenset({"OTHER"}),
    )
    verdict = check(
        side="long",
        account=account,
        instrument=_instrument(price=Decimal("100"), stop_price=Decimal("90"), lot_size=1),
        config=_config(max_position_pct=Decimal("1")),
    )
    assert verdict.allowed
    assert verdict.max_lots > 0


def test_long_open_position_uses_equity_at_stops_not_remaining_cash() -> None:
    held = OpenPositionState(
        ticker="OTHER",
        side="long",
        lots=60,
        lot_size=1,
        stop_price=Decimal("248.5"),
        market_price=Decimal("250"),
    )
    account = _account(
        balance_free=Decimal("85000"),
        open_positions=(held,),
        open_positions_count=1,
        open_tickers=frozenset({"OTHER"}),
    )
    instrument = _instrument(price=Decimal("100"), stop_price=Decimal("90"), lot_size=1)
    verdict = check(
        side="long",
        account=account,
        instrument=instrument,
        config=_config(max_position_pct=Decimal("1")),
    )
    assert verdict.allowed
    # Старый бюджет: 1% от 85000 = 850; новый допустимый убыток выше 850,
    # но не выше 1% капитала B и одновременно укладывается в дневной запас.
    loss_per_lot = Decimal("100") * Decimal("1.0005") * Decimal("1.003") - Decimal("90") * Decimal(
        "0.9995"
    ) * Decimal("0.997")
    assert verdict.max_lots * loss_per_lot > Decimal("850")
    assert verdict.effective_risk_pct < Decimal("1")


def test_short_proceeds_do_not_inflate_capital_or_risk_budget() -> None:
    held = OpenPositionState(
        ticker="OTHER",
        side="short",
        lots=60,
        lot_size=1,
        stop_price=Decimal("251.5"),
        market_price=Decimal("250"),
    )
    account = _account(
        balance_free=Decimal("115000"),
        open_positions=(held,),
        open_positions_count=1,
        open_tickers=frozenset({"OTHER"}),
    )
    instrument = _instrument(price=Decimal("100"), stop_price=Decimal("90"), lot_size=1)
    verdict = check(
        side="long",
        account=account,
        instrument=instrument,
        config=_config(max_position_pct=Decimal("1")),
    )
    assert verdict.allowed
    assert verdict.max_lots < 100  # прежний cash-бюджет 1150 позволял бы >100 лотов
    assert verdict.effective_risk_pct < Decimal("1")


@pytest.mark.parametrize(
    ("account_args", "instrument_args", "failed_reason"),
    [
        (
            {
                "open_positions": (
                    OpenPositionState("OTHER", "long", 1, 10, None, Decimal("100")),
                ),
                "open_positions_count": 1,
                "open_tickers": frozenset({"OTHER"}),
            },
            {},
            "без пригодного стопа",
        ),
        (
            {
                "balance_free": Decimal("90000"),
                "daily_pnl": Decimal("-2900"),
                "open_positions": (
                    OpenPositionState("OTHER", "long", 100, 1, Decimal("70"), Decimal("100")),
                ),
                "open_positions_count": 1,
                "open_tickers": frozenset({"OTHER"}),
            },
            {},
            "daily_headroom",
        ),
        (
            {
                "balance_free": Decimal("84900"),
                "daily_start_balance": Decimal("85000"),
                "starting_equity": Decimal("100000"),
            },
            {},
            "drawdown",
        ),
        ({}, {"currency": "USD"}, "не RUB"),
        ({}, {"average_volume_lots": None}, "5m-объёма"),
        ({}, {"stop_price": None}, "стопа"),
    ],
)
def test_invalid_or_exhausted_inputs_fail_closed(
    account_args: dict[str, object], instrument_args: dict[str, object], failed_reason: str
) -> None:
    verdict = check(
        side="long",
        account=_account(**account_args),
        instrument=_instrument(**instrument_args),
        config=_config(),
    )
    assert not verdict.allowed and verdict.max_lots == 0
    assert failed_reason in verdict.checks[-1].reason


def test_invalid_numeric_risk_inputs_fail_closed_without_exception() -> None:
    verdict = check(
        side="long",
        account=_account(),
        instrument=_instrument(),
        config=_config(risk_per_trade=Decimal("NaN")),
    )
    assert not verdict.allowed
    assert "некорректные параметры риска" in verdict.checks[-1].reason


@settings(max_examples=1000, deadline=None)
@given(
    price=st.decimals(min_value="50", max_value="500", places=2),
    distance=st.decimals(min_value="0.1", max_value="4", places=2),
    lot_size=st.integers(min_value=1, max_value=20),
    side=st.sampled_from(["long", "short"]),
    regime=st.sampled_from(["trending", "volatile"]),
)
def test_property_stop_loss_in_paper_journal_never_exceeds_budget(
    price: Decimal, distance: Decimal, lot_size: int, side: str, regime: str
) -> None:
    """Реальные paper-заполнения и FIFO P&L, не повторение формулы ℓ в тесте.

    `Portfolio` получает опорную цену + отдельную сумму проскальзывания:
    передавать одновременно уже сдвинутую Fill.price и slippage_cost нельзя.
    """
    config = _config()
    account = _account()
    stop = price - distance if side == "long" else price + distance
    instrument = _instrument(
        price=price,
        stop_price=stop,
        atr=Decimal("0.1"),
        lot_size=lot_size,
        regime=regime,
    )
    verdict = check(side=side, account=account, instrument=instrument, config=config)  # type: ignore[arg-type]
    assume(verdict.allowed)  # законный ноль после ROUND_DOWN не является нарушением инварианта
    lots = verdict.max_lots
    assert lots >= 1
    broker = PaperBroker(fee_rate=config.fee_rate, slippage_assumed=config.slippage_assumed)
    entry_side = "long" if side == "long" else "short"
    exit_side = "short" if side == "long" else "long"
    entry = broker.fill_market_order(
        side=entry_side, requested_lots=lots, lot_size=lot_size, reference_price=price
    )
    exit_fill = broker.fill_market_order(
        side=exit_side, requested_lots=lots, lot_size=lot_size, reference_price=stop
    )
    conn = sqlite3.connect(":memory:")
    database.apply_migrations(conn)
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    portfolio.open_position(
        ticker="SBER",
        side=side,
        lots=lots,
        lot_size=lot_size,
        price=price,
        fee=entry.fee,
        slippage=entry.slippage_cost,
        stop=stop,
    )  # type: ignore[arg-type]
    closed = portfolio.close_position(
        ticker="SBER",
        lots=lots,
        exit_price=stop,
        fee=exit_fill.fee,
        slippage=exit_fill.slippage_cost,
        exit_reason="stop",
    )
    realized_loss = -sum((trade.pnl_net for trade in closed), Decimal(0))
    assert realized_loss <= Decimal("100000") * config.risk_per_trade
    assert verdict.effective_risk_pct == realized_loss / Decimal("100000") * 100
    assert Decimal(lots * lot_size) * price <= config.max_position_pct * Decimal("100000")
    assert (
        Decimal(lots * lot_size) * price * (1 + config.slippage_assumed) * (1 + config.fee_rate)
        <= Decimal("100000") - config.min_free_balance
    )
    assert Decimal(lots) <= config.max_volume_participation * instrument.average_volume_lots
    conn.close()
