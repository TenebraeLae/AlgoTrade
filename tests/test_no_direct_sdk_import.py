"""M2 acceptance: ни один модуль бота, кроме api_client.py, не импортирует tinkoff.invest."""

from __future__ import annotations

import re
from pathlib import Path

_IMPORT_PATTERN = re.compile(r"^\s*(?:import\s+tinkoff\b|from\s+tinkoff\b)", re.MULTILINE)


def test_only_api_client_imports_tinkoff_sdk() -> None:
    package_root = Path(__file__).resolve().parent.parent / "trading_bot"
    offenders = []
    for path in package_root.rglob("*.py"):
        if path.name == "api_client.py":
            continue
        text = path.read_text(encoding="utf-8")
        if _IMPORT_PATTERN.search(text):
            offenders.append(str(path))
    assert not offenders, f"прямой импорт tinkoff.invest вне api_client.py: {offenders}"
