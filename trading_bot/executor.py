"""Paper-only оркестрация одного закрытого 5m-тика (слой L4).

Разрешено: стратегия → 12 проверок риска → обязательный SQLite-журнал →
бумажное исполнение → FIFO-портфель. Запрещено: gRPC-клиент, live-заявки,
выдуманный рыночный контекст или автоматический запуск без поставщика тиков.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Literal

from trading_bot.api_client import Candle
from trading_bot.broker.paper import Fill, PaperBroker
from trading_bot.journal import DecisionJournal
from trading_bot.portfolio import Portfolio, PortfolioError
from trading_bot.risk_manager import (
    AccountState,
    InstrumentState,
    RiskConfig,
    RiskVerdict,
    Side,
    capital_at_stops,
    check,
    initial_stop,
    initial_take,
    round_stop_price,
)
from trading_bot.source_events import SourceRefusal
from trading_bot.strategy import (
    Decision,
    MarketFilterResult,
    ScheduledRegime,
    Signal,
    StrategyConfig,
    evaluate,
)
from trading_bot.utils import MSK

logger = logging.getLogger(__name__)
_CANDLE_5M = timedelta(minutes=5)
_CANDLE_15M = timedelta(minutes=15)
_CANDLE_1H = timedelta(hours=1)


def _round_take_price(*, side: Side, take: Decimal, entry: Decimal, increment: Decimal) -> Decimal:
    """Округляет тейк по шагу к входу, не превращая его в невалидный уровень."""
    if not take.is_finite() or take <= 0 or not entry.is_finite() or entry <= 0:
        raise ValueError("цена входа и тейк должны быть положительными")
    rounding = ROUND_FLOOR if side == "long" else ROUND_CEILING
    rounded = (take / increment).to_integral_value(rounding=rounding) * increment
    if (
        rounded <= 0
        or (side == "long" and rounded <= entry)
        or (side == "short" and rounded >= entry)
    ):
        raise ValueError("тейк после округления должен остаться по нужную сторону входа")
    return rounded


@dataclass(frozen=True)
class StrategyInputs:
    """Свечи и проверенный контекст сессии для одного решения стратегии.

    `is_session_open` и `session_reason` должны поступать от проверки календаря
    и статуса, а не вычисляться исполнителем из часов или догадок.
    """

    ticker: str
    candles_5m: list[Candle]
    candles_15m: list[Candle]
    candles_1h: list[Candle]
    config: StrategyConfig
    now: datetime
    is_session_open: bool
    session_reason: str
    short_enabled: bool
    session_guard_active: bool
    gap_result: tuple[bool, Decimal] | None = None
    market_filter: MarketFilterResult | None = None
    session_refusal_filter: Literal["session_open", "instrument_status_normal"] = "session_open"
    local_regime: ScheduledRegime | None = None


@dataclass(frozen=True)
class PaperMarketContext:
    """Проверенные данные инструмента и рынка; пропуски не заменяются числами."""

    sector: str
    lot_size: int
    min_price_increment: Decimal
    average_volume_lots: Decimal | None
    currency: str
    spread_bps: Decimal
    trading_status: str
    max_correlation_with_open: Decimal
    available_lots: int | None = None


@dataclass(frozen=True)
class PaperTick:
    """Полный вход одного paper-тика: ни одна часть не требует токена/API."""

    strategy: StrategyInputs
    account: AccountState
    market: PaperMarketContext


@dataclass(frozen=True)
class PaperTickResult:
    """Решение и бумажный исход.

    `decisions.size_lots` — разрешённый размер, не фактически исполненные лоты;
    при нулевом/частичном fill его результат отдельно виден в `fill`/`trades`.
    """

    decision: Decision | None
    decision_id: int | None
    risk_verdict: RiskVerdict | None
    stop_price: Decimal | None
    fill: Fill | None
    trade_id: int | None
    already_processed: bool = False


class PaperRecoveryRequired(RuntimeError):
    """Незавершённое paper-решение требует ручной сверки БД перед запуском."""


class PaperCycle:
    """Исполняет только бумажные сделки после фиксации полного решения в SQLite.

    Поставщик `PaperTick` обязан получать свежий статус, свечи, сессию, рынок
    и учётный снимок извне (`paper_feed.PaperTickFeed` при явном bootstrap).
    Экземпляр не принимает gateway/токен и создаёт только `PaperBroker` и paper-журнал.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        starting_balance: Decimal,
        strategy_config: StrategyConfig,
        risk_config: RiskConfig,
    ) -> None:
        self._conn = conn
        with self._conn:
            self._conn.execute("BEGIN IMMEDIATE")
            self._require_reconciled()
            self._legacy_cutoff = self._load_legacy_cutoff()
        self._strategy_config = strategy_config
        self._risk_config = risk_config
        self._journal = DecisionJournal(conn, mode="paper")
        self._portfolio = Portfolio(conn, mode="paper", starting_balance=starting_balance)
        self._broker = PaperBroker(
            fee_rate=risk_config.fee_rate, slippage_assumed=risk_config.slippage_assumed
        )

    def _require_reconciled(self) -> None:
        """Блокирует запуск, если любой paper-claim остался pending/failed."""
        row = self._conn.execute(
            "SELECT id, ticker, processing_state FROM decisions "
            "WHERE mode = 'paper' AND processing_state IN ('pending', 'failed') "
            "ORDER BY id LIMIT 1"
        ).fetchone()
        if row is not None:
            raise PaperRecoveryRequired(
                f"ручная сверка обязательна: decision_id={row[0]} ticker={row[1]} "
                f"state={row[2]}"
            )

    def _load_legacy_cutoff(self) -> datetime | None:
        """Читает сохранённую границу 0006; битый маркер останавливает paper-цикл."""
        row = self._conn.execute(
            "SELECT cutoff_at, has_legacy_paper_decisions "
            "FROM paper_recovery_meta WHERE singleton=1"
        ).fetchone()
        if row is None:
            raise PaperRecoveryRequired("cutoff миграции отсутствует; требуется ручная сверка")
        try:
            cutoff = datetime.fromisoformat(row[0])
        except (TypeError, ValueError) as exc:
            raise PaperRecoveryRequired(
                "cutoff миграции нечитаем; требуется ручная сверка"
            ) from exc
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise PaperRecoveryRequired("cutoff миграции без timezone; требуется ручная сверка")
        return cutoff.astimezone(UTC) if row[1] else None

    def record_source_refusal(self, refusal: SourceRefusal) -> int:
        """Сохраняет отказ источника до пропуска или остановки цикла."""
        return self._journal.record_source_refusal(refusal)

    def resolve_source_refusals(self, ticker: str, at: datetime) -> int:
        """Закрывает эпизоды тикера только после полного успешного paper-тика."""
        return self._journal.resolve_source_refusals(ticker, at)

    def _validate_input(self, tick: PaperTick) -> None:
        """Не допускает live или несогласованный с SQLite снимок paper-счёта."""
        account = tick.account
        if account.trading_mode != "paper":
            raise ValueError("paper-цикл принимает только paper-счёт")
        today = tick.strategy.now.astimezone(MSK).date()
        if account.daily_anchor_day is None or account.daily_anchor_day > today:
            raise ValueError("нет проверенного дневного якоря paper-счёта")
        if account.trading_day_confirmed and account.daily_anchor_day != today:
            raise ValueError("дневной якорь не совпадает с подтверждённым торговым днём")
        if tick.strategy.config != self._strategy_config:
            raise ValueError("конфигурация стратегии тика отличается от конфигурации запуска")
        if tick.strategy.now.tzinfo is None or tick.strategy.now.utcoffset() is None:
            raise ValueError("время тика должно быть tz-aware")
        if account.balance_free != self._portfolio.balance:
            raise ValueError("баланс снимка не совпадает с бумажным портфелем")
        actual_tickers = self._portfolio.open_tickers()
        if (
            account.open_tickers != actual_tickers
            or frozenset(position.ticker for position in account.open_positions) != actual_tickers
            or account.open_positions_count != len(actual_tickers)
            or len(account.open_positions) != len(actual_tickers)
        ):
            raise ValueError("открытые позиции снимка не совпадают с бумажным портфелем")
        for state in account.open_positions:
            stored = self._portfolio.get_position(state.ticker)
            if (
                stored is None
                or stored.side != state.side
                or stored.lots != state.lots
                or stored.stop != state.stop_price
            ):
                raise ValueError("позиции снимка не совпадают с бумажным портфелем")
        step = tick.market.min_price_increment
        if not step.is_finite() or step <= 0 or tick.market.lot_size <= 0:
            raise ValueError("шаг цены и размер лота должны быть положительными")

    @staticmethod
    def _closed(candles: list[Candle], *, now: datetime, duration: timedelta) -> list[Candle]:
        """Возвращает хронологически упорядоченные завершившиеся свечи до `now`."""
        for candle in candles:
            if candle.ts_open.tzinfo is None or candle.ts_open.utcoffset() is None:
                raise ValueError("свеча должна иметь tz-aware timestamp")
        closed = [
            candle
            for candle in candles
            if candle.is_complete is True and candle.ts_open + duration <= now
        ]
        return sorted(closed, key=lambda candle: candle.ts_open)

    def _prepare_risk(
        self, tick: PaperTick, decision: Decision, price: Decimal
    ) -> tuple[Signal, Decimal, Decimal, RiskVerdict]:
        """Строит уровни и полные 12 проверок по единому закрытому close."""
        signal = decision.signal
        if signal is None:
            raise ValueError("решение signal не содержит Signal")
        inputs, market = tick.strategy, tick.market
        atr = Decimal(decision.indicator_snapshot["atr"])
        if signal.stop_price is None:
            stop = initial_stop(
                side=signal.side,
                entry_price=price,
                atr=atr,
                atr_stop_mult=self._risk_config.atr_stop_mult,
                price_increment=market.min_price_increment,
            )
        else:
            stop = round_stop_price(
                side=signal.side,
                stop_price=signal.stop_price,
                price_increment=market.min_price_increment,
            )
        take = signal.take_price
        if take is None:
            take = initial_take(
                side=signal.side,
                entry_price=price,
                atr=atr,
                atr_take_mult=self._risk_config.atr_take_mult,
            )
        take = _round_take_price(
            side=signal.side, take=take, entry=price, increment=market.min_price_increment
        )
        instrument = InstrumentState(
            ticker=inputs.ticker,
            sector=market.sector,
            price=price,
            atr=atr,
            lot_size=market.lot_size,
            stop_price=stop,
            min_price_increment=market.min_price_increment,
            average_volume_lots=market.average_volume_lots,
            currency=market.currency,
            spread_bps=market.spread_bps,
            trading_status=market.trading_status,
            regime=decision.regime,
            is_session_open=inputs.is_session_open and not inputs.session_guard_active,
            session_reason=inputs.session_reason,
            max_correlation_with_open=market.max_correlation_with_open,
        )
        verdict = check(
            side=signal.side, account=tick.account, instrument=instrument, config=self._risk_config
        )
        return signal, stop, take, verdict

    def _daily_risk_account(
        self, account: AccountState
    ) -> tuple[AccountState, tuple[date, Decimal, Decimal] | None]:
        """Возвращает консервативный снимок и новый latch на пороге B/E_day0."""
        day = account.daily_anchor_day
        assert day is not None  # _validate_input проверяет якорь под SQLite lock
        existing = self._conn.execute(
            "SELECT 1 FROM paper_daily_stop_latches WHERE trading_day = ?",
            (day.isoformat(),),
        ).fetchone()
        if existing is not None:
            return replace(account, daily_stop_active=True), None
        if account.daily_stop_active:
            raise PaperRecoveryRequired("дневной стоп исчез из SQLite; требуется ручная сверка")
        limit = self._risk_config.daily_loss_limit
        capital = capital_at_stops(account, self._risk_config)
        if (
            capital is None
            or not account.daily_start_balance.is_finite()
            or account.daily_start_balance <= 0
            or not limit.is_finite()
            or not 0 < limit < 1
        ):
            return replace(account, trading_day_confirmed=False), None
        threshold = (Decimal(1) - limit) * account.daily_start_balance
        if capital <= threshold:
            return replace(account, daily_stop_active=True), (day, capital, threshold)
        return replace(account, daily_stop_active=False), None

    def _write_daily_stop(
        self, day: date, capital: Decimal, threshold: Decimal, now: datetime
    ) -> None:
        """Фиксирует первое достижение порога; запись никогда не удаляется."""
        self._conn.execute(
            "INSERT INTO paper_daily_stop_latches "
            "(trading_day, triggered_at, capital_at_stops, threshold_equity) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(trading_day) DO NOTHING",
            (day.isoformat(), now.isoformat(), str(capital), str(threshold)),
        )

    def _mark_state(self, decision_id: int, state: str) -> None:
        """Меняет pending claim только при ожидаемом состоянии; иное требует сверки."""
        cursor = self._conn.execute(
            "UPDATE decisions SET processing_state = ? "
            "WHERE id = ? AND mode = 'paper' AND processing_state = 'pending'",
            (state, decision_id),
        )
        if cursor.rowcount != 1:
            self._conn.rollback()
            raise PaperRecoveryRequired(f"claim изменён извне: decision_id={decision_id}")
        self._conn.commit()

    def run_tick(self, tick: PaperTick) -> PaperTickResult:
        """Атомарно закрепляет закрытый 5m-бар до paper-fill и исключает повторы.

        Незавершённый claim блокирует любые новые решения до ручной сверки.
        Даже если сделка уже записана, pending не переисполняется автоматически.
        """
        inputs, market = tick.strategy, tick.market
        if inputs.now.tzinfo is None or inputs.now.utcoffset() is None:
            raise ValueError("время тика должно быть tz-aware")
        now = inputs.now.astimezone(UTC)
        try:
            candles_5m = self._closed(inputs.candles_5m, now=now, duration=_CANDLE_5M)
        except ValueError:
            self.record_source_refusal(
                SourceRefusal(inputs.ticker, "candles_5m", now, None, "stop", "ValueError")
            )
            raise
        if not candles_5m:
            self.record_source_refusal(
                SourceRefusal(inputs.ticker, "candles_5m", now, None, "stop", "empty_history")
            )
            raise ValueError("нет подтверждённой закрытой 5m-свечи для paper-решения")
        bar_ts = candles_5m[-1].ts_open.astimezone(UTC)
        if bar_ts.minute % 5 or bar_ts.second or bar_ts.microsecond:
            self.record_source_refusal(
                SourceRefusal(inputs.ticker, "bar_grid", now, bar_ts, "stop", "ValueError")
            )
            raise ValueError("закрытая 5m-свеча не соответствует 5m-сетке")
        try:
            candles_15m = self._closed(inputs.candles_15m, now=now, duration=_CANDLE_15M)
            candles_1h = self._closed(inputs.candles_1h, now=now, duration=_CANDLE_1H)
        except ValueError:
            self.record_source_refusal(
                SourceRefusal(inputs.ticker, "higher_timeframe", now, bar_ts, "stop", "ValueError")
            )
            raise
        validation_failed = False
        evaluation_error_type: str | None = None
        daily_stop_pending: tuple[date, Decimal, Decimal] | None = None
        try:
            with self._conn:
                self._conn.execute("BEGIN IMMEDIATE")
                self._require_reconciled()
                row = self._conn.execute(
                    "SELECT id, processing_state FROM decisions "
                    "WHERE mode = 'paper' AND ticker = ? AND source_bar_ts = ?",
                    (inputs.ticker, bar_ts.isoformat()),
                ).fetchone()
                if row is not None:
                    if row[1] != "complete":
                        raise PaperRecoveryRequired(
                            f"ручная сверка обязательна: decision_id={row[0]} state={row[1]}"
                        )
                    return PaperTickResult(None, int(row[0]), None, None, None, None, True)
                if self._legacy_cutoff is not None and bar_ts + _CANDLE_5M <= self._legacy_cutoff:
                    logger.warning(
                        "event=paper_legacy_bar_skipped ticker=%s bar_ts=%s cutoff=%s action=ack",
                        inputs.ticker,
                        bar_ts.isoformat(),
                        self._legacy_cutoff.isoformat(),
                    )
                    return PaperTickResult(None, None, None, None, None, None, True)
                self._portfolio.refresh_balance()
                try:
                    self._validate_input(tick)
                except ValueError:
                    validation_failed = True
                    raise
                tick_account, daily_stop_pending = self._daily_risk_account(tick.account)
                tick = replace(tick, account=tick_account)
                if daily_stop_pending is not None:
                    self._write_daily_stop(*daily_stop_pending, now)
                try:
                    decision = evaluate(
                        ticker=inputs.ticker,
                        candles_5m=candles_5m,
                        candles_15m=candles_15m,
                        candles_1h=candles_1h,
                        config=self._strategy_config,
                        now=now,
                        is_session_open=inputs.is_session_open,
                        session_reason=inputs.session_reason,
                        session_refusal_filter=inputs.session_refusal_filter,
                        short_enabled=inputs.short_enabled,
                        session_guard_active=inputs.session_guard_active,
                        spread_bps=market.spread_bps,
                        gap_result=inputs.gap_result,
                        market_filter=inputs.market_filter,
                        local_regime=inputs.local_regime,
                    )
                except Exception as exc:
                    # Любая обычная ошибка evaluate — отказ, не успех-shaped no_signal.
                    # За пределами транзакции после rollback пишем безопасный эпизод.
                    evaluation_error_type = type(exc).__name__
                    raise
                if decision.verdict == "no_signal":
                    decision_id = self._journal.record(decision, source_bar_ts=bar_ts)
                    return PaperTickResult(decision, decision_id, None, None, None, None)
                try:
                    price = candles_5m[-1].close
                    signal, stop, take, verdict = self._prepare_risk(tick, decision, price)
                except (ValueError, TypeError, KeyError, ArithmeticError):
                    logger.exception(
                        "event=paper_risk_failed ticker=%s ts=%s bar_ts=%s",
                        inputs.ticker,
                        now.isoformat(),
                        bar_ts.isoformat(),
                    )
                    failure = replace(
                        decision,
                        side=None,
                        signal=None,
                        verdict="no_signal",
                        filters_failed=decision.filters_failed + ("risk_processing_error",),
                        reason="не удалось подготовить риск; paper-исполнение остановлено",
                    )
                    self._journal.record(failure, source_bar_ts=bar_ts, processing_state="failed")
                    raise
                state: Literal["complete", "pending", "failed"] = (
                    "pending" if verdict.allowed else "complete"
                )
                decision_id = self._journal.record(
                    decision, risk_verdict=verdict, source_bar_ts=bar_ts, processing_state=state
                )
                if not verdict.allowed:
                    return PaperTickResult(decision, decision_id, verdict, stop, None, None)
        except Exception as exc:
            logger.error(
                "event=paper_tick_processing_stopped ticker=%s ts=%s bar_ts=%s "
                "error_type=%s action=stop",
                inputs.ticker,
                now.isoformat(),
                bar_ts.isoformat(),
                type(exc).__name__,
            )
            if daily_stop_pending is not None:
                with self._conn:
                    self._conn.execute("BEGIN IMMEDIATE")
                    self._write_daily_stop(*daily_stop_pending, now)
            if validation_failed:
                self.record_source_refusal(
                    SourceRefusal(
                        inputs.ticker, "tick_validation", now, bar_ts, "stop", "ValueError"
                    )
                )
            elif evaluation_error_type is not None:
                logger.error(
                    "event=paper_evaluate_failed ticker=%s ts=%s bar_ts=%s "
                    "error_type=%s action=stop",
                    inputs.ticker,
                    now.isoformat(),
                    bar_ts.isoformat(),
                    evaluation_error_type,
                )
                self.record_source_refusal(
                    SourceRefusal(
                        inputs.ticker,
                        "strategy_evaluate",
                        now,
                        bar_ts,
                        "stop",
                        evaluation_error_type,
                    )
                )
            raise

        try:
            fill = self._broker.fill_market_order(
                side=signal.side,
                requested_lots=verdict.max_lots,
                lot_size=market.lot_size,
                reference_price=price,
                available_lots=market.available_lots,
            )
            trade_id = None
            if fill.filled_lots:
                # Цена сделки — референсная; slippage учитывается отдельно.
                trade_id = self._portfolio.open_position(
                    ticker=inputs.ticker,
                    side=signal.side,
                    lots=fill.filled_lots,
                    lot_size=market.lot_size,
                    price=price,
                    fee=fill.fee,
                    slippage=fill.slippage_cost,
                    journal_id=decision_id,
                    opened_at=now,
                    stop=stop,
                    take=take,
                )
            self._mark_state(decision_id, "complete")
        except (ValueError, sqlite3.Error, PortfolioError):
            logger.exception(
                "event=paper_execution_failed ticker=%s ts=%s decision_id=%s action=stop",
                inputs.ticker,
                now.isoformat(),
                decision_id,
            )
            self._mark_state(decision_id, "failed")
            raise
        return PaperTickResult(decision, decision_id, verdict, stop, fill, trade_id)
