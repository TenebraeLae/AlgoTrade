"""Риск-менеджмент (слой L3, docs/01_ARCHITECTURE.md, docs/04_RISK.md).

Единственный модуль, который решает **сколько** и **можно ли**. Закрытая зона
(AGENTS.md §4.2, docs/13_SECURITY.md): ни одна точка входа — бот, VK, дашборд,
оптимизатор — не может увеличить `risk_per_trade`, дневной лимит потерь, максимум
позиций или ослабить любой стоп через этот модуль. Здесь нет ни одной публичной
функции, принимающей риск-параметр как аргумент для его повышения; риск читается
только из `config.RiskConfig`, которую собирает `config.load_settings()` — правка
только человеком в файле конфигурации.

Разрешено: считать размер позиции, стоп/тейк/трейлинг, проверять 12 условий перед
открытием позиции (docs/04_RISK.md), выдавать `RiskVerdict`. Запрещено: знать о
брокере/заявках (это `executor.py`), считать P&L (это `portfolio.py`), решать,
торговать ли инструмент (это `strategy.py`) — риск-менеджер только проверяет то,
что ему принесли, и не ходит за данными сам.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Literal

from trading_bot.api_client import NORMAL_TRADING_STATUS

Side = Literal["long", "short"]
#: Режимы рынка из docs/03_STRATEGY.md — risk_manager сам их не вычисляет,
#: только реагирует (chaos → отказ, volatile → уменьшение размера).
Regime = Literal["trending", "ranging", "volatile", "chaos", "transitional"]

#: Имена 12 проверок в фиксированном порядке (docs/04_RISK.md) — порядок важен
#: для сопоставимости journal между периодами, поэтому это константа, не набор.
CHECK_NAMES: tuple[str, ...] = (
    "trading_mode_valid",
    "session_open",
    "instrument_status_normal",
    "spread_within_limit",
    "daily_loss_limit_not_reached",
    "trades_per_day_within_limit",
    "free_balance_sufficient",
    "open_positions_within_limit",
    "sector_and_ticker_limits",
    "correlation_within_limit",
    "volatility_within_limit",
    "size_and_funds_sufficient",
)


@dataclass(frozen=True)
class RiskConfig:
    """Параметры риска — инжектируются из `config.Settings`, не читаются напрямую.

    Публичного способа заменить экземпляр на «более рискованный» в этом модуле
    нет: `RiskConfig` неизменяем (`frozen=True`), и ни одна функция ниже не
    принимает и не возвращает изменённую копию с большими лимитами.
    """

    risk_per_trade: Decimal
    atr_stop_mult: Decimal
    atr_take_mult: Decimal
    atr_trail_trigger: Decimal
    atr_trail_dist: Decimal
    daily_loss_limit: Decimal
    global_drawdown_stop: Decimal
    max_position_pct: Decimal
    max_volume_participation: Decimal
    max_trades_per_day: int
    min_free_balance: Decimal
    max_open_positions: int
    max_per_sector: int
    max_correlation: Decimal
    max_atr_ratio: Decimal
    max_spread_bps: Decimal
    volatile_size_scaler: Decimal
    slippage_assumed: Decimal
    fee_rate: Decimal


@dataclass(frozen=True)
class OpenPositionState:
    """Открытая позиция: цены за бумагу, число лотов и их лотность — из журнала."""

    ticker: str
    side: Side
    lots: int
    lot_size: int
    stop_price: Decimal | None
    market_price: Decimal


@dataclass(frozen=True)
class AccountState:
    """Снимок состояния счёта на момент проверки — поставляется `portfolio.py` (M6).

    risk_manager сам не читает БД/брокера: все факты приходят готовыми.
    """

    trading_mode: str
    balance_free: Decimal
    trades_today: int
    daily_pnl: Decimal
    daily_start_balance: Decimal  # капитал на начало торгового дня, не свободный cash
    starting_equity: Decimal  # капитал при запуске для глобального лимита
    open_positions: tuple[OpenPositionState, ...]
    open_positions_count: int
    open_positions_by_sector: dict[str, int] = field(default_factory=dict)
    open_tickers: frozenset[str] = frozenset()
    daily_anchor_day: date | None = None
    trading_day_confirmed: bool = False
    daily_stop_active: bool = True


@dataclass(frozen=True)
class InstrumentState:
    """Снимок состояния инструмента/сигнала на момент проверки."""

    ticker: str
    sector: str
    price: Decimal
    atr: Decimal
    lot_size: int
    stop_price: Decimal | None  # фактический, уже выбранный/округлённый стоп заявки
    min_price_increment: Decimal  # шаг цены из проверенного инструмента
    average_volume_lots: Decimal | None  # проверенный 5m-объём именно в лотах
    currency: str
    spread_bps: Decimal
    trading_status: str
    regime: Regime
    is_session_open: bool
    session_reason: str
    max_correlation_with_open: Decimal


@dataclass(frozen=True)
class CheckResult:
    """Результат одной из 12 проверок — с причиной, а не голым `bool`."""

    name: str
    passed: bool
    reason: str


@dataclass(frozen=True)
class RiskVerdict:
    """Итог проверки сигнала (docs/01_ARCHITECTURE.md: `allowed, reason, max_lots, checks`)."""

    allowed: bool
    reason: str
    max_lots: int
    checks: tuple[CheckResult, ...]
    effective_risk_pct: Decimal = Decimal(0)  # число процентов, не дробная доля


@dataclass(frozen=True)
class _Sizing:
    lots: int
    capital: Decimal
    risk_budget: Decimal
    loss_per_lot: Decimal
    binding: str
    reason: str


def _positive(value: Decimal) -> bool:
    return value.is_finite() and value > 0


def capital_at_stops(account: AccountState, config: RiskConfig) -> Decimal | None:
    """Капитал B после исполнения всех стопов; None при непроверенных данных.

    Ту же формулу используют размер позиции, дневной фильтр и персистентный
    paper-стоп. Гэп за стопом учитывается худшей из рыночной и стоп-цены.
    """
    if (
        not account.balance_free.is_finite()
        or not config.fee_rate.is_finite()
        or not config.slippage_assumed.is_finite()
        or not 0 <= config.fee_rate < 1
        or not 0 <= config.slippage_assumed < 1
        or account.open_positions_count != len(account.open_tickers)
        or account.open_tickers != frozenset(pos.ticker for pos in account.open_positions)
    ):
        return None
    capital = account.balance_free
    one = Decimal(1)
    for position in account.open_positions:
        stop, mark = position.stop_price, position.market_price
        if (
            stop is None
            or not _positive(stop)
            or not _positive(mark)
            or position.lots <= 0
            or position.lot_size <= 0
            or position.side not in ("long", "short")
            or not position.ticker
        ):
            return None
        units = Decimal(position.lots * position.lot_size)
        if position.side == "long":
            capital += (
                units * min(stop, mark) * (one - config.slippage_assumed) * (one - config.fee_rate)
            )
        else:
            capital -= (
                units * max(stop, mark) * (one + config.slippage_assumed) * (one + config.fee_rate)
            )
    return capital if capital.is_finite() else None


def _size_position(
    *, side: Side, account: AccountState, instrument: InstrumentState, config: RiskConfig
) -> _Sizing:
    """Чистый расчёт четырёх потолков и капитала на стопах; ошибка данных → отказ."""

    def refuse(reason: str) -> _Sizing:
        return _Sizing(0, Decimal(0), Decimal(0), Decimal(0), "none", reason)

    if instrument.currency != "RUB":
        return refuse("валюта инструмента не RUB")
    if side not in ("long", "short"):
        return refuse("неизвестная сторона позиции")
    price, stop = instrument.price, instrument.stop_price
    if (
        not _positive(price)
        or stop is None
        or not _positive(stop)
        or instrument.lot_size <= 0
        or not _positive(instrument.min_price_increment)
        or instrument.average_volume_lots is None
        or not _positive(instrument.average_volume_lots)
    ):
        return refuse("нет пригодных цены, стопа, лотности, шага цены или 5m-объёма в лотах")
    if stop % instrument.min_price_increment != 0:
        return refuse("стоп не кратен шагу цены инструмента")
    if (side == "long" and stop >= price) or (side == "short" and stop <= price):
        return refuse("стоп не на убыточной стороне цены входа")
    if account.daily_stop_active or not account.trading_day_confirmed:
        return refuse("дневной стоп активен или торговый день не подтверждён")
    if (
        not _positive(account.daily_start_balance)
        or not _positive(account.starting_equity)
        or not account.balance_free.is_finite()
        or account.open_positions_count != len(account.open_tickers)
        or account.open_tickers != frozenset(pos.ticker for pos in account.open_positions)
    ):
        return refuse("нет корректного капитала начала дня/старта или снимка позиций")
    if not all(
        value.is_finite()
        for value in (
            config.risk_per_trade,
            config.daily_loss_limit,
            config.global_drawdown_stop,
            config.max_position_pct,
            config.max_volume_participation,
            config.volatile_size_scaler,
            config.slippage_assumed,
            config.fee_rate,
            config.min_free_balance,
            config.max_spread_bps,
            config.max_correlation,
            config.max_atr_ratio,
        )
    ):
        return refuse("некорректные параметры риска")
    if (
        not 0 < config.risk_per_trade < 1
        or not 0 < config.daily_loss_limit < 1
        or not 0 < config.global_drawdown_stop < 1
        or not 0 < config.max_position_pct <= 1
        or not 0 < config.max_volume_participation <= 1
        or not 0 <= config.volatile_size_scaler <= 1
        or not 0 <= config.slippage_assumed < 1
        or not 0 <= config.fee_rate < 1
        or config.min_free_balance < 0
    ):
        return refuse("некорректные параметры риска")

    one = Decimal(1)
    fee, slip = config.fee_rate, config.slippage_assumed
    cash = account.balance_free
    equity = cash
    gross_exposure = Decimal(0)
    for position in account.open_positions:
        mark, held_stop = position.market_price, position.stop_price
        if (
            held_stop is None
            or not _positive(mark)
            or not _positive(held_stop)
            or position.lots <= 0
            or position.lot_size <= 0
            or position.side not in ("long", "short")
            or not position.ticker
        ):
            return refuse("открытая позиция без пригодного стопа или рыночной цены")
        units = Decimal(position.lots * position.lot_size)
        value = units * mark
        gross_exposure += value
        if position.side == "long":
            equity += value
        else:
            equity -= value
    capital = capital_at_stops(account, config)
    if capital is None:
        return refuse("капитал на стопах нельзя проверить")
    if not _positive(capital):
        return refuse("капитал на стопах неположителен")

    risk_limits = (
        ("risk", config.risk_per_trade * capital),
        ("daily_headroom", capital - (one - config.daily_loss_limit) * account.daily_start_balance),
        ("drawdown", capital - (one - config.global_drawdown_stop) * account.starting_equity),
    )
    binding_risk, budget = min(risk_limits, key=lambda item: item[1])
    if budget <= 0:
        return refuse(f"исчерпан бюджет риска: {binding_risk}")

    units_per_lot = Decimal(instrument.lot_size)
    if side == "long":
        loss_per_lot = units_per_lot * (
            price * (one + slip) * (one + fee) - stop * (one - slip) * (one - fee)
        )
    else:
        loss_per_lot = units_per_lot * (
            stop * (one + slip) * (one + fee) - price * (one - slip) * (one - fee)
        )
    if not _positive(loss_per_lot):
        return refuse("неположительный убыток на лот")

    funds = max(Decimal(0), equity - gross_exposure - config.min_free_balance)
    limits = (
        (binding_risk, budget / loss_per_lot),
        ("cap", config.max_position_pct * capital / (units_per_lot * price)),
        ("funds", funds / (units_per_lot * price * (one + slip) * (one + fee))),
        ("liquidity", config.max_volume_participation * instrument.average_volume_lots),
    )
    binding, unscaled = min(limits, key=lambda item: item[1])
    scale = config.volatile_size_scaler if instrument.regime == "volatile" else one
    lots = int((unscaled * scale).to_integral_value(rounding=ROUND_DOWN))
    reason = f"связывающий потолок: {binding}; расчётный размер {lots} лотов"
    return _Sizing(lots, capital, budget, loss_per_lot, binding, reason)


def calculate_position_size(
    *, side: Side, account: AccountState, instrument: InstrumentState, config: RiskConfig
) -> int:
    """Число лотов по капиталу на стопах и четырём потолкам, всегда `ROUND_DOWN`.

    При отсутствии проверенного стопа, капитала или объёма возвращает 0 (fail closed).
    Не проверяет торговую сессию: для разрешения сделки обязателен `check()`.
    """
    return _size_position(side=side, account=account, instrument=instrument, config=config).lots


def round_stop_price(*, side: Side, stop_price: Decimal, price_increment: Decimal) -> Decimal:
    """Округляет стоп от входа: вниз для long, вверх для short, по шагу инструмента."""
    if not _positive(stop_price) or not _positive(price_increment) or side not in ("long", "short"):
        raise ValueError("стоп, шаг цены и сторона позиции должны быть пригодны")
    rounding = ROUND_FLOOR if side == "long" else ROUND_CEILING
    ticks = (stop_price / price_increment).to_integral_value(rounding=rounding)
    return ticks * price_increment


def initial_stop(
    *,
    side: Side,
    entry_price: Decimal,
    atr: Decimal,
    atr_stop_mult: Decimal,
    price_increment: Decimal,
) -> Decimal:
    """Начальный ATR-стоп, округлённый от входа по реальному шагу цены инструмента."""
    if not _positive(entry_price) or not _positive(atr) or not _positive(atr_stop_mult):
        raise ValueError("цена входа, ATR и множитель стопа должны быть положительны")
    distance = atr * atr_stop_mult
    raw = entry_price - distance if side == "long" else entry_price + distance
    return round_stop_price(side=side, stop_price=raw, price_increment=price_increment)


def initial_take(
    *, side: Side, entry_price: Decimal, atr: Decimal, atr_take_mult: Decimal
) -> Decimal:
    """Тейк-профит: вход ± `atr_take_mult × ATR` (docs/04_RISK.md)."""
    distance = atr * atr_take_mult
    return entry_price + distance if side == "long" else entry_price - distance


def breakeven_stop(
    *, side: Side, entry_price: Decimal, fee_rate: Decimal, slippage_assumed: Decimal
) -> Decimal:
    """«Честный» безубыток: закрытие здесь даёт net ≈ 0, а не небольшой убыток.

    Round-trip (вход + выход) несёт комиссию и проскальзывание дважды —
    docs/04_RISK.md: «с учётом комиссии и проскальзывания, честный ноль — это
    минус издержки». `total_cost_rate = 2*(fee_rate + slippage_assumed)`.
    """
    total_cost_rate = Decimal(2) * (fee_rate + slippage_assumed)
    if side == "long":
        return entry_price * (Decimal(1) + total_cost_rate)
    return entry_price * (Decimal(1) - total_cost_rate)


def trailing_stop(
    *, side: Side, current_price: Decimal, atr: Decimal, atr_trail_dist: Decimal
) -> Decimal:
    """Уровень трейлинг-стопа от текущей цены (не гарантирует, что его можно
    поставить — это решает `can_move_stop`, вызывающий код обязан её применить).
    """
    distance = atr * atr_trail_dist
    return current_price - distance if side == "long" else current_price + distance


def can_move_stop(*, side: Side, current_stop: Decimal, new_stop: Decimal) -> bool:
    """Перемещение стопа разрешено только в пользу позиции (docs/04_RISK.md).

    Для `long` — только вверх (ближе к цене/выше), для `short` — только вниз.
    Это единственная функция, решающая, можно ли принять новый уровень стопа —
    ни исполнитель, ни VK, ни оптимизатор не могут обойти её проверку молча.
    """
    if side == "long":
        return new_stop >= current_stop
    return new_stop <= current_stop


def _check_trading_mode(account: AccountState) -> CheckResult:
    passed = account.trading_mode in ("paper", "live")
    reason = (
        "режим торговли определён" if passed else f"неизвестный режим: {account.trading_mode!r}"
    )
    return CheckResult(CHECK_NAMES[0], passed, reason)


def _check_session(instrument: InstrumentState) -> CheckResult:
    return CheckResult(CHECK_NAMES[1], instrument.is_session_open, instrument.session_reason)


def _check_instrument_status(instrument: InstrumentState) -> CheckResult:
    passed = instrument.trading_status == NORMAL_TRADING_STATUS
    reason = (
        "статус NORMAL_TRADING"
        if passed
        else f"статус инструмента не NORMAL_TRADING: {instrument.trading_status}"
    )
    return CheckResult(CHECK_NAMES[2], passed, reason)


def _check_spread(instrument: InstrumentState, config: RiskConfig) -> CheckResult:
    passed = (
        instrument.spread_bps.is_finite()
        and config.max_spread_bps.is_finite()
        and instrument.spread_bps <= config.max_spread_bps
    )
    reason = (
        f"спред {instrument.spread_bps} bps ≤ {config.max_spread_bps}"
        if passed
        else f"спред {instrument.spread_bps} bps > лимита {config.max_spread_bps}"
    )
    return CheckResult(CHECK_NAMES[3], passed, reason)


def _check_daily_loss_limit(account: AccountState, config: RiskConfig) -> CheckResult:
    if not account.trading_day_confirmed:
        return CheckResult(
            CHECK_NAMES[4], False, "новый торговый день не подтверждён закрытым баром"
        )
    if account.daily_stop_active:
        return CheckResult(CHECK_NAMES[4], False, "дневной стоп уже зафиксирован в журнале")
    capital = capital_at_stops(account, config)
    if (
        not _positive(account.daily_start_balance)
        or capital is None
        or not capital.is_finite()
        or not config.daily_loss_limit.is_finite()
        or not 0 < config.daily_loss_limit < 1
    ):
        return CheckResult(CHECK_NAMES[4], False, "капитал на стопах/якорь дня непригоден")
    daily_loss_pct = (account.daily_start_balance - capital) / account.daily_start_balance
    passed = daily_loss_pct < config.daily_loss_limit
    reason = (
        f"дневная просадка на стопах {daily_loss_pct:.4f} < лимита {config.daily_loss_limit}"
        if passed
        else f"дневная просадка на стопах {daily_loss_pct:.4f} достигла/превысила лимит "
        f"{config.daily_loss_limit}"
    )
    return CheckResult(CHECK_NAMES[4], passed, reason)


def _check_trades_per_day(account: AccountState, config: RiskConfig) -> CheckResult:
    passed = account.trades_today < config.max_trades_per_day
    reason = f"сделок за день {account.trades_today} из {config.max_trades_per_day}"
    return CheckResult(CHECK_NAMES[5], passed, reason)


def _check_free_balance(account: AccountState, config: RiskConfig) -> CheckResult:
    passed = (
        account.balance_free.is_finite()
        and config.min_free_balance.is_finite()
        and account.balance_free >= config.min_free_balance
    )
    reason = (
        f"свободный баланс {account.balance_free} ≥ {config.min_free_balance}"
        if passed
        else f"свободный баланс {account.balance_free} < минимума {config.min_free_balance}"
    )
    return CheckResult(CHECK_NAMES[6], passed, reason)


def _check_open_positions(account: AccountState, config: RiskConfig) -> CheckResult:
    passed = account.open_positions_count < config.max_open_positions
    reason = f"открытых позиций {account.open_positions_count} из {config.max_open_positions}"
    return CheckResult(CHECK_NAMES[7], passed, reason)


def _check_sector_and_ticker(
    account: AccountState, instrument: InstrumentState, config: RiskConfig
) -> CheckResult:
    if instrument.ticker in account.open_tickers:
        return CheckResult(CHECK_NAMES[8], False, f"позиция по {instrument.ticker} уже открыта")
    sector_count = account.open_positions_by_sector.get(instrument.sector, 0)
    passed = sector_count < config.max_per_sector
    reason = (
        f"в секторе {instrument.sector} {sector_count} из {config.max_per_sector}"
        if passed
        else f"в секторе {instrument.sector} уже {sector_count} ≥ лимита {config.max_per_sector}"
    )
    return CheckResult(CHECK_NAMES[8], passed, reason)


def _check_correlation(instrument: InstrumentState, config: RiskConfig) -> CheckResult:
    passed = (
        instrument.max_correlation_with_open.is_finite()
        and config.max_correlation.is_finite()
        and instrument.max_correlation_with_open < config.max_correlation
    )
    reason = (
        f"корреляция {instrument.max_correlation_with_open} < {config.max_correlation}"
        if passed
        else f"корреляция {instrument.max_correlation_with_open} ≥ лимита {config.max_correlation}"
    )
    return CheckResult(CHECK_NAMES[9], passed, reason)


def _check_volatility(instrument: InstrumentState, config: RiskConfig) -> CheckResult:
    if not _positive(instrument.price) or not _positive(instrument.atr):
        return CheckResult(CHECK_NAMES[10], False, "цена или ATR инструмента непригодны")
    if not config.max_atr_ratio.is_finite():
        return CheckResult(CHECK_NAMES[10], False, "лимит ATR непригоден")
    atr_ratio = instrument.atr / instrument.price
    if instrument.regime == "chaos":
        return CheckResult(CHECK_NAMES[10], False, f"режим chaos (ATR/price={atr_ratio:.4f})")
    passed = atr_ratio <= config.max_atr_ratio
    reason = (
        f"ATR/price {atr_ratio:.4f} ≤ {config.max_atr_ratio}"
        if passed
        else f"ATR/price {atr_ratio:.4f} > лимита {config.max_atr_ratio}"
    )
    return CheckResult(CHECK_NAMES[10], passed, reason)


def _check_size_and_funds(sizing: _Sizing) -> CheckResult:
    """Урезанный по средствам размер — отказ только если не помещается ни один лот."""
    reason = sizing.reason if sizing.lots >= 1 else f"INSUFFICIENT_SIZE: {sizing.reason}"
    return CheckResult(CHECK_NAMES[11], sizing.lots >= 1, reason)


def check(
    *,
    side: Side,
    account: AccountState,
    instrument: InstrumentState,
    config: RiskConfig,
) -> RiskVerdict:
    """Прогоняет все 12 проверок в фиксированном порядке (docs/04_RISK.md) и
    считает размер позиции. Все проверки выполняются независимо от результата
    предыдущих — journal должен видеть полный snapshot причин (docs/03_STRATEGY.md,
    «Journal решения»), а не только первую сработавшую.

    Возврат: `RiskVerdict` — `allowed=True` только если все 12 проверок прошли;
    `max_lots` — расчётный размер (с учётом `volatile`-уменьшения), `0`, если
    хоть одна проверка не пройдена или лотов не набралось.
    """
    sizing = _size_position(side=side, account=account, instrument=instrument, config=config)

    checks = (
        _check_trading_mode(account),
        _check_session(instrument),
        _check_instrument_status(instrument),
        _check_spread(instrument, config),
        _check_daily_loss_limit(account, config),
        _check_trades_per_day(account, config),
        _check_free_balance(account, config),
        _check_open_positions(account, config),
        _check_sector_and_ticker(account, instrument, config),
        _check_correlation(instrument, config),
        _check_volatility(instrument, config),
        _check_size_and_funds(sizing),
    )

    allowed = all(c.passed for c in checks)
    if allowed:
        reason = f"все проверки пройдены; {sizing.reason}"
        final_lots = sizing.lots
        effective_risk_pct = Decimal(100) * final_lots * sizing.loss_per_lot / sizing.capital
    else:
        first_failed = next(c for c in checks if not c.passed)
        reason = first_failed.reason
        final_lots = 0
        effective_risk_pct = Decimal(0)

    return RiskVerdict(
        allowed=allowed,
        reason=reason,
        max_lots=final_lots,
        checks=checks,
        effective_risk_pct=effective_risk_pct,
    )
