"""Тесты strategy.py: режимы, условия входа, фильтры, journal (docs/03_STRATEGY.md,
docs/15_ACCEPTANCE.md M7).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_bot.api_client import Candle
from trading_bot.strategy import (
    ConditionResult,
    Decision,
    InsufficientHistoryError,
    MarketFilterResult,
    Signal,
    StrategyConfig,
    _Snapshot,
    aligned_down,
    aligned_up,
    classify_regime,
    compute_snapshot,
    evaluate,
    evaluate_market_breadth,
    gap_filter,
    ranging_conditions,
    trending_conditions,
)
from trading_bot.utils import can_trade_now, session_guard_active

_MSK = timezone(timedelta(hours=3))
_REQUIRED_SNAPSHOT_KEYS = {
    "ema9",
    "ema21",
    "ema50",
    "rsi_5m",
    "rsi_15m",
    "macd_hist",
    "bb_lower",
    "bb_upper",
    "adx",
    "atr",
    "atr_ratio",
    "vwap",
    "volume",
    "volume_avg20",
    "spread_bps",
    "gap_pct",
    "breadth",
}


def _config(**overrides: object) -> StrategyConfig:
    base = dict(
        ema_fast=9,
        ema_mid=21,
        ema_slow=50,
        rsi_period=14,
        rsi_oversold=Decimal("30"),
        rsi_overbought=Decimal("70"),
        rsi_trend_low=Decimal("30"),
        rsi_trend_high=Decimal("65"),
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        bb_period=20,
        bb_mult=Decimal("2.0"),
        atr_period=14,
        adx_period=14,
        adx_trend=Decimal("25"),
        adx_range=Decimal("20"),
        adx_chaos=Decimal("40"),
        max_atr_ratio=Decimal("0.05"),
        vol_window=20,
        gap_pause_pct=Decimal("0.03"),
        gap_pause_min=30,
        breadth_bearish=Decimal("0.30"),
        breadth_bullish=Decimal("0.70"),
        session_guard_min=10,
    )
    base.update(overrides)
    return StrategyConfig(**base)  # type: ignore[arg-type]


def _long_snapshot() -> _Snapshot:
    """Снимок, где все шесть условий trending-long выполнены (docs/03_STRATEGY.md)."""
    return _Snapshot(
        ema_fast_5m=Decimal("110"),
        ema_mid_5m=Decimal("105"),
        ema_slow_5m=Decimal("100"),
        ema_fast_15m=Decimal("110"),
        ema_mid_15m=Decimal("105"),
        ema_slow_15m=Decimal("100"),
        ema_fast_1h=Decimal("110"),
        ema_mid_1h=Decimal("105"),
        ema_slow_1h=Decimal("100"),
        rsi_5m=Decimal("50"),
        rsi_15m=Decimal("50"),
        macd_hist_15m=Decimal("2"),
        macd_hist_15m_prev=Decimal("1"),
        bb_lower_5m=Decimal("90"),
        bb_upper_5m=Decimal("110"),
        bb_middle_5m=Decimal("100"),
        bb_middle_5m_prev=Decimal("100"),
        adx_15m=Decimal("30"),
        atr_15m=Decimal("1"),
        atr_ratio_15m=Decimal("0.01"),
        vwap_5m=Decimal("108"),
        close_5m=Decimal("110"),
        close_5m_prev=Decimal("109"),
        open_5m=Decimal("108"),
        high_5m=Decimal("111"),
        low_5m=Decimal("107"),
        volume_5m=1000,
        volume_avg_5m=Decimal("500"),
        volume_spike_5m=True,
        ema9_cross_up=True,
        ema9_cross_down=False,
    )


def _short_snapshot() -> _Snapshot:
    """Зеркальный снимок для trending-short."""
    return _Snapshot(
        ema_fast_5m=Decimal("90"),
        ema_mid_5m=Decimal("95"),
        ema_slow_5m=Decimal("100"),
        ema_fast_15m=Decimal("90"),
        ema_mid_15m=Decimal("95"),
        ema_slow_15m=Decimal("100"),
        ema_fast_1h=Decimal("90"),
        ema_mid_1h=Decimal("95"),
        ema_slow_1h=Decimal("100"),
        rsi_5m=Decimal("50"),
        rsi_15m=Decimal("50"),
        macd_hist_15m=Decimal("-2"),
        macd_hist_15m_prev=Decimal("-1"),
        bb_lower_5m=Decimal("90"),
        bb_upper_5m=Decimal("110"),
        bb_middle_5m=Decimal("100"),
        bb_middle_5m_prev=Decimal("100"),
        adx_15m=Decimal("30"),
        atr_15m=Decimal("1"),
        atr_ratio_15m=Decimal("0.01"),
        vwap_5m=Decimal("92"),
        close_5m=Decimal("90"),
        close_5m_prev=Decimal("91"),
        open_5m=Decimal("92"),
        high_5m=Decimal("93"),
        low_5m=Decimal("89"),
        volume_5m=1000,
        volume_avg_5m=Decimal("500"),
        volume_spike_5m=True,
        ema9_cross_up=False,
        ema9_cross_down=True,
    )


def _ranging_long_snapshot() -> _Snapshot:
    base = _long_snapshot()
    return replace(
        base,
        close_5m=Decimal("89"),
        close_5m_prev=Decimal("99"),
        open_5m=Decimal("88"),
        high_5m=Decimal("90"),
        low_5m=Decimal("87"),
        bb_lower_5m=Decimal("90"),
        rsi_5m=Decimal("25"),
        adx_15m=Decimal("10"),
        atr_ratio_15m=Decimal("0.01"),
    )


def _ranging_short_snapshot() -> _Snapshot:
    base = _short_snapshot()
    return replace(
        base,
        close_5m=Decimal("111"),
        close_5m_prev=Decimal("101"),
        open_5m=Decimal("112"),
        high_5m=Decimal("113"),
        low_5m=Decimal("110"),
        bb_upper_5m=Decimal("110"),
        rsi_5m=Decimal("75"),
        adx_15m=Decimal("10"),
        atr_ratio_15m=Decimal("0.01"),
    )


# --- aligned_up/aligned_down -----------------------------------------------------


def test_aligned_up_true_when_strictly_descending_emas() -> None:
    assert aligned_up(ema_fast=Decimal("3"), ema_mid=Decimal("2"), ema_slow=Decimal("1"))


def test_aligned_up_false_when_not_ordered() -> None:
    assert not aligned_up(ema_fast=Decimal("1"), ema_mid=Decimal("2"), ema_slow=Decimal("3"))


def test_aligned_down_true_when_strictly_ascending_emas() -> None:
    assert aligned_down(ema_fast=Decimal("1"), ema_mid=Decimal("2"), ema_slow=Decimal("3"))


# --- classify_regime --------------------------------------------------------------


def test_classify_regime_chaos_high_adx_and_high_volatility() -> None:
    regime = classify_regime(
        adx_value=Decimal("45"),
        atr_ratio=Decimal("0.08"),
        ema_fast=Decimal("110"),
        ema_mid=Decimal("105"),
        ema_slow=Decimal("100"),
        config=_config(),
    )
    assert regime == "chaos"


def test_classify_regime_volatile_high_vol_low_adx() -> None:
    regime = classify_regime(
        adx_value=Decimal("15"),
        atr_ratio=Decimal("0.08"),
        ema_fast=Decimal("100"),
        ema_mid=Decimal("100"),
        ema_slow=Decimal("100"),
        config=_config(),
    )
    assert regime == "volatile"


def test_classify_regime_trending_up_aligned_and_adx_above_threshold() -> None:
    regime = classify_regime(
        adx_value=Decimal("30"),
        atr_ratio=Decimal("0.01"),
        ema_fast=Decimal("110"),
        ema_mid=Decimal("105"),
        ema_slow=Decimal("100"),
        config=_config(),
    )
    assert regime == "trending"


def test_classify_regime_trending_down_aligned_and_adx_above_threshold() -> None:
    regime = classify_regime(
        adx_value=Decimal("30"),
        atr_ratio=Decimal("0.01"),
        ema_fast=Decimal("90"),
        ema_mid=Decimal("95"),
        ema_slow=Decimal("100"),
        config=_config(),
    )
    assert regime == "trending"


def test_classify_regime_ranging_when_adx_below_range_threshold() -> None:
    regime = classify_regime(
        adx_value=Decimal("10"),
        atr_ratio=Decimal("0.01"),
        ema_fast=Decimal("100"),
        ema_mid=Decimal("100"),
        ema_slow=Decimal("100"),
        config=_config(),
    )
    assert regime == "ranging"


def test_classify_regime_transitional_when_adx_between_range_and_trend_unaligned() -> None:
    regime = classify_regime(
        adx_value=Decimal("22"),
        atr_ratio=Decimal("0.01"),
        ema_fast=Decimal("100"),
        ema_mid=Decimal("101"),
        ema_slow=Decimal("99"),
        config=_config(),
    )
    assert regime == "transitional"


# --- trending_conditions: базовый проход и изолированный отказ на каждом условии --


def test_trending_conditions_long_all_pass() -> None:
    conditions = trending_conditions("long", _long_snapshot(), _config())
    assert all(c.passed for c in conditions)
    assert len(conditions) == 6


def test_trending_conditions_short_all_pass() -> None:
    conditions = trending_conditions("short", _short_snapshot(), _config())
    assert all(c.passed for c in conditions)


def test_trending_condition_ema_cross_isolated_failure() -> None:
    snap = replace(_long_snapshot(), ema9_cross_up=False)
    conditions = trending_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"ema_cross"}


def test_trending_condition_rsi_isolated_failure() -> None:
    snap = replace(_long_snapshot(), rsi_15m=Decimal("90"))
    conditions = trending_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"rsi_not_overheated"}


def test_trending_condition_momentum_isolated_failure() -> None:
    snap = replace(_long_snapshot(), macd_hist_15m=Decimal("-1"))
    conditions = trending_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"momentum"}


def test_trending_condition_price_vs_vwap_isolated_failure() -> None:
    snap = replace(_long_snapshot(), close_5m=Decimal("100"), vwap_5m=Decimal("108"))
    conditions = trending_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"price_fair_vs_vwap"}


def test_trending_condition_volume_confirmation_isolated_failure() -> None:
    snap = replace(_long_snapshot(), volume_spike_5m=False)
    conditions = trending_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"volume_confirmation"}


def test_trending_condition_multi_timeframe_isolated_failure() -> None:
    snap = replace(_long_snapshot(), ema_fast_1h=Decimal("90"))
    conditions = trending_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"multi_timeframe_alignment"}


# --- ranging_conditions ------------------------------------------------------------


def test_ranging_conditions_long_all_pass() -> None:
    conditions = ranging_conditions("long", _ranging_long_snapshot(), _config())
    assert all(c.passed for c in conditions)


def test_ranging_conditions_short_all_pass() -> None:
    conditions = ranging_conditions("short", _ranging_short_snapshot(), _config())
    assert all(c.passed for c in conditions)


def test_ranging_condition_band_touch_isolated_failure() -> None:
    snap = replace(_ranging_long_snapshot(), close_5m=Decimal("95"))
    conditions = ranging_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"band_touch"}


def test_ranging_condition_rsi_extreme_isolated_failure() -> None:
    snap = replace(_ranging_long_snapshot(), rsi_5m=Decimal("50"))
    conditions = ranging_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"rsi_extreme"}


def test_ranging_condition_reversal_bar_isolated_failure() -> None:
    snap = replace(_ranging_long_snapshot(), close_5m=Decimal("87"), open_5m=Decimal("88"))
    conditions = ranging_conditions("long", snap, _config())
    failed = {c.name for c in conditions if not c.passed}
    assert failed == {"reversal_bar"}


def test_ranging_long_rejects_reversal_if_previous_close_was_above_middle_band() -> None:
    snap = replace(_ranging_long_snapshot(), close_5m_prev=Decimal("101"))

    conditions = ranging_conditions("long", snap, _config())

    assert {condition.name for condition in conditions if not condition.passed} == {"reversal_bar"}
    reversal = next(condition for condition in conditions if condition.name == "reversal_bar")
    assert "prev_close=101" in reversal.reason
    assert "prev_middle=100" in reversal.reason


def test_ranging_short_rejects_reversal_if_previous_close_was_below_middle_band() -> None:
    snap = replace(_ranging_short_snapshot(), close_5m_prev=Decimal("99"))

    conditions = ranging_conditions("short", snap, _config())

    assert {condition.name for condition in conditions if not condition.passed} == {"reversal_bar"}


# --- gap_filter --------------------------------------------------------------------


def test_gap_filter_inactive_when_gap_below_threshold() -> None:
    now = datetime(2026, 3, 2, 10, 5, tzinfo=_MSK)
    active, pct = gap_filter(
        now=now,
        session_open_price=Decimal("101"),
        prev_session_close=Decimal("100"),
        config=_config(),
    )
    assert active is False
    assert pct == Decimal("0.01")


def test_gap_filter_active_within_pause_window() -> None:
    now = datetime(2026, 3, 2, 10, 5, tzinfo=_MSK)
    active, pct = gap_filter(
        now=now,
        session_open_price=Decimal("105"),
        prev_session_close=Decimal("100"),
        config=_config(),
    )
    assert active is True
    assert pct == Decimal("0.05")


def test_gap_filter_expires_after_pause_window() -> None:
    now = datetime(2026, 3, 2, 10, 45, tzinfo=_MSK)
    active, _ = gap_filter(
        now=now,
        session_open_price=Decimal("105"),
        prev_session_close=Decimal("100"),
        config=_config(),
    )
    assert active is False


def test_gap_filter_zero_prev_close_is_safe() -> None:
    now = datetime(2026, 3, 2, 10, 5, tzinfo=_MSK)
    active, pct = gap_filter(
        now=now, session_open_price=Decimal("10"), prev_session_close=Decimal("0"), config=_config()
    )
    assert active is False
    assert pct == Decimal("0")


# --- evaluate_market_breadth --------------------------------------------------------


def test_market_breadth_neutral_allows_both_sides() -> None:
    result = evaluate_market_breadth(
        breadth=Decimal("0.5"),
        atr_imoex_rising=True,
        deviation_std=Decimal("0.01"),
        chaos_std_threshold=Decimal("0.5"),
        config=_config(),
    )
    assert result.allow_long and result.allow_short and not result.paused


def test_market_breadth_bearish_blocks_long_only() -> None:
    result = evaluate_market_breadth(
        breadth=Decimal("0.2"),
        atr_imoex_rising=True,
        deviation_std=Decimal("0.01"),
        chaos_std_threshold=Decimal("0.5"),
        config=_config(),
    )
    assert result.allow_long is False
    assert result.allow_short is True


def test_market_breadth_bullish_blocks_short_only() -> None:
    result = evaluate_market_breadth(
        breadth=Decimal("0.8"),
        atr_imoex_rising=True,
        deviation_std=Decimal("0.01"),
        chaos_std_threshold=Decimal("0.5"),
        config=_config(),
    )
    assert result.allow_short is False
    assert result.allow_long is True


def test_market_breadth_paused_when_deviation_exceeds_chaos_threshold() -> None:
    result = evaluate_market_breadth(
        breadth=Decimal("0.5"),
        atr_imoex_rising=False,
        deviation_std=Decimal("0.9"),
        chaos_std_threshold=Decimal("0.5"),
        config=_config(),
    )
    assert result.paused is True
    assert result.allow_long is False
    assert result.allow_short is False


# --- session_guard_active / can_trade_now (utils.py, docs/04_RISK.md п.2) ----------


def test_session_guard_active_right_after_open() -> None:
    now = datetime(2026, 3, 2, 10, 5, tzinfo=_MSK)
    assert session_guard_active(now=now, guard_min=10) is True


def test_session_guard_inactive_well_into_session() -> None:
    now = datetime(2026, 3, 2, 12, 0, tzinfo=_MSK)
    assert session_guard_active(now=now, guard_min=10) is False


def test_session_guard_active_right_before_closing_auction() -> None:
    now = datetime(2026, 3, 2, 23, 35, tzinfo=_MSK)
    assert session_guard_active(now=now, guard_min=10) is True


def test_session_guard_disabled_when_guard_min_zero() -> None:
    now = datetime(2026, 3, 2, 10, 0, tzinfo=_MSK)
    assert session_guard_active(now=now, guard_min=0) is False


def test_evaluate_requires_explicit_session_guard_state() -> None:
    with pytest.raises(TypeError, match="session_guard_active"):
        evaluate(
            ticker="SBER",
            candles_5m=[],
            candles_15m=[],
            candles_1h=[],
            config=_config(),
            now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
            is_session_open=True,
            session_reason="ok",
            short_enabled=True,
        )  # type: ignore[call-arg]


# --- evaluate(): фикстуры тренд/флэт/гэп/chaos/всплеск (docs/15_ACCEPTANCE.md M7) --


def _patch_snapshot(monkeypatch: pytest.MonkeyPatch, snap: _Snapshot) -> None:
    import trading_bot.strategy as strategy_module

    monkeypatch.setattr(strategy_module, "compute_snapshot", lambda **_kwargs: snap)


def test_evaluate_trend_fixture_gives_long_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot(monkeypatch, _long_snapshot())
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "signal"
    assert decision.side == "long"
    assert decision.regime == "trending"
    assert decision.signal is not None
    assert decision.signal.stop_price is None
    assert decision.signal.take_price is None
    assert decision.indicator_snapshot  # непустой snapshot — обязателен по докам


def test_evaluate_flat_fixture_gives_ranging_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot(monkeypatch, _ranging_long_snapshot())
    decision = evaluate(
        ticker="GAZP",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "signal"
    assert decision.side == "long"
    assert decision.regime == "ranging"


def test_ranging_long_signal_carries_candle_low_stop_and_middle_band_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_snapshot(monkeypatch, _ranging_long_snapshot())
    decision = evaluate(
        ticker="GAZP",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )

    assert decision.signal is not None
    assert decision.signal.stop_price == Decimal("87")
    assert decision.signal.take_price == Decimal("100")
    assert decision.indicator_snapshot["stop_price"] == "87"
    assert decision.indicator_snapshot["take_price"] == "100"


def test_ranging_short_signal_carries_candle_high_stop_and_middle_band_take(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_snapshot(monkeypatch, _ranging_short_snapshot())
    decision = evaluate(
        ticker="GAZP",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )

    assert decision.signal is not None
    assert decision.signal.stop_price == Decimal("113")
    assert decision.signal.take_price == Decimal("100")


def test_ranging_rejects_non_profitable_take_at_current_price(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = replace(
        _ranging_long_snapshot(),
        bb_lower_5m=Decimal("89"),
        bb_middle_5m=Decimal("89"),
    )
    _patch_snapshot(monkeypatch, snap)
    decision = evaluate(
        ticker="GAZP",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=False,
        session_guard_active=False,
    )

    assert decision.signal is None
    assert any("invalid_exit_levels" in failure for failure in decision.filters_failed)


def test_evaluate_ranging_refusal_keeps_previous_close_in_journal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = replace(_ranging_long_snapshot(), close_5m_prev=Decimal("101"))
    _patch_snapshot(monkeypatch, snap)
    decision = evaluate(
        ticker="GAZP",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=False,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert any("prev_close=101" in failure for failure in decision.filters_failed)


def test_evaluate_gap_fixture_blocks_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot(monkeypatch, _long_snapshot())
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
        gap_result=(True, Decimal("0.05")),
    )
    assert decision.verdict == "no_signal"
    assert "gap_pause" in decision.filters_failed
    assert decision.indicator_snapshot


def test_evaluate_chaos_fixture_blocks_signal(monkeypatch: pytest.MonkeyPatch) -> None:
    chaos_snap = replace(_long_snapshot(), adx_15m=Decimal("45"), atr_ratio_15m=Decimal("0.08"))
    _patch_snapshot(monkeypatch, chaos_snap)
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert decision.regime == "chaos"
    assert "regime_chaos" in decision.filters_failed


@pytest.mark.parametrize(
    ("adx_value", "atr_ratio", "expected_regime"),
    [
        (Decimal("30"), Decimal("0.08"), "volatile"),
        (Decimal("22"), Decimal("0.01"), "transitional"),
    ],
)
def test_evaluate_untradable_regime_records_filter_refusal(
    monkeypatch: pytest.MonkeyPatch,
    adx_value: Decimal,
    atr_ratio: Decimal,
    expected_regime: str,
) -> None:
    _patch_snapshot(
        monkeypatch,
        replace(_long_snapshot(), adx_15m=adx_value, atr_ratio_15m=atr_ratio),
    )
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert decision.regime == expected_regime
    assert f"regime_{expected_regime}" in decision.filters_failed
    assert decision.indicator_snapshot["atr_ratio"] == str(atr_ratio)


def test_regime_refusal_precedes_gap_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot(
        monkeypatch,
        replace(_long_snapshot(), adx_15m=Decimal("45"), atr_ratio_15m=Decimal("0.08")),
    )
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
        gap_result=(True, Decimal("0.05")),
    )
    assert decision.filters_failed == ("regime_chaos",)
    assert decision.indicator_snapshot["gap_pct"] == "0.05"


def test_evaluate_volume_spike_absent_blocks_trending_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snap = replace(_long_snapshot(), volume_spike_5m=False)
    _patch_snapshot(monkeypatch, snap)
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert any("volume_confirmation" in reason for reason in decision.filters_failed)


def test_evaluate_returns_no_signal_when_session_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=False,
        session_reason="выходной день",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert decision.filters_failed == ("session_open",)
    assert decision.reason == "выходной день"
    assert decision.indicator_snapshot.keys() >= _REQUIRED_SNAPSHOT_KEYS
    assert decision.indicator_snapshot["ema9"] == "n/a"


def test_closed_session_reason_takes_priority_over_guard_window() -> None:
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 10, 5, tzinfo=_MSK),
        is_session_open=False,
        session_reason="статус: BREAK_IN_TRADING",
        short_enabled=True,
        session_guard_active=True,
    )

    assert decision.verdict == "no_signal"
    assert decision.filters_failed == ("session_open",)
    assert decision.reason == "статус: BREAK_IN_TRADING"
    assert decision.indicator_snapshot["session_reason"] == "статус: BREAK_IN_TRADING"


@pytest.mark.parametrize("missing_reason", ["", "ok"])
def test_closed_session_without_reason_never_records_success(missing_reason: str) -> None:
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=False,
        session_reason=missing_reason,
        short_enabled=True,
        session_guard_active=False,
    )

    assert decision.filters_failed == ("session_open",)
    assert decision.reason == "сессия закрыта (причина не указана)"
    assert decision.indicator_snapshot["session_reason"] == decision.reason


@pytest.mark.parametrize(
    "guard_time",
    [datetime(2026, 3, 2, 10, 5, tzinfo=_MSK), datetime(2026, 3, 2, 23, 35, tzinfo=_MSK)],
    ids=["opening", "before-closing-auction"],
)
def test_evaluate_rejects_guard_even_if_flag_is_false(
    monkeypatch: pytest.MonkeyPatch, guard_time: datetime
) -> None:
    _patch_snapshot(monkeypatch, _long_snapshot())
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=guard_time,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert decision.filters_failed == ("session_guard",)
    assert "защитное окно" in decision.reason


@pytest.mark.parametrize(
    "out_of_session_time",
    [
        datetime(2026, 3, 2, 8, 0, tzinfo=_MSK),
        datetime(2026, 3, 2, 9, 55, tzinfo=_MSK),
        datetime(2026, 3, 2, 23, 45, tzinfo=_MSK),
        datetime(2026, 3, 2, 23, 50, tzinfo=_MSK),
        datetime(2026, 3, 7, 12, 0, tzinfo=_MSK),
    ],
    ids=["before-open", "opening-auction", "closing-auction", "after-session", "weekend"],
)
def test_evaluate_rejects_outside_session_even_if_caller_reports_open(
    monkeypatch: pytest.MonkeyPatch, out_of_session_time: datetime
) -> None:
    _patch_snapshot(monkeypatch, _long_snapshot())
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=out_of_session_time,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert decision.filters_failed == ("session_open",)
    assert decision.reason != "ok"


def test_evaluate_records_session_guard_as_filter_two() -> None:
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 10, 5, tzinfo=_MSK),
        is_session_open=True,
        session_reason="защитное окно 10 мин у открытия/закрытия сессии",
        short_enabled=True,
        session_guard_active=True,
    )
    assert decision.verdict == "no_signal"
    assert decision.filters_failed == ("session_guard",)
    assert "защитное окно" in decision.reason
    assert decision.indicator_snapshot.keys() >= _REQUIRED_SNAPSHOT_KEYS


def test_evaluate_short_blocked_without_short_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_snapshot(monkeypatch, _short_snapshot())
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=False,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert "short_availability" in decision.filters_failed


def test_evaluate_short_allowed_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_snapshot(monkeypatch, _short_snapshot())
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "signal"
    assert decision.side == "short"


def test_evaluate_market_filter_blocks_long_side_even_with_valid_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_snapshot(monkeypatch, _long_snapshot())
    market_filter = MarketFilterResult(
        allow_long=False, allow_short=True, paused=False, reason="breadth"
    )
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
        market_filter=market_filter,
    )
    assert decision.verdict == "no_signal"


def test_market_chaos_metrics_are_preserved_in_signal_and_refusal_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_snapshot(monkeypatch, _long_snapshot())
    now = datetime(2026, 3, 2, 12, 0, tzinfo=_MSK)
    normal = MarketFilterResult(
        allow_long=True,
        allow_short=True,
        paused=False,
        reason="normal",
        breadth=Decimal("0.6"),
        chaos_score=Decimal("0.72"),
        chaos_threshold=Decimal("0.68"),
        market_regime="normal",
    )
    signal = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=now,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
        market_filter=normal,
    )
    assert signal.verdict == "signal"
    assert signal.indicator_snapshot["market_chaos_score"] == "0.72"
    assert signal.indicator_snapshot["market_chaos_threshold"] == "0.68"
    assert signal.indicator_snapshot["market_regime"] == "normal"

    chaos = MarketFilterResult(
        allow_long=False,
        allow_short=False,
        paused=True,
        reason="chaos fail-closed",
        chaos_score=None,
        chaos_threshold=None,
        market_regime="chaos",
    )
    refusal = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=now,
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
        market_filter=chaos,
    )
    assert refusal.verdict == "no_signal"
    assert refusal.indicator_snapshot["market_chaos_score"] == "n/a"
    assert refusal.indicator_snapshot["market_chaos_threshold"] == "n/a"
    assert refusal.indicator_snapshot["market_regime"] == "chaos"


def test_no_signal_decision_always_has_full_indicator_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docs/03_STRATEGY.md: «пустой snapshot приравнивается к отсутствию journal» —
    100% отказов должны нести непустой snapshot.
    """
    snap = replace(_long_snapshot(), ema9_cross_up=False)
    _patch_snapshot(monkeypatch, snap)
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert decision.indicator_snapshot.keys() >= _REQUIRED_SNAPSHOT_KEYS


