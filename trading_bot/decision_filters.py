"""Проекция выполненных проверок стратегии/риска на категории 1–10 (L4).

Разрешено: сопоставить стабильные имена проверок с docs/03 и сохранить свидетельства.
Запрещено: повторно считать риск, считать отсутствие проверки успешным или менять сигнал.
"""

from typing import Literal

from trading_bot.database import FilterCategoryRecord
from trading_bot.risk_manager import RiskVerdict
from trading_bot.strategy import Decision

# Для групповой категории passed требует ВСЕХ компонентов; failed достаточно одного.
_CATEGORY_CHECKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("session_time", ("session_open",)),
    ("session_guard", ("session_guard",)),
    ("instrument_status", ("instrument_status_normal",)),
    ("regime", ("regime_tradable",)),
    ("gap", ("gap_pause",)),
    ("volatility", ("risk:volatility_within_limit",)),
    ("spread", ("risk:spread_within_limit",)),
    ("correlation", ("risk:correlation_within_limit",)),
    ("diversification", ("risk:open_positions_within_limit", "risk:sector_and_ticker_limits")),
    (
        "daily_limits",
        (
            "risk:daily_loss_limit_not_reached",
            "risk:trades_per_day_within_limit",
            "risk:free_balance_sufficient",
        ),
    ),
)


def categorize_filters(
    decision: Decision, risk: RiskVerdict | None
) -> tuple[FilterCategoryRecord, ...]:
    """Возвращает все 10 категорий, не выдумывая выполнение пропущенных проверок.

    Разные numbering strategy (1–10) и risk (1–12) не смешиваются. Имена
    дополнительных ограничений остаются в filters_passed/failed вне этой проекции.
    """
    passed = set(decision.filters_passed)
    failed = {name.split(":", 1)[0] for name in decision.filters_failed}
    if failed & {
        "regime_chaos",
        "regime_volatile",
        "regime_transitional",
        "local_regime_unavailable",
    }:
        failed.add("regime_tradable")
    if risk is not None:
        for check in risk.checks:
            name = f"risk:{check.name}"
            (passed if check.passed else failed).add(name)
            # Категории 1/3 допускают свидетельства стратегии ИЛИ риска.
            if check.name in ("session_open", "instrument_status_normal"):
                (passed if check.passed else failed).add(check.name)
    results: list[FilterCategoryRecord] = []
    for number, (name, required) in enumerate(_CATEGORY_CHECKS, start=1):
        good = tuple(check for check in required if check in passed)
        bad = tuple(check for check in required if check in failed)
        # Сохраняем точное risk-имя источника для категорий-алиасов.
        good_evidence = tuple(f"risk:{c}" if f"risk:{c}" in passed else c for c in good)
        bad_evidence = tuple(f"risk:{c}" if f"risk:{c}" in failed else c for c in bad)
        status: Literal["passed", "failed", "not_evaluated"] = (
            "failed" if bad else "passed" if len(good) == len(required) else "not_evaluated"
        )
        results.append(FilterCategoryRecord(number, name, status, good_evidence, bad_evidence))
    return tuple(results)
