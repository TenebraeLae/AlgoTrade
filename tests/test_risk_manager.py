"""Тесты risk_manager.py (docs/04_RISK.md): размер позиции, 12 проверок, стопы."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from trading_bot.api_client import NORMAL_TRADING_STATUS
from trading_bot.risk_manager import (
    CHECK_NAMES,
    AccountState,
    InstrumentState,
    OpenPositionState,
    RiskConfig,
    breakeven_stop,
    calculate_position_size,
    can_move_stop,
    check,
    initial_stop,
    initial_take,
    trailing_stop,
)

#: docs/11_TESTING.md/docs/04_RISK.md требуют 1000 случайных комбинаций для property-тестов;
#: `deadline=None`, так как `check()` на Decimal может быть медленнее встроенного
#: таймаута hypothesis на медленных CI-раннерах.
_PROPERTY_SETTINGS = settings(max_examples=1000, deadline=None)


def _config(**overrides: object) -> RiskConfig:
    base = dict(
        risk_per_trade=Decimal("0.01"),
        atr_stop_mult=Decimal("1.5"),
        atr_take_mult=Decimal("2.0"),
        atr_trail_trigger=Decimal("1.0"),
        atr_trail_dist=Decimal("1.0"),
        daily_loss_limit=Decimal("0.03"),
        global_drawdown_stop=Decimal("0.15"),
        max_position_pct=Decimal("0.15"),
        max_volume_participation=Decimal("0.05"),
        max_trades_per_day=30,
        min_free_balance=Decimal("5000"),
        max_open_positions=7,
        max_per_sector=2,
        max_correlation=Decimal("0.85"),
        max_atr_ratio=Decimal("0.05"),
        max_spread_bps=Decimal("30"),
        volatile_size_scaler=Decimal("0.5"),
        slippage_assumed=Decimal("0.0005"),
        fee_rate=Decimal("0.003"),
    )
    base.update(overrides)
    return RiskConfig(**base)  # type: ignore[arg-type]


def _account(**overrides: object) -> AccountState:
    base = dict(
        trading_mode="paper",
        balance_free=Decimal("100000"),
        trades_today=0,
        daily_pnl=Decimal("0"),
        daily_start_balance=Decimal("100000"),
        starting_equity=Decimal("100000"),
        open_positions=(),
        open_positions_count=0,
        open_positions_by_sector={},
        open_tickers=frozenset(),
        trading_day_confirmed=True,
        daily_stop_active=False,
    )
    base.update(overrides)
    return AccountState(**base)  # type: ignore[arg-type]


def _instrument(**overrides: object) -> InstrumentState:
    base = dict(
        ticker="SBER",
        sector="financials",
        price=Decimal("250"),
        atr=Decimal("5"),
        lot_size=10,
        spread_bps=Decimal("10"),
        trading_status=NORMAL_TRADING_STATUS,
        regime="trending",
        is_session_open=True,
        session_reason="в торговом окне",
        max_correlation_with_open=Decimal("0.1"),
        average_volume_lots=Decimal("10000"),
        min_price_increment=Decimal("0.01"),
        currency="RUB",
    )
    base.update(overrides)
    if "stop_price" not in overrides:
        base["stop_price"] = base["price"] - base["atr"] * Decimal("1.5")
    return InstrumentState(**base)  # type: ignore[arg-type]


def test_position_size_is_capped_by_capital_with_costs_from_real_stop() -> None:
    account = _account()
    instrument = _instrument(price=Decimal("250"), atr=Decimal("1"), stop_price=Decimal("248.5"))
    config = _config()
    assert (
        calculate_position_size(side="long", account=account, instrument=instrument, config=config)
        == 6
    )
    verdict = check(side="long", account=account, instrument=instrument, config=config)
    assert verdict.allowed and verdict.max_lots == 6


# Golden-пятёрка и независимое свойство 1000 paper-сделок — tests/test_position_sizing.py.


# --- Свойство: нулевой свободный баланс -> вердикт всегда отказ (hypothesis) ----


@_PROPERTY_SETTINGS
@given(
    trades_today=st.integers(min_value=0, max_value=29),
    open_positions_count=st.integers(min_value=0, max_value=6),
    atr=st.decimals(min_value="0.1", max_value="20", places=2),
    price=st.decimals(min_value="1", max_value="1000", places=2),
)
def test_property_zero_free_balance_is_always_denied(
    trades_today: int, open_positions_count: int, atr: Decimal, price: Decimal
) -> None:
    account = _account(
        balance_free=Decimal("0"),
        trades_today=trades_today,
        open_positions_count=open_positions_count,
    )
    instrument = _instrument(atr=atr, price=price)
    verdict = check(side="long", account=account, instrument=instrument, config=_config())
    assert verdict.allowed is False


# --- chaos: 0 новых позиций за 1000 случайных примеров (hypothesis) ------------------


@_PROPERTY_SETTINGS
@given(
    balance_free=st.decimals(min_value="10000", max_value="1000000", places=2),
    atr=st.decimals(min_value="0.1", max_value="100", places=2),
    price=st.decimals(min_value="1", max_value="5000", places=2),
)
def test_property_chaos_regime_never_allows_new_positions(
    balance_free: Decimal, atr: Decimal, price: Decimal
) -> None:
    account = _account(balance_free=balance_free)
    instrument = _instrument(regime="chaos", atr=atr, price=price)
    verdict = check(side="long", account=account, instrument=instrument, config=_config())
    assert verdict.allowed is False


# --- Полный проход всех 12 проверок ---------------------------------------------


def test_check_all_pass_gives_allowed_verdict_with_positive_lots() -> None:
    verdict = check(side="long", account=_account(), instrument=_instrument(), config=_config())
    assert verdict.allowed is True
    assert verdict.max_lots > 0
    assert len(verdict.checks) == 12
    assert all(c.passed for c in verdict.checks)
    assert [c.name for c in verdict.checks] == list(CHECK_NAMES)


# --- Изолированный тест отказа на каждую из 12 проверок ------------------------


def test_check_1_rejects_invalid_trading_mode() -> None:
    verdict = check(
        side="long",
        account=_account(trading_mode="sandbox"),
        instrument=_instrument(),
        config=_config(),
    )
    failed = {c.name: c for c in verdict.checks}["trading_mode_valid"]
    assert failed.passed is False


def test_check_2_rejects_when_session_closed() -> None:
    verdict = check(
        side="long",
        account=_account(),
        instrument=_instrument(is_session_open=False, session_reason="вне торгового окна"),
        config=_config(),
    )
    failed = {c.name: c for c in verdict.checks}["session_open"]
    assert failed.passed is False
    assert "вне торгового окна" in failed.reason


def test_check_3_rejects_when_instrument_not_normal_trading() -> None:
    verdict = check(
        side="long",
        account=_account(),
        instrument=_instrument(trading_status="SECURITY_TRADING_STATUS_BREAK_IN_TRADING"),
        config=_config(),
    )
    failed = {c.name: c for c in verdict.checks}["instrument_status_normal"]
    assert failed.passed is False


def test_check_4_rejects_when_spread_too_wide() -> None:
    verdict = check(
        side="long",
        account=_account(),
        instrument=_instrument(spread_bps=Decimal("50")),
        config=_config(max_spread_bps=Decimal("30")),
    )
    failed = {c.name: c for c in verdict.checks}["spread_within_limit"]
    assert failed.passed is False


def test_check_5_daily_loss_limit_uses_stop_capital_not_marked_pnl() -> None:
    config = _config(daily_loss_limit=Decimal("0.03"))
    # Отмеченный P&L может восстановиться, но капитал после стопов ровно на пороге.
    at_threshold = _account(balance_free=Decimal("97000"), daily_pnl=Decimal("100"))
    verdict_at = check(side="long", account=at_threshold, instrument=_instrument(), config=config)
    assert {c.name: c for c in verdict_at.checks}["daily_loss_limit_not_reached"].passed is False

    # Отмеченный P&L может быть плохим, но B ещё не достиг порога.
    below_threshold = _account(balance_free=Decimal("97010"), daily_pnl=Decimal("-4000"))
    verdict_below = check(
        side="long", account=below_threshold, instrument=_instrument(), config=config
    )
    assert {c.name: c for c in verdict_below.checks}["daily_loss_limit_not_reached"].passed is True


def test_check_5_uses_worse_stop_even_if_mark_to_market_is_profitable() -> None:
    held = OpenPositionState("OTHER", "long", 300, 1, Decimal("90"), Decimal("110"))
    account = _account(
        balance_free=Decimal("70000"),
        daily_pnl=Decimal("3000"),
        open_positions=(held,),
        open_positions_count=1,
        open_tickers=frozenset({"OTHER"}),
    )
    verdict = check(side="long", account=account, instrument=_instrument(), config=_config())
    assert not {c.name: c for c in verdict.checks}["daily_loss_limit_not_reached"].passed
    assert verdict.max_lots == 0


def test_unset_daily_safety_fields_default_to_refusal() -> None:
    values = dataclasses.asdict(_account())
    values.pop("trading_day_confirmed")
    values.pop("daily_stop_active")
    unknown = AccountState(**values)
    assert not unknown.trading_day_confirmed and unknown.daily_stop_active
    assert (
        calculate_position_size(
            side="long", account=unknown, instrument=_instrument(), config=_config()
        )
        == 0
    )
    assert not check(
        side="long", account=unknown, instrument=_instrument(), config=_config()
    ).allowed


def test_check_5_persistent_daily_stop_cannot_be_cleared_by_price_recovery() -> None:
    verdict = check(
        side="long",
        account=_account(balance_free=Decimal("100000"), daily_stop_active=True),
        instrument=_instrument(),
        config=_config(),
    )
    assert not {c.name: c for c in verdict.checks}["daily_loss_limit_not_reached"].passed
    assert not verdict.allowed
    assert (
        calculate_position_size(
            side="long",
            account=_account(daily_stop_active=True),
            instrument=_instrument(),
            config=_config(),
        )
        == 0
    )


def test_check_6_rejects_when_trades_per_day_exceeded() -> None:
    verdict = check(
        side="long",
        account=_account(trades_today=30),
        instrument=_instrument(),
        config=_config(max_trades_per_day=30),
    )
    failed = {c.name: c for c in verdict.checks}["trades_per_day_within_limit"]
    assert failed.passed is False


def test_check_7_rejects_when_free_balance_below_minimum() -> None:
    verdict = check(
        side="long",
        account=_account(balance_free=Decimal("4999")),
        instrument=_instrument(),
        config=_config(min_free_balance=Decimal("5000")),
    )
    failed = {c.name: c for c in verdict.checks}["free_balance_sufficient"]
    assert failed.passed is False


def test_check_8_rejects_when_open_positions_at_limit() -> None:
    verdict = check(
        side="long",
        account=_account(open_positions_count=7),
        instrument=_instrument(),
        config=_config(max_open_positions=7),
    )
    failed = {c.name: c for c in verdict.checks}["open_positions_within_limit"]
    assert failed.passed is False


def test_check_9_rejects_duplicate_ticker() -> None:
    verdict = check(
        side="long",
        account=_account(open_tickers=frozenset({"SBER"})),
        instrument=_instrument(ticker="SBER"),
        config=_config(),
    )
    failed = {c.name: c for c in verdict.checks}["sector_and_ticker_limits"]
    assert failed.passed is False
    assert "SBER" in failed.reason


def test_check_9_rejects_sector_limit() -> None:
    verdict = check(
        side="long",
        account=_account(open_positions_by_sector={"financials": 2}),
        instrument=_instrument(sector="financials", ticker="VTBR"),
        config=_config(max_per_sector=2),
    )
    failed = {c.name: c for c in verdict.checks}["sector_and_ticker_limits"]
    assert failed.passed is False


def test_check_10_rejects_high_correlation() -> None:
    verdict = check(
        side="long",
        account=_account(),
        instrument=_instrument(max_correlation_with_open=Decimal("0.9")),
        config=_config(max_correlation=Decimal("0.85")),
    )
    failed = {c.name: c for c in verdict.checks}["correlation_within_limit"]
    assert failed.passed is False


def test_check_11_rejects_atr_ratio_above_limit() -> None:
    verdict = check(
        side="long",
        account=_account(),
        instrument=_instrument(price=Decimal("100"), atr=Decimal("10")),  # ratio 0.10
        config=_config(max_atr_ratio=Decimal("0.05")),
    )
    failed = {c.name: c for c in verdict.checks}["volatility_within_limit"]
    assert failed.passed is False


def test_check_11_volatile_regime_halves_size_but_does_not_reject() -> None:
    account = _account()
    instrument = _instrument(regime="volatile")
    config = _config(volatile_size_scaler=Decimal("0.5"))
    verdict_volatile = check(side="long", account=account, instrument=instrument, config=config)
    verdict_trending = check(
        side="long", account=account, instrument=_instrument(regime="trending"), config=config
    )
    assert verdict_volatile.allowed is True
    assert verdict_volatile.max_lots == verdict_trending.max_lots // 2


def test_check_12_rejects_when_zero_lots() -> None:
    verdict = check(
        side="long",
        account=_account(balance_free=Decimal("1")),
        instrument=_instrument(),
        config=_config(min_free_balance=Decimal("0")),
    )
    failed = {c.name: c for c in verdict.checks}["size_and_funds_sufficient"]
    assert failed.passed is False


def test_check_12_rejects_when_cost_exceeds_balance() -> None:
    """Лоты формально >=1 по риск-формуле, но купить их не хватает средств."""
    verdict = check(
        side="long",
        account=_account(balance_free=Decimal("100")),
        instrument=_instrument(price=Decimal("1000000"), atr=Decimal("1"), lot_size=1),
        config=_config(min_free_balance=Decimal("0"), risk_per_trade=Decimal("0.99")),
    )
    failed = {c.name: c for c in verdict.checks}["size_and_funds_sufficient"]
    assert failed.passed is False


# --- Стопы/тейки/трейлинг/безубыток ---------------------------------------------


def test_initial_stop_and_take_long() -> None:
    stop = initial_stop(
        side="long",
        entry_price=Decimal("100"),
        atr=Decimal("2"),
        atr_stop_mult=Decimal("1.5"),
        price_increment=Decimal("0.01"),
    )
    take = initial_take(
        side="long", entry_price=Decimal("100"), atr=Decimal("2"), atr_take_mult=Decimal("2.0")
    )
    assert stop == Decimal("97")
    assert take == Decimal("104")


def test_initial_stop_and_take_short() -> None:
    stop = initial_stop(
        side="short",
        entry_price=Decimal("100"),
        atr=Decimal("2"),
        atr_stop_mult=Decimal("1.5"),
        price_increment=Decimal("0.01"),
    )
    take = initial_take(
        side="short", entry_price=Decimal("100"), atr=Decimal("2"), atr_take_mult=Decimal("2.0")
    )
    assert stop == Decimal("103")
    assert take == Decimal("96")


def test_initial_stop_rounds_away_from_entry_to_instrument_tick() -> None:
    long = initial_stop(
        side="long",
        entry_price=Decimal("100"),
        atr=Decimal("1.01"),
        atr_stop_mult=Decimal("1.5"),
        price_increment=Decimal("0.05"),
    )
    short = initial_stop(
        side="short",
        entry_price=Decimal("100"),
        atr=Decimal("1.01"),
        atr_stop_mult=Decimal("1.5"),
        price_increment=Decimal("0.05"),
    )
    assert long == Decimal("98.45")
    assert short == Decimal("101.55")


def test_check_rejects_non_tick_stop_before_sizing() -> None:
    verdict = check(
        side="long",
        account=_account(),
        instrument=_instrument(
            price=Decimal("100"), stop_price=Decimal("99.97"), min_price_increment=Decimal("0.05")
        ),
        config=_config(),
    )
    assert not verdict.allowed
    assert "шагу цены" in verdict.checks[-1].reason


def test_breakeven_stop_accounts_for_round_trip_costs_long() -> None:
    breakeven = breakeven_stop(
        side="long",
        entry_price=Decimal("100"),
        fee_rate=Decimal("0.003"),
        slippage_assumed=Decimal("0.0005"),
    )
    # total_cost_rate = 2*(0.003+0.0005) = 0.007 -> 100*1.007 = 100.7
    assert breakeven == Decimal("100.7")


def test_breakeven_stop_accounts_for_round_trip_costs_short() -> None:
    breakeven = breakeven_stop(
        side="short",
        entry_price=Decimal("100"),
        fee_rate=Decimal("0.003"),
        slippage_assumed=Decimal("0.0005"),
    )
    assert breakeven == Decimal("99.3")


def test_trailing_stop_long_and_short() -> None:
    long_trail = trailing_stop(
        side="long", current_price=Decimal("110"), atr=Decimal("2"), atr_trail_dist=Decimal("1.0")
    )
    short_trail = trailing_stop(
        side="short", current_price=Decimal("90"), atr=Decimal("2"), atr_trail_dist=Decimal("1.0")
    )
    assert long_trail == Decimal("108")
    assert short_trail == Decimal("92")


def test_can_move_stop_long_only_upward() -> None:
    assert can_move_stop(side="long", current_stop=Decimal("95"), new_stop=Decimal("97")) is True
    assert can_move_stop(side="long", current_stop=Decimal("95"), new_stop=Decimal("94")) is False
    assert can_move_stop(side="long", current_stop=Decimal("95"), new_stop=Decimal("95")) is True


def test_can_move_stop_short_only_downward() -> None:
    assert can_move_stop(side="short", current_stop=Decimal("105"), new_stop=Decimal("103")) is True
    assert (
        can_move_stop(side="short", current_stop=Decimal("105"), new_stop=Decimal("106")) is False
    )


# --- Отсутствие публичного API усиления риска -----------------------------------


def test_risk_config_is_frozen_cannot_be_mutated() -> None:
    config = _config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.risk_per_trade = Decimal("0.5")  # type: ignore[misc]


def test_no_public_function_can_increase_risk_parameters() -> None:
    """Ни одна публичная функция модуля не принимает и не возвращает `RiskConfig`
    с изменённым (увеличенным) значением -- единственный способ изменить лимиты --
    правка `config.py` человеком и рестарт (AGENTS.md §4.2).
    """
    import inspect

    import trading_bot.risk_manager as risk_manager_module

    forbidden_name_fragments = ("increase", "override", "boost", "raise_limit", "set_risk")
    public_names = [name for name in dir(risk_manager_module) if not name.startswith("_")]
    for name in public_names:
        lowered = name.lower()
        assert not any(fragment in lowered for fragment in forbidden_name_fragments), name

    # Ни одна публичная функция не принимает произвольный override-параметр риска.
    for name in public_names:
        obj = getattr(risk_manager_module, name)
        if inspect.isfunction(obj):
            for param_name in inspect.signature(obj).parameters:
                assert "override" not in param_name.lower()