def test_evaluate_missing_history_has_complete_unavailable_snapshot() -> None:
    decision = evaluate(
        ticker="SBER",
        candles_5m=[],
        candles_15m=[],
        candles_1h=[],
        config=_config(),
        now=datetime(2026, 3, 2, 12, 0, tzinfo=_MSK),
        is_session_open=True,
        session_reason="ok",
        short_enabled=True,
        session_guard_active=False,
    )
    assert decision.verdict == "no_signal"
    assert decision.indicator_snapshot.keys() >= _REQUIRED_SNAPSHOT_KEYS
    assert decision.indicator_snapshot["adx"] == "n/a"


def test_compute_snapshot_ignores_unclosed_and_unknown_candles() -> None:
    """Ни незакрытая, ни стримовая свеча неизвестной полноты не влияет на сигнал."""

    def candles(count: int, interval: int) -> list[Candle]:
        start = datetime(2026, 3, 2, 7, tzinfo=UTC)
        result = []
        for index in range(count):
            close = Decimal(100 + index) + Decimal(index % 3) / Decimal(10)
            result.append(
                Candle(
                    open=close - Decimal("0.2"),
                    high=close + Decimal("1"),
                    low=close - Decimal("1"),
                    close=close,
                    volume=100 + index,
                    ts_open=start + timedelta(minutes=interval * index),
                    is_complete=True,
                )
            )
        return result

    series_5m = candles(60, 5)
    series_15m = candles(60, 15)
    series_1h = candles(60, 60)
    expected = compute_snapshot(
        candles_5m=series_5m,
        candles_15m=series_15m,
        candles_1h=series_1h,
        config=_config(),
    )
    assert expected.volume_avg_5m == Decimal("148.5")
    assert expected.close_5m_prev == Decimal("158.1")
    assert expected.high_5m == series_5m[-1].high
    assert expected.low_5m == series_5m[-1].low

    def unclosed(last: Candle, *, complete: bool | None) -> Candle:
        return replace(
            last,
            high=Decimal("10000"),
            low=Decimal("1"),
            close=Decimal("9000"),
            volume=100_000,
            is_complete=complete,
        )

    actual = compute_snapshot(
        candles_5m=series_5m + [unclosed(series_5m[-1], complete=False)],
        candles_15m=series_15m + [unclosed(series_15m[-1], complete=None)],
        candles_1h=series_1h + [unclosed(series_1h[-1], complete=False)],
        config=_config(),
    )
    assert actual == expected


def test_compute_snapshot_rejects_when_no_closed_candles() -> None:
    partial = Candle(
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=1,
        ts_open=datetime(2026, 3, 2, 7, tzinfo=UTC),
        is_complete=None,
    )
    with pytest.raises(InsufficientHistoryError, match="закрытых свечей"):
        compute_snapshot(
            candles_5m=[partial],
            candles_15m=[partial],
            candles_1h=[partial],
            config=_config(),
        )


# --- Дублирующие импорты, чтобы линт не считал их неиспользуемыми ------------------


def test_dataclasses_are_frozen_and_importable() -> None:
    assert Signal.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert Decision.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert ConditionResult.__dataclass_params__.frozen  # type: ignore[attr-defined]


def test_can_trade_now_still_importable_for_integration() -> None:
    assert callable(can_trade_now)
