"""Подбор бумажного пула по наблюдаемой ликвидности, без сетевых вызовов."""

from __future__ import annotations

from decimal import Decimal

import pytest

from trading_bot.pool import LiquidityRules, recommend_pool


def test_recommendation_explains_threshold_rank_and_exclusions() -> None:
    # 30 кандидатов, 10 фактических сессий на инструмент.
    volumes = {f"T{i:02}": [i * 100] * 10 for i in range(1, 31)}
    volumes["MISSING"] = [5000] * 9
    result = recommend_pool(
        volumes,
        LiquidityRules(sessions=10, percentile=Decimal("0.40"), minimum=15, maximum=20),
    )

    assert result.threshold_lots == Decimal(1200)
    assert result.selected == tuple(f"T{i:02}" for i in range(30, 12, -1))
    assert result.average_daily_lots["T30"] == Decimal(3000)
    assert result.excluded["T12"] == "объём не превышает 40-й перцентиль"
    assert result.excluded["MISSING"] == "нет 10 проверенных сессий"
    assert len(result.selected) == 18


def test_fewer_than_minimum_liquid_candidates_fail_closed() -> None:
    volumes = {f"T{i:02}": [i] * 10 for i in range(20)}
    with pytest.raises(ValueError, match="ликвидных"):
        recommend_pool(
            volumes,
            LiquidityRules(sessions=10, percentile=Decimal("0.40"), minimum=15, maximum=30),
        )


def test_invalid_volume_never_counts_as_liquidity() -> None:
    volumes = {f"T{i:02}": [i * 10] * 10 for i in range(30)}
    volumes["BAD"] = [-1] * 10
    result = recommend_pool(
        volumes,
        LiquidityRules(sessions=10, percentile=Decimal("0.40"), minimum=15, maximum=30),
    )
    assert "BAD" not in result.selected
    assert result.excluded["BAD"] == "объём сессии некорректен"


def test_bad_liquidity_rules_are_rejected() -> None:
    with pytest.raises(ValueError):
        LiquidityRules(sessions=0, percentile=Decimal("0.4"), minimum=15, maximum=30)
    with pytest.raises(ValueError):
        LiquidityRules(sessions=10, percentile=Decimal("1"), minimum=15, maximum=30)
