"""PaperBroker — симуляция исполнения (docs/00_PLAN.md §8, §3).

Разрешено: рассчитать цену/комиссию/проскальзывание заполнения по переданной
референсной цене; смоделировать частичное исполнение и правило «остаток
отменяется через 60 секунд» как чистую функцию времени (без реального sleep).
Запрещено: любой импорт gRPC-SDK/`api_client.InvestGateway` — этот класс
физически не может отправить реальную заявку (docs/13_SECURITY.md, п.1; проверяется
тестами `tests/test_paper_broker.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

Side = Literal["long", "short"]

#: Остаток неисполненной лимитки отменяется через 60с (docs/00_PLAN.md §8).
DEFAULT_CANCEL_TIMEOUT_SEC = 60


@dataclass(frozen=True)
class Fill:
    """Результат симулированного исполнения одной заявки."""

    filled_lots: int
    price: Decimal
    fee: Decimal
    slippage_cost: Decimal
    remaining_lots: int


class PaperBroker:
    """Бумажный брокер: только расчёт заполнения, без единого gRPC-вызова.

    Конструктор не принимает `InvestGateway`/токен — им физически негде храниться
    в этом классе (docs/13_SECURITY.md, п.1).
    """

    def __init__(self, *, fee_rate: Decimal, slippage_assumed: Decimal) -> None:
        if fee_rate < 0 or slippage_assumed < 0:
            raise ValueError("fee_rate/slippage_assumed не могут быть отрицательными")
        self._fee_rate = fee_rate
        self._slippage_assumed = slippage_assumed

    def fill_market_order(
        self,
        *,
        side: Side,
        requested_lots: int,
        lot_size: int,
        reference_price: Decimal,
        available_lots: int | None = None,
    ) -> Fill:
        """Симулирует рыночную заявку: проскальзывание против позиции, комиссия
        от тарифа, частичное исполнение, если `available_lots` (симулированная
        ликвидность) меньше запрошенного (docs/00_PLAN.md §8).

        Возврат: `Fill` — `filled_lots <= requested_lots`,
        `remaining_lots = requested_lots - filled_lots`.
        Отказ: `ValueError` на `requested_lots <= 0`.
        """
        if requested_lots <= 0:
            raise ValueError("requested_lots должен быть положительным")
        if available_lots is None:
            filled_lots = requested_lots
        else:
            filled_lots = min(requested_lots, max(available_lots, 0))

        # Проскальзывание всегда против позиции: long платит дороже, short продаёт дешевле.
        if side == "long":
            slippage_mult = Decimal(1) + self._slippage_assumed
        else:
            slippage_mult = Decimal(1) - self._slippage_assumed
        fill_price = reference_price * slippage_mult

        filled_units = Decimal(filled_lots * lot_size)
        notional = fill_price * filled_units
        fee = notional * self._fee_rate
        slippage_cost = abs(fill_price - reference_price) * filled_units

        return Fill(
            filled_lots=filled_lots,
            price=fill_price,
            fee=fee,
            slippage_cost=slippage_cost,
            remaining_lots=requested_lots - filled_lots,
        )


def should_cancel_remainder(
    *, placed_at: datetime, now: datetime, timeout_sec: int = DEFAULT_CANCEL_TIMEOUT_SEC
) -> bool:
    """Правило «остаток лимитки отменяется через 60 секунд» (docs/00_PLAN.md §8).

    Чистая функция времени — вызывающий код (будущий `executor.py`, M8) сам решает,
    когда её проверять; здесь нет реального таймера/`sleep`.
    """
    return (now - placed_at) >= timedelta(seconds=timeout_sec)
