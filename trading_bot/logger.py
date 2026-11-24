"""Настройка логирования (слой L0, docs/01_ARCHITECTURE.md).

Разрешено: настроить структурированный формат для стандартного `logging` один раз
при старте (`setup_logging`). Запрещено: логировать открытые токены (см.
`security.mask_token`) и полные ответы API (docs/02_CODING_RULES.md, «Логирование»).
"""

from __future__ import annotations

import logging
import sys

#: Формат из docs/02_CODING_RULES.md: событие/уровень/контекст. `%(name)s` даёт
#: модуль-источник, чтобы не терять контекст при разборе логов.
_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def setup_logging(level: str = "INFO") -> None:
    """Настраивает корневой логгер один раз при старте процесса.

    Возврат: нет (побочный эффект — глобальная конфигурация `logging`).
    Отказ: неизвестный уровень — `ValueError` от `logging.getLevelName`, явная
    ошибка запуска, а не тихий откат на `INFO`.
    """
    numeric_level = logging.getLevelName(level.upper())
    if not isinstance(numeric_level, int):
        raise ValueError(f"неизвестный уровень логирования: {level!r}")

    root = logging.getLogger()
    root.setLevel(numeric_level)
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))
    root.addHandler(handler)
