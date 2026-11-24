"""Приёмочные границы классификатора и long/short-правил M7 без сети."""

from dataclasses import fields, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_paper_cycle_integration import _cycle, _inputs, _tick
from tests.test_strategy import (
    _config,
    _long_snapshot,
    _ranging_long_snapshot,
    _ranging_short_snapshot,
    _short_snapshot,
)
from tests.test_strategy_filters_integration import _candles, _real_candles
from trading_bot import database, strategy
from trading_bot.config import Settings, _fallback_settings
from trading_bot.risk_manager import Side

_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def _settings_config(settings: Settings) -> strategy.StrategyConfig:
    return strategy.StrategyConfig(
        **{item.name: getattr(settings, item.name) for item in fields(strategy.StrategyConfig)}
    )


def _evaluate_snapshot(
    snap: strategy._Snapshot, config: strategy.StrategyConfig
) -> strategy.Decision:
    with patch.object(strategy, "compute_snapshot", return_value=snap):
        return strategy.evaluate(
            ticker="SBER",
            candles_5m=[],
            candles_15m=[],
            candles_1h=[],
            config=config,
            now=_NOW,
            is_session_open=True,
            session_reason="ok",
            short_enabled=True,
            session_guard_active=False,
        )


@pytest.mark.parametrize(
    ("adx", "volatility", "expected"),
    [
        ("19.999", "0.05", "ranging"),
        ("20", "0.05", "transitional"),
        ("25", "0.05", "transitional"),
        ("25.001", "0.05", "trending"),
        ("40", "0.050001", "volatile"),
        ("40.001", "0.05", "trending"),
        ("40.001", "0.050001", "chaos"),
        ("10", "0.050001", "volatile"),
    ],
)
@pytest.mark.parametrize("direction", [1, -1])
def test_classifier_strict_boundaries(
    adx: str, volatility: str, expected: str, direction: int
) -> None:
    assert (
        strategy.classify_regime(
            adx_value=Decimal(adx),
            atr_ratio=Decimal(volatility),
            ema_fast=Decimal(100 + direction * 2),
            ema_mid=Decimal(100 + direction),
            ema_slow=Decimal(100),
            config=_config(),
        )
        == expected
    )


def test_classifier_uses_custom_thresholds_and_requires_strict_ema_alignment() -> None:
    config = _config(
        adx_range=Decimal("10"),
        adx_trend=Decimal("30"),
        adx_chaos=Decimal("50"),
        max_atr_ratio=Decimal("0.08"),
    )
    for adx, ratio, expected in [
        ("9", "0.06", "ranging"),
        ("20", "0.06", "transitional"),
        ("31", "0.06", "trending"),
        ("45", "0.09", "volatile"),
        ("51", "0.09", "chaos"),
    ]:
        assert (
            strategy.classify_regime(
                adx_value=Decimal(adx),
                atr_ratio=Decimal(ratio),
                ema_fast=Decimal(3),
                ema_mid=Decimal(2),
                ema_slow=Decimal(1),
                config=config,
            )
            == expected
        )
    for emas in [(3, 3, 1), (3, 1, 2), (1, 1, 1)]:
        assert (
            strategy.classify_regime(
                adx_value=Decimal(35),
                atr_ratio=Decimal("0.01"),
                ema_fast=Decimal(emas[0]),
                ema_mid=Decimal(emas[1]),
                ema_slow=Decimal(emas[2]),
                config=config,
            )
            == "transitional"
        )


