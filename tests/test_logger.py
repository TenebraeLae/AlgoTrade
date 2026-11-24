"""Тесты logger.py: настройка форматтера, отказ на неизвестный уровень."""

from __future__ import annotations

import logging

import pytest

from trading_bot.logger import setup_logging


def test_setup_logging_configures_stream_handler() -> None:
    setup_logging("DEBUG")

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0], logging.StreamHandler)


def test_setup_logging_rejects_unknown_level() -> None:
    with pytest.raises(ValueError):
        setup_logging("NOT_A_LEVEL")


def test_setup_logging_is_idempotent_no_duplicate_handlers() -> None:
    setup_logging("INFO")
    setup_logging("INFO")

    assert len(logging.getLogger().handlers) == 1
