"""Детерминированная рекомендация ликвидного paper-пула (слой L3).

Разрешено: ранжировать проверенные сессионные объёмы в лотах; paper-bootstrap
применяет selected на старте. Запрещено: знать о SDK, счёте, заявках или менять риск.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal


@dataclass(frozen=True)
class LiquidityRules:
    """Настройки окна, перцентиля и допустимого размера пула из конфига."""

    sessions: int
    percentile: Decimal
    minimum: int
    maximum: int

    def __post_init__(self) -> None:
        if (
            self.sessions <= 0
            or not self.percentile.is_finite()
            or not Decimal(0) < self.percentile < Decimal(1)
            or self.minimum <= 0
            or self.maximum < self.minimum
        ):
            raise ValueError("некорректные настройки ликвидности")


@dataclass(frozen=True)
class PoolRecommendation:
    """Детерминированный selected и причины; входы всё ещё требуют всех фильтров."""

    selected: tuple[str, ...]
    threshold_lots: Decimal
    average_daily_lots: dict[str, Decimal]
    excluded: dict[str, str]


def recommend_pool(
    daily_volume_lots: Mapping[str, Sequence[int]], rules: LiquidityRules
) -> PoolRecommendation:
    """Предлагает пул с объёмом строго выше ближайшего ранга перцентиля.

    Ожидает подтверждённый объём каждой из `sessions` торговых сессий в лотах
    на тикер; календарь и сбор свечей — обязанности поставщика выше по стеку.
    Недостаток кандидатов не превращается в уменьшенный пул: явный отказ.
    """
    averages: dict[str, Decimal] = {}
    excluded: dict[str, str] = {}
    for ticker, volumes in daily_volume_lots.items():
        if not ticker or len(volumes) != rules.sessions:
            excluded[ticker] = f"нет {rules.sessions} проверенных сессий"
        elif any(not isinstance(volume, int) or volume < 0 for volume in volumes):
            excluded[ticker] = "объём сессии некорректен"
        else:
            averages[ticker] = Decimal(sum(volumes)) / Decimal(rules.sessions)
    if len(averages) < rules.minimum:
        raise ValueError("недостаточно кандидатов с проверенной ликвидностью")

    ordered_values = sorted(averages.values())
    nearest_rank = int(
        (rules.percentile * Decimal(len(ordered_values))).to_integral_value(rounding=ROUND_CEILING)
    )
    threshold = ordered_values[nearest_rank - 1]
    eligible = sorted(
        (ticker for ticker, average in averages.items() if average > threshold),
        key=lambda ticker: (-averages[ticker], ticker),
    )
    if len(eligible) < rules.minimum:
        raise ValueError("недостаточно ликвидных кандидатов выше перцентиля")
    selected = tuple(eligible[: rules.maximum])
    pct = rules.percentile * 100
    pct_label = str(int(pct)) if pct == pct.to_integral_value() else str(pct)
    reason = f"объём не превышает {pct_label}-й перцентиль"
    for ticker in averages:
        if ticker not in selected:
            excluded[ticker] = reason if averages[ticker] <= threshold else "вне размера пула"
    return PoolRecommendation(
        selected=selected,
        threshold_lots=threshold,
        average_daily_lots=averages,
        excluded=excluded,
    )