@pytest.mark.parametrize(
    "rsi, allowed", [("65", False), ("69.999", False), ("70", False), ("70.001", True)]
)
def test_default_ranging_short_threshold_matches_spec(
    rsi: str, allowed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TBOT_RSI_OVERBOUGHT", raising=False)
    config = _settings_config(Settings(_env_file=None))
    decision = _evaluate_snapshot(replace(_ranging_short_snapshot(), rsi_5m=Decimal(rsi)), config)
    assert (decision.verdict == "signal") is allowed
    assert decision.side == ("short" if allowed else None)


def test_fallback_ranging_threshold_is_separate_from_trending() -> None:
    settings = _fallback_settings()
    assert settings.rsi_overbought == Decimal(70)
    assert (settings.rsi_trend_low, settings.rsi_trend_high) == (Decimal(30), Decimal(65))


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize(
    "condition",
    [
        "ema_cross",
        "rsi_not_overheated",
        "momentum",
        "price_fair_vs_vwap",
        "volume_confirmation",
        "multi_timeframe_alignment",
    ],
)
def test_each_trending_condition_blocks_evaluate_in_both_directions(
    side: Side, condition: str
) -> None:
    snap = _long_snapshot() if side == "long" else _short_snapshot()
    assert _evaluate_snapshot(snap, _config()).side == side
    if condition == "ema_cross":
        snap = replace(snap, ema9_cross_up=False, ema9_cross_down=False)
    elif condition == "rsi_not_overheated":
        snap = replace(snap, rsi_15m=Decimal(90) if side == "long" else Decimal(10))
    elif condition == "momentum":
        snap = replace(snap, macd_hist_15m=snap.macd_hist_15m_prev)
    elif condition == "price_fair_vs_vwap":
        snap = replace(snap, vwap_5m=snap.close_5m)
    elif condition == "volume_confirmation":
        snap = replace(snap, volume_spike_5m=False)
    else:
        snap = replace(snap, ema_fast_1h=snap.ema_mid_1h)
    failed = strategy.trending_conditions(side, snap, _config())
    assert [item.name for item in failed if not item.passed] == [condition]
    decision = _evaluate_snapshot(snap, _config())
    assert decision.verdict == "no_signal"
    assert decision.signal is None
    assert any(condition in reason for reason in decision.filters_failed)


@pytest.mark.parametrize("side", ["long", "short"])
def test_custom_rsi_thresholds_control_trending_and_ranging(side: Side) -> None:
    trend = _long_snapshot() if side == "long" else _short_snapshot()
    config = _config(rsi_trend_low=Decimal(40), rsi_trend_high=Decimal(55))
    low, high = (Decimal(40), Decimal(55)) if side == "long" else (Decimal(45), Decimal(60))
    for rsi, allowed in [
        (low, True),
        (high, True),
        (low - Decimal("0.001"), False),
        (high + Decimal("0.001"), False),
    ]:
        assert (
            _evaluate_snapshot(replace(trend, rsi_15m=rsi), config).verdict == "signal"
        ) is allowed
    ranging = _ranging_long_snapshot() if side == "long" else _ranging_short_snapshot()
    config = _config(rsi_oversold=Decimal(20), rsi_overbought=Decimal(80))
    assert _evaluate_snapshot(ranging, config).signal is None
    rsi = Decimal(19) if side == "long" else Decimal(81)
    assert _evaluate_snapshot(replace(ranging, rsi_5m=rsi), config).side == side


@pytest.mark.parametrize("side", ["long", "short"])
@pytest.mark.parametrize("condition", ["band_touch", "rsi_extreme", "reversal_bar"])
def test_each_ranging_condition_blocks_both_directions(side: Side, condition: str) -> None:
    snap = _ranging_long_snapshot() if side == "long" else _ranging_short_snapshot()
    assert _evaluate_snapshot(snap, _config()).side == side
    if condition == "band_touch":
        snap = replace(snap, bb_lower_5m=snap.close_5m - 1, bb_upper_5m=snap.close_5m + 1)
    elif condition == "rsi_extreme":
        snap = replace(snap, rsi_5m=Decimal(30) if side == "long" else Decimal(70))
    else:
        snap = replace(snap, open_5m=snap.close_5m)
    checks = strategy.ranging_conditions(side, snap, _config())
    assert [item.name for item in checks if not item.passed] == [condition]
    decision = _evaluate_snapshot(snap, _config())
    assert decision.verdict == "no_signal" and decision.signal is None
    assert any(condition in reason for reason in decision.filters_failed)


def test_real_closed_candles_produce_trending_short_without_snapshot_mock() -> None:
    candles = _real_candles()
    mirrored = [
        [
            replace(
                c,
                open=Decimal(300) - c.open,
                high=Decimal(300) - c.low,
                low=Decimal(300) - c.high,
                close=Decimal(300) - c.close,
            )
            for c in timeframe
        ]
        for timeframe in candles
    ]
    decision = strategy.evaluate(
        ticker="SBER",
        candles_5m=mirrored[0],
        candles_15m=mirrored[1],
        candles_1h=mirrored[2],
        config=_config(),
        now=_NOW,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.regime == "trending"
    assert decision.side == "short", decision.filters_failed
    assert decision.signal is not None


def test_real_chaos_candles_are_journaled_without_fill_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "chaos.db"
    inputs = _inputs()
    chaos_15m = [
        replace(c, high=c.close + Decimal(10), low=c.close - Decimal(10))
        for c in _candles([Decimal(100) + Decimal(i) for i in range(80)], minutes=15)
    ]
    tick = _tick(inputs=replace(inputs, candles_15m=chaos_15m))
    conn = database.connect(path)
    with patch("trading_bot.broker.paper.PaperBroker.fill_market_order") as fill:
        result = _cycle(conn).run_tick(tick)
        assert result.decision is not None
        assert result.decision.regime == "chaos"
        assert result.decision.verdict == "no_signal"
        assert result.fill is None and result.trade_id is None
        fill.assert_not_called()
    conn.close()
    conn = database.connect(path)
    with patch.object(strategy, "compute_snapshot", side_effect=AssertionError("repeat")):
        repeated = _cycle(conn).run_tick(tick)
        assert repeated.already_processed
    assert conn.execute("SELECT regime, verdict, processing_state FROM decisions").fetchall() == [
        ("chaos", "no_signal", "complete")
    ]
    assert conn.execute("SELECT count(*) FROM trades").fetchone() == (0,)
    assert conn.execute("SELECT count(*) FROM positions").fetchone() == (0,)
    conn.close()


def test_chaos_never_reaches_entry_rules_in_1000_snapshots() -> None:
    with (
        patch.object(strategy, "trending_conditions", side_effect=AssertionError("entry in chaos")),
        patch.object(strategy, "ranging_conditions", side_effect=AssertionError("entry in chaos")),
    ):
        for index in range(1000):
            base = _long_snapshot() if index % 2 else _short_snapshot()
            snap = replace(
                base,
                adx_15m=Decimal(41) + Decimal(index % 60),
                atr_ratio_15m=Decimal("0.051") + Decimal(index) / Decimal(10000),
            )
            decision = _evaluate_snapshot(snap, _config())
            assert decision.regime == "chaos"
            assert decision.verdict == "no_signal"
            assert decision.signal is None and decision.side is None
            assert decision.filters_failed == ("regime_chaos",)
