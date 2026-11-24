"""Application candle pacing budgets are configurable, finite and immutable."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.test_config import _clear_tbot_env
from trading_bot.config import CandleRequestPolicy, Settings, load_settings


@pytest.mark.parametrize(
    "name,value",
    [
        ("candle_min_interval_sec", "0"),
        ("candle_min_interval_sec", "nan"),
        ("candle_min_interval_sec", "inf"),
        ("candle_max_attempts", "0"),
        ("candle_retry_base_sec", "-1"),
        ("candle_retry_base_sec", "inf"),
        ("candle_max_retry_wait_sec", "0"),
        ("candle_max_retry_wait_sec", "nan"),
        ("candle_quota_max_attempts", "0"),
        ("candle_quota_max_attempts", "62"),
        ("market_data_requests_per_minute", "0"),
        ("market_data_requests_per_minute", "601"),
        ("liquidity_workers", "0"),
        ("liquidity_workers", "33"),
    ],
)
def test_invalid_candle_setting_is_rejected(name: str, value: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


@pytest.mark.parametrize(
    "invalid_key",
    [
        "candle_min_interval_sec",
        "candle_max_attempts",
        "candle_retry_base_sec",
        "candle_max_retry_wait_sec",
        "candle_quota_max_attempts",
        "market_data_requests_per_minute",
        "liquidity_workers",
    ],
)
def test_invalid_unary_environment_restores_all_paper_defaults(
    invalid_key: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_TRADING_MODE", "live")
    defaults = {
        "candle_min_interval_sec": 0.1,
        "candle_max_attempts": 4,
        "candle_retry_base_sec": 5.0,
        "candle_max_retry_wait_sec": 120.0,
        "candle_quota_max_attempts": 61,
        "market_data_requests_per_minute": 600,
        "liquidity_workers": 4,
    }
    for name in defaults:
        monkeypatch.setenv("TBOT_" + name.upper(), "2")
    monkeypatch.setenv("TBOT_" + invalid_key.upper(), "invalid")
    settings = load_settings()
    assert settings.trading_mode == "paper"
    assert {name: getattr(settings, name) for name in defaults} == defaults


def test_candle_settings_accept_environment_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TBOT_CANDLE_MIN_INTERVAL_SEC", "2.5")
    monkeypatch.setenv("TBOT_CANDLE_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("TBOT_CANDLE_RETRY_BASE_SEC", "8")
    monkeypatch.setenv("TBOT_CANDLE_MAX_RETRY_WAIT_SEC", "90")
    settings = Settings(_env_file=None)
    assert settings.candle_min_interval_sec == 2.5
    assert settings.candle_max_attempts == 3
    assert settings.candle_retry_base_sec == 8
    assert settings.candle_max_retry_wait_sec == 90


def test_shared_market_data_policy_defaults_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert CandleRequestPolicy().quota_max_attempts == 61
    assert CandleRequestPolicy().market_data_requests_per_minute == 600
    monkeypatch.setenv("TBOT_CANDLE_QUOTA_MAX_ATTEMPTS", "31")
    monkeypatch.setenv("TBOT_MARKET_DATA_REQUESTS_PER_MINUTE", "300")
    monkeypatch.setenv("TBOT_LIQUIDITY_WORKERS", "2")
    settings = Settings(_env_file=None)
    assert settings.candle_quota_max_attempts == 31
    assert settings.market_data_requests_per_minute == 300
    assert settings.liquidity_workers == 2


@pytest.mark.parametrize("attempts", [0, 62])
def test_quota_direct_policy_has_finite_operator_bound(attempts: int) -> None:
    with pytest.raises(ValueError):
        CandleRequestPolicy(quota_max_attempts=attempts)


def test_default_candle_spacing_is_one_tenth_second(monkeypatch: pytest.MonkeyPatch) -> None:
    """The operator-selected pacing is shared by direct policy and runtime Settings."""
    monkeypatch.delenv("TBOT_CANDLE_MIN_INTERVAL_SEC", raising=False)
    assert CandleRequestPolicy().min_interval_sec == 0.1
    assert Settings(_env_file=None).candle_min_interval_sec == 0.1


def test_candle_policy_is_immutable_and_validates_direct_construction() -> None:
    policy = CandleRequestPolicy()
    with pytest.raises(FrozenInstanceError):
        policy.max_attempts = 100  # type: ignore[misc]
    with pytest.raises(ValueError):
        CandleRequestPolicy(min_interval_sec=float("nan"))
    with pytest.raises(ValueError):
        CandleRequestPolicy(max_retry_wait_sec=float("inf"))
    with pytest.raises(ValueError):
        CandleRequestPolicy(max_attempts=0)
