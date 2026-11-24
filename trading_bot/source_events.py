"""Безопасные факты об отказе источника до формирования paper-тика.

Разрешено: передавать причину и действие без текста исключения или секрета.
Запрещено: считать источник исправным, принимать торговое решение и писать в БД.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class SourceRefusal:
    """Один отказ при построении тика известного paper-тикера."""

    ticker: str
    stage: str
    observed_at: datetime
    source_bar_ts: datetime | None
    action: Literal["skip", "stop"]
    error_type: str
