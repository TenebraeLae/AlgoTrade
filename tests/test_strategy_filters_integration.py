"""Сквозные проверки M7 на закрытых свечах, риске и журнале решений."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from tests.test_risk_manager import _account as risk_account
from tests.test_risk_manager import _config as risk_config
from tests.test_risk_manager import _instrument as risk_instrument
from tests.test_strategy import _config as strategy_config
from trading_bot import database
from trading_bot.api_client import Candle
from trading_bot.journal import DecisionJournal
from trading_bot.risk_manager import (
    OpenPositionState,
    RiskVerdict,
    check,
    initial_stop,
    round_stop_price,
)
from trading_bot.strategy import Decision, evaluate
from trading_bot.utils import MSK, can_trade_now

_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)  # 12:00 МСК, обычная сессия


def _candles(closes: list[Decimal], *, minutes: int) -> list[Candle]:
    start = _NOW - timedelta(minutes=len(closes) * minutes)
    return [
        Candle(
            open=price - Decimal("0.1"),
            high=price + Decimal("0.5"),
            low=price - Decimal("0.5"),
            close=price,
            volume=200 if index == len(closes) - 1 else 100,
            ts_open=start + timedelta(minutes=index * minutes),
            is_complete=True,
        )
        for index, price in enumerate(closes)
    ]


def _real_candles() -> tuple[list[Candle], list[Candle], list[Candle]]:
    prices_5m = [
        (
            Decimal("100") + Decimal(index) * Decimal("0.15")
            if index < 55
            else (
                Decimal("108.1") - Decimal(index - 55) * Decimal("0.25")
                if index < 79
                else Decimal("120")
            )
        )
        for index in range(80)
    ]
    prices_15m = [
        Decimal("100")
        + Decimal(index) * Decimal("0.4")
        + Decimal(index % 5 - 2) * Decimal("0.8")
        - (Decimal("0.3") if index == 78 else Decimal(0))
        for index in range(79)
    ]
    prices_1h = [Decimal("100") + Decimal(index) * Decimal("0.5") for index in range(80)]
    return (
        _candles(prices_5m, minutes=5),
        _candles(prices_15m, minutes=15),
        _candles(prices_1h, minutes=60),
    )


def _evaluate_real(
    *,
    is_session_open: bool = True,
    session_reason: str = "ok",
    session_guard_active: bool = False,
    gap_result: tuple[bool, Decimal] | None = None,
) -> Decision:
    candles_5m, candles_15m, candles_1h = _real_candles()
    return evaluate(
        ticker="SBER",
        candles_5m=candles_5m,
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        config=strategy_config(),
        now=_NOW,
        is_session_open=is_session_open,
        session_reason=session_reason,
        session_guard_active=session_guard_active,
        short_enabled=True,
        spread_bps=Decimal("10"),
        gap_result=gap_result,
    )


def _evaluate_volatile_real() -> Decision:
    candles_5m, candles_15m, candles_1h = _real_candles()
    volatile_15m = [
        replace(candle, high=candle.high + Decimal("10"), low=candle.low - Decimal("10"))
        for candle in candles_15m
    ]
    return evaluate(
        ticker="SBER",
        candles_5m=candles_5m,
        candles_15m=volatile_15m,
        candles_1h=candles_1h,
        config=strategy_config(),
        now=_NOW,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
        spread_bps=Decimal("10"),
    )


def _ranging_candles(*, side: str = "long") -> tuple[list[Candle], list[Candle], list[Candle]]:
    """Реальные свечи флэта с касанием BB и закрытым разворотом в нужную сторону."""
    alternating = [Decimal("100.05") if index % 2 else Decimal("99.95") for index in range(60)]
    candles_5m = _candles(alternating + [Decimal("95"), Decimal("94.5")], minutes=5)
    candles_5m[-1] = replace(
        candles_5m[-1], open=Decimal("94.2"), high=Decimal("94.8"), low=Decimal("94.03")
    )
    flat_prices = [Decimal("100.1") if index % 2 else Decimal("99.9") for index in range(60)]
    candles = (
        candles_5m,
        _candles(flat_prices, minutes=15),
        _candles(flat_prices, minutes=60),
    )
    if side == "long":
        return candles

    def mirror(timeframe: list[Candle]) -> list[Candle]:
        return [
            replace(
                candle,
                open=Decimal("200") - candle.open,
                high=Decimal("200") - candle.low,
                low=Decimal("200") - candle.high,
                close=Decimal("200") - candle.close,
            )
            for candle in timeframe
        ]

    return mirror(candles[0]), mirror(candles[1]), mirror(candles[2])


def _evaluate_ranging_real(*, side: str = "long") -> Decision:
    candles_5m, candles_15m, candles_1h = _ranging_candles(side=side)
    return evaluate(
        ticker="SBER",
        candles_5m=candles_5m,
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        config=strategy_config(),
        now=_NOW,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
        spread_bps=Decimal("10"),
    )


def test_closed_candle_fixture_creates_real_trending_signal() -> None:
    decision = _evaluate_real()
    assert decision.verdict == "signal", (
        decision.regime,
        decision.filters_failed,
        decision.indicator_snapshot,
    )
    assert decision.signal is not None
    assert decision.signal.side == "long"
    assert decision.signal.stop_price is None
    assert decision.signal.take_price is None
    assert "session_guard" in decision.filters_passed


def test_no_lookahead_evaluate_ignores_next_unclosed_candles() -> None:
    candles_5m, candles_15m, candles_1h = _real_candles()
    baseline = _evaluate_real()
    assert baseline.verdict == "signal"

    def future_candle(last: Candle) -> Candle:
        return replace(
            last,
            ts_open=_NOW,
            open=Decimal("500"),
            high=Decimal("600"),
            low=Decimal("1"),
            close=Decimal("550"),
            volume=1_000_000,
            is_complete=False,
        )

    shifted = evaluate(
        ticker="SBER",
        candles_5m=candles_5m + [future_candle(candles_5m[-1])],
        candles_15m=candles_15m + [future_candle(candles_15m[-1])],
        candles_1h=candles_1h + [future_candle(candles_1h[-1])],
        config=strategy_config(),
        now=_NOW,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
        spread_bps=Decimal("10"),
    )
    assert shifted == baseline


def _check_real_signal(
    decision: Decision,
    *,
    account_overrides: dict[str, object] | None = None,
    instrument_overrides: dict[str, object] | None = None,
) -> RiskVerdict:
    assert decision.signal is not None
    candles_5m = (
        _ranging_candles(side=decision.signal.side)[0]
        if decision.regime == "ranging"
        else _real_candles()[0]
    )
    entry = candles_5m[-1].close
    atr = Decimal(decision.indicator_snapshot["atr"])
    config = risk_config()
    raw_stop = decision.signal.stop_price
    if raw_stop is None:
        raw_stop = initial_stop(
            side=decision.signal.side,
            entry_price=entry,
            atr=atr,
            atr_stop_mult=config.atr_stop_mult,
            price_increment=Decimal("0.01"),
        )
    effective_stop = round_stop_price(
        side=decision.signal.side,
        stop_price=raw_stop,
        price_increment=Decimal("0.1") if decision.regime == "ranging" else Decimal("0.01"),
    )
    instrument = replace(
        risk_instrument(
            price=entry,
            atr=atr,
            regime=decision.regime,
            stop_price=effective_stop,
            min_price_increment=Decimal("0.1") if decision.regime == "ranging" else Decimal("0.01"),
        ),
        **(instrument_overrides or {}),
    )
    return check(
        side=decision.signal.side,
        account=risk_account(**(account_overrides or {})),
        instrument=instrument,
        config=config,
    )


def test_real_signal_survives_all_twelve_risk_checks() -> None:
    verdict = _check_real_signal(_evaluate_real())
    assert verdict.allowed, [(item.name, item.reason) for item in verdict.checks if not item.passed]
    assert verdict.max_lots > 0


def test_ranging_reversal_uses_real_closed_candles_and_candle_stop() -> None:
    decision = _evaluate_ranging_real()
    assert decision.verdict == "signal", decision.filters_failed
    assert decision.regime == "ranging"
    assert decision.signal is not None
    assert decision.signal.side == "long"
    assert decision.signal.stop_price == Decimal("94.03")
    assert decision.signal.take_price == Decimal("99.475")
    assert Decimal(decision.indicator_snapshot["prev_close_5m"]) < Decimal(
        decision.indicator_snapshot["bb_middle_prev"]
    )
    verdict = _check_real_signal(decision)
    assert verdict.allowed
    assert round_stop_price(
        side="long", stop_price=decision.signal.stop_price, price_increment=Decimal("0.1")
    ) == Decimal("94.0")
    expected = check(
        side="long",
        account=risk_account(),
        config=risk_config(),
        instrument=risk_instrument(
            price=Decimal("94.5"),
            atr=Decimal("1"),
            regime="ranging",
            stop_price=Decimal("94.0"),
            min_price_increment=Decimal("0.1"),
        ),
    )
    atr_stop = check(
        side="long",
        account=risk_account(),
        config=risk_config(),
        instrument=risk_instrument(
            price=Decimal("94.5"),
            atr=Decimal("1"),
            regime="ranging",
            stop_price=Decimal("93.0"),
            min_price_increment=Decimal("0.1"),
        ),
    )
    assert verdict.effective_risk_pct == expected.effective_risk_pct
    assert verdict.effective_risk_pct != atr_stop.effective_risk_pct


def test_ranging_short_uses_real_closed_candles_and_rounded_stop() -> None:
    decision = _evaluate_ranging_real(side="short")
    assert decision.verdict == "signal", decision.filters_failed
    assert decision.regime == "ranging"
    assert decision.signal is not None
    assert decision.signal.side == "short"
    assert decision.signal.stop_price == Decimal("105.97")
    assert decision.signal.take_price == Decimal("100.525")
    assert round_stop_price(
        side="short", stop_price=decision.signal.stop_price, price_increment=Decimal("0.1")
    ) == Decimal("106.0")
    assert _check_real_signal(decision).allowed


def test_ranging_signal_requires_previous_close_below_previous_middle() -> None:
    candles_5m, candles_15m, candles_1h = _ranging_candles()
    candles_5m[-2] = replace(candles_5m[-2], close=Decimal("101"), high=Decimal("101.5"))
    decision = evaluate(
        ticker="SBER",
        candles_5m=candles_5m,
        candles_15m=candles_15m,
        candles_1h=candles_1h,
        config=strategy_config(),
        now=_NOW,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert any("reversal_bar" in failure for failure in decision.filters_failed)


def test_filter_1_session_refuses_real_signal_at_risk_gate() -> None:
    verdict = _check_real_signal(
        _evaluate_real(),
        instrument_overrides={"is_session_open": False, "session_reason": "аукцион"},
    )
    assert not verdict.allowed
    assert {item.name for item in verdict.checks if not item.passed} == {"session_open"}


def test_filter_2_guard_window_blocks_before_strategy_and_risk() -> None:
    gateway = Mock()
    session = can_trade_now(
        gateway,
        instrument_id="SBER",
        now=datetime(2026, 3, 2, 10, 5, tzinfo=MSK),
        session_guard_min=10,
    )
    assert not session.allowed
    assert "защитное окно" in session.reason
    assert session.guard_active
    assert _evaluate_real(
        # Guard — отдельный отказ; session.allowed остаётся False для риска/заявки.
        is_session_open=session.allowed or session.guard_active,
        session_reason=session.reason,
        session_guard_active=session.guard_active,
    ).filters_failed == ("session_guard",)
    verdict = _check_real_signal(
        _evaluate_real(),
        instrument_overrides={"is_session_open": session.allowed, "session_reason": session.reason},
    )
    assert not verdict.allowed
    assert any(item.name == "session_open" and not item.passed for item in verdict.checks)
    gateway.get_trading_status.assert_not_called()


def test_filter_3_instrument_status_refuses_real_signal() -> None:
    verdict = _check_real_signal(
        _evaluate_real(), instrument_overrides={"trading_status": "HALTED"}
    )
    assert {item.name for item in verdict.checks if not item.passed} == {"instrument_status_normal"}


def test_filter_4_chaos_refuses_real_signal_at_risk_gate() -> None:
    verdict = _check_real_signal(_evaluate_real(), instrument_overrides={"regime": "chaos"})
    assert not verdict.allowed
    assert "chaos" in next(
        item.reason for item in verdict.checks if item.name == "volatility_within_limit"
    )


def test_filter_4_volatile_is_refused_from_real_closed_candles() -> None:
    decision = _evaluate_volatile_real()
    assert decision.regime == "volatile"
    assert decision.verdict == "no_signal"
    assert decision.filters_failed == ("regime_volatile",)
    assert Decimal(decision.indicator_snapshot["atr_ratio"]) > Decimal("0.05")


def test_filter_5_gap_pause_refuses_real_closed_candle_candidate() -> None:
    decision = _evaluate_real(gap_result=(True, Decimal("0.05")))
    assert decision.verdict == "no_signal"
    assert decision.filters_failed == ("gap_pause",)
    assert decision.indicator_snapshot["gap_pct"] == "0.05"


def test_filter_6_volatility_refuses_real_signal_at_risk_gate() -> None:
    verdict = _check_real_signal(_evaluate_real(), instrument_overrides={"atr": Decimal("6.1")})
    assert {item.name for item in verdict.checks if not item.passed} == {"volatility_within_limit"}


def test_filter_7_spread_refuses_real_signal_at_risk_gate() -> None:
    verdict = _check_real_signal(
        _evaluate_real(), instrument_overrides={"spread_bps": Decimal("31")}
    )
    assert {item.name for item in verdict.checks if not item.passed} == {"spread_within_limit"}


def test_filter_8_correlation_refuses_real_signal_at_risk_gate() -> None:
    verdict = _check_real_signal(
        _evaluate_real(), instrument_overrides={"max_correlation_with_open": Decimal("0.85")}
    )
    assert {item.name for item in verdict.checks if not item.passed} == {"correlation_within_limit"}


def test_filter_9_diversification_refuses_existing_ticker() -> None:
    holding = OpenPositionState(
        ticker="SBER",
        side="long",
        lots=1,
        lot_size=10,
        stop_price=Decimal("100"),
        market_price=Decimal("120"),
    )
    verdict = _check_real_signal(
        _evaluate_real(),
        account_overrides={
            "open_positions": (holding,),
            "open_positions_count": 1,
            "open_tickers": frozenset({"SBER"}),
        },
    )
    assert {item.name for item in verdict.checks if not item.passed} == {"sector_and_ticker_limits"}


def test_filter_10_daily_limits_refuse_after_daily_stop() -> None:
    verdict = _check_real_signal(_evaluate_real(), account_overrides={"daily_stop_active": True})
    # Дневной latch блокирует и сам фильтр, и публичный сайзинг (вторая линия защиты).
    assert {item.name for item in verdict.checks if not item.passed} == {
        "daily_loss_limit_not_reached",
        "size_and_funds_sufficient",
    }


def test_real_candle_decisions_and_risk_refusal_survive_sqlite_restart(tmp_path: Path) -> None:
    trending = _evaluate_real()
    ranging_long = _evaluate_ranging_real()
    ranging_short = _evaluate_ranging_real(side="short")
    gap = _evaluate_real(gap_result=(True, Decimal("0.05")))
    closed = _evaluate_real(is_session_open=False, session_reason="вне сессии")
    volatile = _evaluate_volatile_real()
    db_path = tmp_path / "m7-decisions.db"
    conn = database.connect(db_path)
    journal = DecisionJournal(conn, mode="paper")
    decisions = (
        (trending, _check_real_signal(trending)),
        (ranging_long, _check_real_signal(ranging_long)),
        (ranging_short, _check_real_signal(ranging_short)),
        (trending, _check_real_signal(trending, instrument_overrides={"trading_status": "HALTED"})),
        (gap, None),
        (closed, None),
        (volatile, None),
    )
    ids = [journal.record(decision, risk_verdict=risk) for decision, risk in decisions]
    conn.close()

    conn = database.connect(db_path)
    rows = conn.execute(
        "SELECT id, ts, indicators, filters_passed, filters_failed, size_lots, verdict, "
        "effective_risk_pct FROM decisions ORDER BY id"
    ).fetchall()
    assert [row[0] for row in rows] == ids
    assert [row[6] for row in rows] == [
        "signal",
        "signal",
        "signal",
        "no_signal",
        "no_signal",
        "no_signal",
        "no_signal",
    ]
    assert all(row[1] == _NOW.isoformat() for row in rows)
    assert all(json.loads(row[2]) for row in rows)
    assert all(row[5] > 0 and Decimal(row[7]) > 0 for row in rows[:3])
    assert json.loads(rows[1][2])["stop_price"] == "94.03"
    assert json.loads(rows[2][2])["take_price"] == "100.525"
    assert rows[3][5:] == (0, "no_signal", "0")
    assert any(
        failure.startswith("risk:instrument_status_normal") for failure in json.loads(rows[3][4])
    )
    assert "gap_pause" in json.loads(rows[4][4])
    assert "session_open" in json.loads(rows[5][4])
    assert json.loads(rows[5][2])["ema9"] == "n/a"
    assert json.loads(rows[6][4]) == ["regime_volatile"]
    conn.close()
