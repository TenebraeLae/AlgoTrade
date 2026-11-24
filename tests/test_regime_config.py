"""Regime interval is an injected, validated restart-time setting."""

from pathlib import Path

import pytest

from tests.test_config import _clear_tbot_env
from tests.test_regime_schedule import _at, _observation
from tests.test_strategy import _config
from trading_bot.config import load_settings
from trading_bot.regime_schedule import LocalRegimeSchedule


def test_regime_interval_default_override_and_safe_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert load_settings().regime_refresh_min == 30
    monkeypatch.setenv("TBOT_REGIME_REFRESH_MIN", "15")
    assert load_settings().regime_refresh_min == 15
    monkeypatch.setenv("TBOT_REGIME_REFRESH_MIN", "0")
    monkeypatch.setenv("TBOT_TRADING_MODE", "live")
    fallback = load_settings()
    assert fallback.regime_refresh_min == 30
    assert fallback.trading_mode == "paper"


def test_custom_interval_changes_slots_but_not_pre_session_anchor() -> None:
    schedule = LocalRegimeSchedule(_config(), refresh_minutes=15)
    pre = schedule.update("SBER", _at(9, 50), _observation(_at(9, 50)))
    assert pre.slot == _at(9, 50)
    state = schedule.update("SBER", _at(10, 17), _observation(_at(10, 17)))
    assert state.slot == _at(10, 15)
    assert state.usable_for("SBER", _at(10, 29))
    assert not state.usable_for("SBER", _at(10, 30))
