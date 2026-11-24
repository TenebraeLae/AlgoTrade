"""Тесты trading_bot/paper_feed.py: чистые вычисления + FakeGateway/реальный SQLite.

Покрывает контракт из докстринга модуля: закрытые/свежие свечи, честный снимок
счёта на каждый вызов, ack-дедупликация, различие «новый бар + отказ» (`SourceRefusal`,
`action="stop"`) от «неполный пул/индекс» (журналируемая пауза `MarketFilterResult`),
отказ от выдуманных границ сессии, и сводку round-robin-курсора после `SourceRefusal`
(`action="skip"`) для восстановимых пер-тикерных отказов.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest

from tests.test_strategy import _config as strategy_config_base
from trading_bot import database
from trading_bot.api_client import (
    NORMAL_TRADING_STATUS,
    Candle,
    GatewayError,
    OrderBookQuote,
    ShareMetadata,
)
from trading_bot.executor import PaperCycle
from trading_bot.market_regime import MarketRegimeClassifier, MarketRegimeConfig, RegimeBar
from trading_bot.paper_account import PaperAccountSource
from trading_bot.paper_feed import (
    _LAST_5M_BAR_TIME,
    InsufficientFeedDataError,
    PaperTickFeed,
    StaleDataError,
    average_volume_prior,
    compute_gap,
    index_atr_rising,
    max_abs_correlation_with_open,
    pearson_correlation,
    pool_breadth,
    required_bars_1h,
    required_bars_5m,
    required_bars_15m,
    required_bars_index,
    session_open_and_prev_close,
    spread_bps_from_quote,
)
from trading_bot.portfolio import Portfolio
from trading_bot.risk_manager import RiskConfig
from trading_bot.source_events import SourceRefusal
from trading_bot.utils import MSK, SESSION_CLOSING_AUCTION_START, SESSION_TRADING_START

_TODAY = date(2026, 3, 2)  # понедельник
_YESTERDAY = date(2026, 2, 27)  # пятница — предыдущая торговая сессия
_EARLIER = date(2026, 2, 26)  # четверг — для daily_volume_lots
_NOW = datetime(2026, 3, 2, 19, 5, tzinfo=UTC)  # 22:05 МСК


def _msk(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=MSK).astimezone(UTC)


def _session_grid(
    session_date: date, *, end: time, start: time = SESSION_TRADING_START, minutes: int = 5
) -> list[datetime]:
    """Полная сетка таймстемпов таймфрейма внутри `[start, end)` по МСК."""
    start_dt = datetime.combine(session_date, start, tzinfo=MSK)
    end_dt = datetime.combine(session_date, end, tzinfo=MSK)
    out: list[datetime] = []
    t = start_dt
    while t + timedelta(minutes=minutes) <= end_dt:
        out.append(t.astimezone(UTC))
        t += timedelta(minutes=minutes)
    return out


def _candles_at(
    timestamps: list[datetime], *, start_price: Decimal, step: Decimal, base_volume: int = 1000
) -> list[Candle]:
    out: list[Candle] = []
    price = start_price
    for i, ts in enumerate(timestamps):
        open_ = price
        close = price + step
        out.append(
            Candle(
                open=open_,
                high=max(open_, close) + Decimal("0.5"),
                low=min(open_, close) - Decimal("0.5"),
                close=close,
                volume=base_volume + i,
                ts_open=ts,
                is_complete=True,
            )
        )
        price = close
    return out


def _config(**overrides: object) -> object:
    base = strategy_config_base(
        ema_fast=2,
        ema_mid=3,
        ema_slow=5,
        bb_period=5,
        vol_window=3,
        adx_period=2,
        macd_fast=2,
        macd_slow=5,
        macd_signal=2,
        session_guard_min=0,
    )
    return replace(base, **overrides) if overrides else base


def _classifier(**overrides: object) -> MarketRegimeClassifier:
    cfg = MarketRegimeConfig(period=2, chaos_window=3, confirm_bars=1)
    if overrides:
        cfg = replace(cfg, **overrides)
    return MarketRegimeClassifier(config=cfg)


@dataclass
class FakeGateway:
    shares: dict[str, ShareMetadata] = field(default_factory=dict)
    candles: dict[tuple[str, str], list[Candle]] = field(default_factory=dict)
    quotes: dict[str, OrderBookQuote] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    fail_share: set[str] = field(default_factory=set)
    fail_candles: set[str] = field(default_factory=set)
    fail_quote: set[str] = field(default_factory=set)
    fail_status: set[str] = field(default_factory=set)
    status_calls: list[str] = field(default_factory=list)

    def check_cancelled(self) -> None:
        """This default deterministic fake has no operator stop request."""

    @contextmanager
    def history_worker(self, stop: Event) -> Iterator[None]:
        self.check_cancelled()
        yield
        self.check_cancelled()

    def get_share_metadata(self, *, ticker: str) -> ShareMetadata:
        if ticker in self.fail_share:
            raise GatewayError(f"share недоступен: {ticker}")
        return self.shares[ticker]

    def get_candles_history(
        self, *, instrument_id: str, timeframe: str, from_: datetime, to: datetime
    ) -> list[Candle]:
        if instrument_id in self.fail_candles:
            raise GatewayError(f"свечи недоступны: {instrument_id}")
        all_candles = self.candles.get((instrument_id, timeframe), [])
        return [c for c in all_candles if from_ <= c.ts_open <= to]

    def get_order_book_quote(self, *, instrument_id: str) -> OrderBookQuote:
        if instrument_id in self.fail_quote:
            raise GatewayError(f"стакан недоступен: {instrument_id}")
        return self.quotes[instrument_id]

    def get_trading_status(self, *, instrument_id: str) -> str:
        self.status_calls.append(instrument_id)
        if instrument_id in self.fail_status:
            raise GatewayError(f"статус недоступен: {instrument_id}")
        return self.status.get(instrument_id, NORMAL_TRADING_STATUS)


def _share(ticker: str, *, sector: str = "financials") -> ShareMetadata:
    return ShareMetadata(
        ticker=ticker,
        instrument_id=f"{ticker}_ID",
        lot_size=10,
        min_price_increment=Decimal("0.01"),
        currency="RUB",
        sector=sector,
        short_enabled=True,
        trading_status=NORMAL_TRADING_STATUS,
    )


def _quote(bid: Decimal, ask: Decimal, observed_at: datetime) -> OrderBookQuote:
    return OrderBookQuote(best_bid=bid, best_ask=ask, observed_at=observed_at)


def _populate_ticker(
    gw: FakeGateway,
    ticker: str,
    *,
    start_price: Decimal = Decimal("100"),
    step: Decimal = Decimal("0.05"),
    sector: str = "financials",
    now: datetime = _NOW,
) -> ShareMetadata:
    """Заполняет FakeGateway валидными данными на `ticker` с закрытыми 5m/15m/1h
    свечами, оканчивающимися точно на границе 10:00 МСК сегодняшней сессии и
    захватывающими один подтверждённый бар предыдущей сессии (23:45 МСК пятницы)
    для честного расчёта гэпа."""
    share = _share(ticker, sector=sector)
    gw.shares[ticker] = share
    iid = share.instrument_id

    today_end = now.astimezone(MSK).time().replace(second=0, microsecond=0)
    grid_5m = _session_grid(_TODAY, end=today_end)
    yesterday_close_ts = _session_grid(_YESTERDAY, end=SESSION_CLOSING_AUCTION_START)[-1]
    assert yesterday_close_ts.astimezone(MSK).time() == _LAST_5M_BAR_TIME
    candles_5m = _candles_at(
        [yesterday_close_ts], start_price=start_price - Decimal("3"), step=Decimal("0")
    ) + _candles_at(grid_5m, start_price=start_price, step=step)
    gw.candles[(iid, "5m")] = candles_5m

    grid_15m = _session_grid(_TODAY, end=today_end, minutes=15)
    gw.candles[(iid, "15m")] = _candles_at(grid_15m, start_price=start_price, step=step)

    grid_1h = _session_grid(_TODAY, end=today_end, minutes=60)
    gw.candles[(iid, "1h")] = _candles_at(grid_1h, start_price=start_price, step=step)

    gw.quotes[iid] = _quote(start_price - Decimal("0.05"), start_price + Decimal("0.05"), now)
    gw.status[iid] = NORMAL_TRADING_STATUS
    return share


def _feed(
    gw: FakeGateway,
    account_source: PaperAccountSource,
    *,
    approved: list[str],
    clock: datetime = _NOW,
    classifier: MarketRegimeClassifier | None = None,
    index_id: str = "IMOEX_ID",
    liquidity_workers: int = 4,
) -> PaperTickFeed:
    return PaperTickFeed(
        gateway=gw,  # type: ignore[arg-type]
        account_source=account_source,
        regime_classifier=classifier or _classifier(),
        strategy_config=_config(),  # type: ignore[arg-type]
        approved_tickers=approved,
        index_instrument_id=index_id,
        clock=lambda: clock,
        correlation_window=3,
        volume_avg_window=3,
        liquidity_workers=liquidity_workers,
        max_bar_staleness=timedelta(minutes=15),
        max_quote_staleness=timedelta(minutes=5),
    )


def _db(tmp_path: Path, name: str = "paper.db") -> sqlite3.Connection:
    return database.connect(tmp_path / name)


def _account_source(conn: sqlite3.Connection) -> PaperAccountSource:
    return PaperAccountSource(conn, Decimal("100000"))


# --- Чистые вычисления ---------------------------------------------------------


def test_spread_bps_from_quote_computes_and_rejects_nonpositive_mid() -> None:
    quote = _quote(Decimal("99"), Decimal("101"), _NOW)
    assert spread_bps_from_quote(quote) == Decimal("200")
    with pytest.raises(ValueError, match="положительной"):
        spread_bps_from_quote(_quote(Decimal("-1"), Decimal("1"), _NOW))


def test_average_volume_prior_excludes_current_bar_and_requires_window() -> None:
    assert average_volume_prior([10, 20, 30], 3) is None  # нужно window+1
    assert average_volume_prior([10, 20, 30, 999], 3) == Decimal(20)


def test_pool_breadth_rejects_empty_and_computes_fraction() -> None:
    with pytest.raises(ValueError):
        pool_breadth({})
    pairs = {"A": (Decimal(11), Decimal(10)), "B": (Decimal(9), Decimal(10))}
    assert pool_breadth(pairs) == Decimal("0.5")


def test_index_atr_rising_none_without_two_values() -> None:
    assert index_atr_rising([], 2) is None


def test_pearson_correlation_perfect_and_degenerate() -> None:
    xs = [Decimal(1), Decimal(2), Decimal(3)]
    ys = [Decimal(2), Decimal(4), Decimal(6)]
    assert pearson_correlation(xs, ys) == Decimal(1)
    assert pearson_correlation([Decimal(1), Decimal(1)], [Decimal(2), Decimal(3)]) is None
    assert pearson_correlation([Decimal(1)], [Decimal(2)]) is None


def test_max_abs_correlation_is_zero_only_without_open_positions() -> None:
    assert max_abs_correlation_with_open(current_returns={}, open_returns={}) == Decimal(0)


def test_max_abs_correlation_raises_on_insufficient_overlap() -> None:
    now = _NOW
    current = {now: Decimal("0.01"), now + timedelta(minutes=5): Decimal("0.02")}
    other = {now + timedelta(hours=1): Decimal("0.01")}  # ни одной общей отметки времени
    with pytest.raises(InsufficientFeedDataError):
        max_abs_correlation_with_open(current_returns=current, open_returns={"GAZP": other})


def test_required_bars_grow_with_periods() -> None:
    small = _config()
    big = replace(small, ema_slow=50)
    assert required_bars_5m(big, correlation_window=3, volume_avg_window=3) > required_bars_5m(
        small, correlation_window=3, volume_avg_window=3
    )
    assert required_bars_15m(big) > required_bars_15m(small)
    assert required_bars_1h(big) > required_bars_1h(small)
    assert required_bars_index(_classifier(chaos_window=500)) > required_bars_index(_classifier())


def test_session_open_and_prev_close_rejects_non_boundary_bars() -> None:
    # Первая свеча сегодня в 10:05, а не ровно в 10:00 МСК — не доказанная граница дня.
    off_boundary = _candles_at(
        [_msk(_TODAY, time(10, 5))], start_price=Decimal("100"), step=Decimal("0")
    )
    assert session_open_and_prev_close(off_boundary) is None


def test_session_open_and_prev_close_requires_verified_previous_close() -> None:
    today_open = _candles_at(
        [_msk(_TODAY, SESSION_TRADING_START)], start_price=Decimal("100"), step=Decimal("0")
    )
    # Предыдущая "сессия" заканчивается не в 23:45 МСК (например, обрыв в середине дня).
    prev_mid_session = _candles_at(
        [_msk(_YESTERDAY, time(15, 0))], start_price=Decimal("90"), step=Decimal("0")
    )
    assert session_open_and_prev_close(prev_mid_session + today_open) is None


def test_session_open_and_prev_close_and_gap_with_verified_boundaries() -> None:
    today_open = _candles_at(
        [_msk(_TODAY, SESSION_TRADING_START)], start_price=Decimal("110"), step=Decimal("0")
    )
    prev_close = _candles_at(
        [_msk(_YESTERDAY, _LAST_5M_BAR_TIME)], start_price=Decimal("100"), step=Decimal("0")
    )
    found = session_open_and_prev_close(prev_close + today_open)
    assert found is not None
    opening, closing = found
    assert opening.open == Decimal("110")
    assert closing.close == Decimal("100")
    now_near_open = _msk(_TODAY, time(10, 5))  # внутри gap_pause_min=30 минут от открытия
    gap = compute_gap(prev_close + today_open, now=now_near_open, config=_config())
    assert gap is not None
    active, pct = gap
    assert pct == Decimal("0.1")  # |110-100|/100
    assert active is True  # больше gap_pause_pct=0.03 и внутри окна паузы от открытия

    gap_later = compute_gap(prev_close + today_open, now=_NOW, config=_config())
    assert gap_later is not None
    assert gap_later[1] == Decimal("0.1")
    assert gap_later[0] is False  # окно паузы уже давно закрыто к 22:05 МСК


# --- PaperTickFeed: happy path ---------------------------------------------------


def test_build_tick_happy_path_no_open_positions(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    tick = feed.next_paper_tick()

    assert tick is not None
    assert tick.strategy.ticker == "SBER"
    assert tick.strategy.candles_5m[-1].is_complete is True
    assert tick.strategy.short_enabled is True
    assert tick.strategy.is_session_open is True
    assert tick.market.sector == "financials"
    assert tick.market.lot_size == share.lot_size
    assert tick.market.trading_status == NORMAL_TRADING_STATUS
    assert gw.status_calls == [share.instrument_id]
    assert tick.market.max_correlation_with_open == Decimal(0)
    assert tick.account.open_positions == ()
    conn.close()


def test_status_request_failure_yields_journaled_refusal_without_second_rpc(
    tmp_path: Path,
) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    gw.fail_status.add(share.instrument_id)
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    tick = feed.next_paper_tick()

    assert tick is not None
    assert gw.status_calls == [share.instrument_id]
    assert tick.strategy.is_session_open is False
    assert tick.market.trading_status == "STATUS_UNKNOWN"
    cycle = PaperCycle(
        conn,
        starting_balance=Decimal("100000"),
        strategy_config=_config(),  # type: ignore[arg-type]
        risk_config=_risk_config(),
    )
    result = cycle.run_tick(tick)
    assert result.decision.verdict == "no_signal"
    row = conn.execute("SELECT reason, filters_failed FROM decisions").fetchone()
    assert row is not None
    assert "статус торгов недоступен" in row[0]
    assert "instrument_status_normal" in row[1]
    categories = json.loads(conn.execute("SELECT filter_categories FROM decisions").fetchone()[0])
    assert [item["status"] for item in categories["categories"][:3]] == [
        "passed",
        "passed",
        "failed",
    ]
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_non_normal_status_is_journaled_without_paper_trade(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    status = "SECURITY_TRADING_STATUS_BREAK_IN_TRADING"
    gw.status[share.instrument_id] = status
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    tick = feed.next_paper_tick()

    assert tick is not None
    assert gw.status_calls == [share.instrument_id]
    assert tick.strategy.is_session_open is False
    assert tick.market.trading_status == status
    cycle = PaperCycle(
        conn,
        starting_balance=Decimal("100000"),
        strategy_config=_config(),  # type: ignore[arg-type]
        risk_config=_risk_config(),
    )
    result = cycle.run_tick(tick)
    assert result.decision.verdict == "no_signal"
    row = conn.execute("SELECT reason, filters_failed FROM decisions").fetchone()
    assert row is not None
    assert status in row[0]
    assert "instrument_status_normal" in row[1]
    categories = json.loads(conn.execute("SELECT filter_categories FROM decisions").fetchone()[0])
    assert [item["status"] for item in categories["categories"][:3]] == [
        "passed",
        "passed",
        "failed",
    ]
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("timeframe", ["15m", "1h"])
def test_stale_ticker_higher_timeframe_history_refuses_tick(tmp_path: Path, timeframe: str) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    gw.candles[(share.instrument_id, timeframe)] = gw.candles[(share.instrument_id, timeframe)][:-5]
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    assert result.ticker == "SBER"
    assert result.stage == "higher_timeframe_stale"
    assert result.action == "stop"
    assert result.error_type == StaleDataError.__name__
    assert result.source_bar_ts is not None
    conn.close()


@pytest.mark.parametrize("timeframe", ["15m", "1h"])
def test_missing_higher_timeframe_history_is_journaled_as_insufficient(
    tmp_path: Path, timeframe: str
) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    gw.candles[(share.instrument_id, timeframe)] = []
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])
    tick = feed.next_paper_tick()
    assert tick is not None
    cycle = PaperCycle(
        conn,
        starting_balance=Decimal("100000"),
        strategy_config=_config(),  # type: ignore[arg-type]
        risk_config=_risk_config(),
    )

    result = cycle.run_tick(tick)

    assert result.decision.verdict == "no_signal"
    row = conn.execute("SELECT filters_failed FROM decisions").fetchone()
    assert row is not None and "insufficient_history" in row[0]
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_next_paper_tick_returns_none_once_pool_exhausted_without_new_bars(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])
    tick = feed.next_paper_tick()
    assert tick is not None
    feed.ack(tick)

    assert feed.next_paper_tick() is None  # тот же бар уже подтверждён
    conn.close()


def test_without_ack_same_bar_is_offered_again(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    first = feed.next_paper_tick()
    second = feed.next_paper_tick()

    assert first is not None and second is not None
    assert first.strategy.candles_5m[-1].ts_open == second.strategy.candles_5m[-1].ts_open
    conn.close()


def test_round_robin_serves_each_ticker_once_per_bar(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    _populate_ticker(gw, "GAZP")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER", "GAZP"])

    seen = set()
    for _ in range(2):
        tick = feed.next_paper_tick()
        assert tick is not None
        feed.ack(tick)
        seen.add(tick.strategy.ticker)
    assert seen == {"SBER", "GAZP"}
    assert feed.next_paper_tick() is None
    conn.close()


# --- Отказы: новый бар найден, но что-то не получено честно ---------------------


def test_new_bar_with_quote_failure_returns_stop_refusal_not_silently_skips(
    tmp_path: Path,
) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    gw.fail_quote.add(share.instrument_id)
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    assert result.ticker == "SBER"
    assert result.stage == "quote"
    assert result.action == "stop"
    assert result.error_type == GatewayError.__name__
    conn.close()


def test_stale_quote_returns_stop_refusal(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    gw.quotes[share.instrument_id] = _quote(
        Decimal("99.9"), Decimal("100.1"), _NOW - timedelta(minutes=30)
    )
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    assert result.stage == "quote"
    assert result.action == "stop"
    assert result.error_type == StaleDataError.__name__
    conn.close()


def test_stale_latest_bar_during_regular_session_returns_skip_refusal(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER", now=_NOW - timedelta(hours=2))  # бары заканчиваются давно
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])  # clock=_NOW, сессия идёт

    result = feed.next_paper_tick()

    # Старый бар во время ожидаемой обычной сессии — не легальное «ничего не нашлось»,
    # а восстановимый отказ.
    assert isinstance(result, SourceRefusal)
    assert result.ticker == "SBER"
    assert result.stage == "bar_stale"
    assert result.action == "skip"
    assert result.error_type == StaleDataError.__name__
    assert result.source_bar_ts is not None
    conn.close()


def test_ticker_metadata_failure_before_new_bar_returns_skip_refusal_and_cursor_advances(
    tmp_path: Path,
) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    gw.fail_share.add("GAZP")  # GAZP недоступен ещё до проверки "новый ли бар"
    _populate_ticker(gw, "ROSN")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["GAZP", "SBER", "ROSN"])

    first = feed.next_paper_tick()
    assert isinstance(first, SourceRefusal)
    assert first.ticker == "GAZP"
    assert first.stage == "ticker_snapshot"
    assert first.action == "skip"
    assert first.error_type == GatewayError.__name__
    assert first.source_bar_ts is None

    # Курсор уже сдвинут: следующий опрос обслужит оставшийся тикер пула.
    second = feed.next_paper_tick()
    assert second is not None and not isinstance(second, SourceRefusal)
    assert second.strategy.ticker in ("SBER", "ROSN")
    conn.close()


def test_ticker_candles_history_gateway_error_returns_skip_refusal(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    gw.fail_candles.add(share.instrument_id)
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    assert result.ticker == "SBER"
    assert result.stage == "ticker_snapshot"
    assert result.action == "skip"
    assert result.error_type == GatewayError.__name__
    assert result.source_bar_ts is None
    conn.close()


def test_ticker_invalid_ohlcv_returns_skip_refusal_not_a_crash(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    candles_5m = gw.candles[(share.instrument_id, "5m")]
    gw.candles[(share.instrument_id, "5m")] = candles_5m + candles_5m[-1:]  # дубль ts_open
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    assert result.ticker == "SBER"
    assert result.stage == "ticker_history_invalid"
    assert result.action == "skip"
    assert result.error_type == "ValueError"
    assert result.source_bar_ts is None
    conn.close()


def test_empty_5m_history_during_regular_session_returns_skip_refusal(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    gw.candles[(share.instrument_id, "5m")] = []
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])  # clock=_NOW: 22:05 МСК, сессия идёт

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    assert result.ticker == "SBER"
    assert result.stage == "bar_empty"
    assert result.action == "skip"
    assert result.source_bar_ts is None
    conn.close()


def test_empty_5m_history_off_session_is_not_a_refusal(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    gw.candles[(share.instrument_id, "5m")] = []
    conn = _db(tmp_path)
    weekend_now = datetime(2026, 2, 28, 12, 0, tzinfo=UTC)  # суббота, нет обычной сессии
    feed = _feed(gw, _account_source(conn), approved=["SBER"], clock=weekend_now)

    assert feed.next_paper_tick() is None  # легальное отсутствие истории вне сессии, не отказ
    conn.close()


# --- Пауза (не исключение) при неполном пуле/индексе -----------------------------


def test_partial_breadth_pauses_with_market_regime_chaos_label(tmp_path: Path) -> None:
    gw = FakeGateway()
    share_a = _populate_ticker(gw, "SBER")
    _populate_ticker(gw, "GAZP")
    gw.fail_candles.add("GAZP_ID")  # GAZP недоступен для breadth (но не для текущего тикера)
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER", "GAZP"])

    tick = feed.next_paper_tick()
    assert tick is not None
    assert tick.strategy.ticker == "SBER"
    mf = tick.strategy.market_filter
    assert mf is not None
    assert mf.paused is True
    assert mf.breadth is None
    del share_a
    conn.close()


def test_partial_breadth_pauses_on_other_tickers_invalid_ohlcv(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    _populate_ticker(gw, "GAZP")
    gazp_5m = gw.candles[("GAZP_ID", "5m")]
    gw.candles[("GAZP_ID", "5m")] = gazp_5m + gazp_5m[-1:]
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER", "GAZP"])

    tick = feed.next_paper_tick()

    assert tick is not None and not isinstance(tick, SourceRefusal)
    assert tick.strategy.ticker == "SBER"
    mf = tick.strategy.market_filter
    assert mf is not None
    assert mf.paused is True
    assert mf.breadth is None
    conn.close()


def test_breadth_rejects_other_ticker_from_different_closed_bar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    _populate_ticker(gw, "GAZP")
    gw.candles[("GAZP_ID", "5m")] = gw.candles[("GAZP_ID", "5m")][:-2]
    gw.candles[("IMOEX_ID", "15m")] = _candles_at(
        _session_grid(_TODAY, end=_NOW.astimezone(MSK).time(), minutes=15),
        start_price=Decimal("3000"),
        step=Decimal("1"),
    )
    classifier = _classifier()
    monkeypatch.setattr(
        classifier, "evaluate", lambda _candles: RegimeBar(Decimal("0.2"), Decimal("0.5"), "normal")
    )
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER", "GAZP"], classifier=classifier)

    tick = feed.next_paper_tick()

    assert tick is not None
    market = tick.strategy.market_filter
    assert market is not None and market.paused
    assert market.breadth is None
    assert market.market_regime == "normal"
    conn.close()


def test_breadth_pauses_when_current_ticker_has_a_gap_in_5m_history(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _populate_ticker(gw, "SBER")
    candles = gw.candles[(share.instrument_id, "5m")]
    gw.candles[(share.instrument_id, "5m")] = candles[:-2] + candles[-1:]
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    tick = feed.next_paper_tick()

    assert tick is not None
    market = tick.strategy.market_filter
    assert market is not None
    assert market.paused is True
    assert market.breadth is None
    conn.close()


def test_stale_index_cannot_allow_trade_even_when_classifier_says_normal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    gw.candles[("IMOEX_ID", "15m")] = _candles_at(
        _session_grid(_TODAY, end=time(18, 0), minutes=15),
        start_price=Decimal("3000"),
        step=Decimal("1"),
    )
    classifier = _classifier()
    monkeypatch.setattr(
        classifier, "evaluate", lambda _candles: RegimeBar(Decimal("0.2"), Decimal("0.5"), "normal")
    )
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"], classifier=classifier)

    tick = feed.next_paper_tick()

    assert tick is not None
    market = tick.strategy.market_filter
    assert market is not None and market.paused
    assert market.market_regime == "chaos"
    assert market.chaos_score is None
    conn.close()


def test_index_unavailable_gives_chaos_label_with_unknown_numbers(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"], index_id="MISSING_INDEX")
    # index_id не заполнен в gw.candles -> get_candles_history вернёт пустой список

    tick = feed.next_paper_tick()
    assert tick is not None
    mf = tick.strategy.market_filter
    assert mf is not None
    assert mf.paused is True
    assert mf.market_regime == "chaos"
    assert mf.chaos_score is None
    assert mf.chaos_threshold is None
    conn.close()


def test_index_gateway_error_gives_chaos_label_not_a_refusal(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    gw.fail_candles.add("IMOEX_ID")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    tick = feed.next_paper_tick()

    assert tick is not None  # GatewayError индекса для КАНДИДАТА не отказ, а chaos-пауза
    mf = tick.strategy.market_filter
    assert mf is not None
    assert mf.paused is True
    assert mf.market_regime == "chaos"
    conn.close()


def test_index_invalid_closed_history_gives_chaos_not_a_crash(tmp_path: Path) -> None:
    # docs/03_STRATEGY.md §«хаос» (111-118): битая история индекса (`ValueError` из
    # `_closed` из-за дублирующегося ts_open) — тоже fail-closed chaos, не краш и не `SourceRefusal`
    # кандидата (это ошибка индекса, не кандидата).
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    index_grid = _session_grid(_TODAY, end=_NOW.astimezone(MSK).time(), minutes=15)
    broken_index = _candles_at(index_grid, start_price=Decimal("3000"), step=Decimal("1"))
    gw.candles[("IMOEX_ID", "15m")] = broken_index + broken_index[-1:]  # дубль ts_open
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    tick = feed.next_paper_tick()

    assert tick is not None
    mf = tick.strategy.market_filter
    assert mf is not None
    assert mf.paused is True
    assert mf.market_regime == "chaos"
    conn.close()


def test_insufficient_index_history_is_chaos_via_real_classifier(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    index_grid = _session_grid(_TODAY, end=_NOW.astimezone(MSK).time(), minutes=15)[:1]
    gw.candles[("IMOEX_ID", "15m")] = _candles_at(
        index_grid, start_price=Decimal("3000"), step=Decimal("1")
    )
    conn = _db(tmp_path)
    # chaos_window нарочно большой — одного бара всегда не хватит (fail-closed классификатора).
    feed = _feed(
        gw,
        _account_source(conn),
        approved=["SBER"],
        classifier=_classifier(chaos_window=50, period=14),
    )

    tick = feed.next_paper_tick()
    assert tick is not None
    mf = tick.strategy.market_filter
    assert mf is not None
    assert mf.market_regime == "chaos"
    assert mf.paused is True
    conn.close()


# --- Снимок счёта: открытые позиции, корреляция, пересчёт по бакету --------------


def test_open_position_outside_pool_is_fetched_and_correlated(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER", start_price=Decimal("100"), step=Decimal("1"))
    # LKOH открыт в портфеле, но не входит в approved_tickers.
    _populate_ticker(gw, "LKOH", start_price=Decimal("100"), step=Decimal("1"))
    conn = _db(tmp_path)
    account_source = _account_source(conn)
    account_source.snapshot(_NOW, {}, {}, {})  # устанавливает якорь starting_equity до сделок
    Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).open_position(
        ticker="LKOH",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_NOW,
        stop=Decimal("90"),
    )
    feed = _feed(gw, account_source, approved=["SBER"])

    tick = feed.next_paper_tick()

    assert tick is not None
    assert tick.account.open_tickers == frozenset({"LKOH"})
    # Идентичный профиль цены (та же start_price/step) -> корреляция доходностей максимальна.
    assert tick.market.max_correlation_with_open == Decimal(1)
    conn.close()


def test_stale_open_position_price_stops_tick_with_refusal_before_journal(
    tmp_path: Path,
) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    _populate_ticker(gw, "GAZP")
    gw.candles[("SBER_ID", "5m")] = gw.candles[("SBER_ID", "5m")][:-1]
    conn = _db(tmp_path)
    account_source = _account_source(conn)
    account_source.snapshot(_NOW, {}, {}, {})
    Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).open_position(
        ticker="SBER",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_NOW,
        stop=Decimal("90"),
    )
    feed = _feed(gw, account_source, approved=["GAZP"])

    result = feed.next_paper_tick()

    assert isinstance(result, SourceRefusal)
    assert result.ticker == "GAZP"
    assert result.stage == "account_snapshot"
    assert result.action == "stop"
    assert result.error_type == InsufficientFeedDataError.__name__
    # Фид ничего не пишет в journal/БД — отказ остаётся без строки решения.
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    conn.close()


def test_account_snapshot_is_rebuilt_after_fill_within_same_bucket(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    _populate_ticker(gw, "GAZP")
    conn = _db(tmp_path)
    account_source = _account_source(conn)
    feed = _feed(gw, account_source, approved=["SBER", "GAZP"])

    first = feed.next_paper_tick()
    assert first is not None
    assert first.account.open_tickers == frozenset()
    filled_ticker = first.strategy.ticker
    filled_price = first.strategy.candles_5m[-1].close
    feed.ack(first)

    # Имитируем paper fill по первому тикеру в этом же 5m-бакете без
    # нового опроса рынка (часы не двигаются).
    Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).open_position(
        ticker=filled_ticker,
        side="long",
        lots=1,
        lot_size=10,
        price=filled_price,
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_NOW,
        stop=filled_price - Decimal("10"),
    )

    second = feed.next_paper_tick()
    assert second is not None
    assert second.strategy.ticker != filled_ticker  # round robin: другой тикер того же бакета
    assert second.account.open_tickers == frozenset({filled_ticker})
    conn.close()


# --- Интеграция с PaperCycle: журналируется отказ по chaos ----------------------


def test_tick_flows_through_papercycle_and_is_journaled(tmp_path: Path) -> None:
    gw = FakeGateway()
    _populate_ticker(gw, "SBER")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"], index_id="MISSING_INDEX")
    cycle = PaperCycle(
        conn,
        starting_balance=Decimal("100000"),
        strategy_config=_config(),  # type: ignore[arg-type]
        risk_config=_risk_config(),
    )

    tick = feed.next_paper_tick()
    assert tick is not None
    result = cycle.run_tick(tick)
    feed.ack(tick)

    assert result.decision.verdict == "no_signal"
    row = conn.execute("SELECT indicators FROM decisions").fetchone()
    assert row is not None
    assert "chaos" in row[0]
    conn.close()


def _risk_config(**overrides: object) -> RiskConfig:
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


# --- daily_volume_lots: только подтверждённые полные сессии ----------------------


def test_daily_volume_lots_excludes_incomplete_today_and_sums_full_sessions(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _share("SBER")
    gw.shares["SBER"] = share
    iid = share.instrument_id

    earlier_grid = _session_grid(_EARLIER, end=SESSION_CLOSING_AUCTION_START)
    yesterday_grid = _session_grid(_YESTERDAY, end=SESSION_CLOSING_AUCTION_START)
    today_partial_grid = _session_grid(_TODAY, end=_NOW.astimezone(MSK).time())

    all_candles = (
        _candles_at(earlier_grid, start_price=Decimal("100"), step=Decimal("0"), base_volume=10)
        + _candles_at(yesterday_grid, start_price=Decimal("100"), step=Decimal("0"), base_volume=20)
        + _candles_at(
            today_partial_grid, start_price=Decimal("100"), step=Decimal("0"), base_volume=9999
        )
    )
    gw.candles[(iid, "5m")] = all_candles
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    result = feed.daily_volume_lots(["SBER"], sessions=2)

    assert len(result["SBER"]) == 2
    expected_earlier = sum(10 + i for i in range(len(earlier_grid)))
    expected_yesterday = sum(20 + i for i in range(len(yesterday_grid)))
    assert result["SBER"] == [expected_earlier, expected_yesterday]
    conn.close()


def test_daily_volume_lots_drops_session_missing_open_boundary(tmp_path: Path) -> None:
    gw = FakeGateway()
    share = _share("SBER")
    gw.shares["SBER"] = share
    iid = share.instrument_id
    # Пятничная сессия начинается в 10:05, а не 10:00 МСК — не подтверждённая граница.
    broken_grid = _session_grid(_YESTERDAY, end=SESSION_CLOSING_AUCTION_START)[1:]
    gw.candles[(iid, "5m")] = _candles_at(
        broken_grid, start_price=Decimal("100"), step=Decimal("0")
    )
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    result = feed.daily_volume_lots(["SBER"], sessions=1)

    assert result["SBER"] == []  # сессия не признана полной -> не попадает в выдачу


def test_daily_volume_lots_rejects_internal_gap_despite_complete_boundaries(
    tmp_path: Path,
) -> None:
    gw = FakeGateway()
    share = _share("SBER")
    gw.shares["SBER"] = share
    grid = _session_grid(_YESTERDAY, end=SESSION_CLOSING_AUCTION_START)
    assert len(grid) > 3
    gap_grid = grid[:12] + grid[13:]
    gw.candles[(share.instrument_id, "5m")] = _candles_at(
        gap_grid, start_price=Decimal("100"), step=Decimal("0")
    )
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])

    assert feed.daily_volume_lots(["SBER"], sessions=1) == {"SBER": []}
    conn.close()


def test_daily_volume_lots_metadata_failure_stops_collection(tmp_path: Path) -> None:
    gw = FakeGateway()
    gw.fail_share.add("UNKNOWN")
    conn = _db(tmp_path)
    feed = _feed(gw, _account_source(conn), approved=["SBER"])
    gw.shares["SBER"] = _share("SBER")

    with pytest.raises(GatewayError):
        feed.daily_volume_lots(["UNKNOWN"], sessions=1)
    conn.close()


# --- Конструктор: явные допуски свежести и lookback -------------------------------


def test_constructor_rejects_lookback_below_required_minimum(tmp_path: Path) -> None:
    gw = FakeGateway()
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="lookback_5m"):
        PaperTickFeed(
            gateway=gw,  # type: ignore[arg-type]
            account_source=_account_source(conn),
            regime_classifier=_classifier(),
            strategy_config=_config(),  # type: ignore[arg-type]
            approved_tickers=["SBER"],
            index_instrument_id="IMOEX_ID",
            clock=lambda: _NOW,
            max_bar_staleness=timedelta(minutes=15),
            max_quote_staleness=timedelta(minutes=5),
            lookback_5m=timedelta(minutes=1),
        )
    conn.close()


def test_constructor_requires_positive_staleness_budgets(tmp_path: Path) -> None:
    gw = FakeGateway()
    conn = _db(tmp_path)
    with pytest.raises(ValueError, match="max_bar_staleness"):
        PaperTickFeed(
            gateway=gw,  # type: ignore[arg-type]
            account_source=_account_source(conn),
            regime_classifier=_classifier(),
            strategy_config=_config(),  # type: ignore[arg-type]
            approved_tickers=["SBER"],
            index_instrument_id="IMOEX_ID",
            clock=lambda: _NOW,
            max_bar_staleness=timedelta(0),
            max_quote_staleness=timedelta(minutes=5),
        )
    conn.close()
