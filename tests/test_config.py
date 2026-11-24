"""Тесты config.py: фейл-сейф на paper, отказ на неизвестный ключ, предупреждение о риске."""

from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import pytest

from trading_bot.config import ConfigError, load_settings


def _clear_tbot_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("TBOT_"):
            monkeypatch.delenv(key, raising=False)


def test_missing_env_file_falls_back_to_paper_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)  # нет .env вообще

    settings = load_settings()

    assert settings.trading_mode == "paper"
    assert settings.risk_per_trade == Decimal("0.01")
    assert settings.daily_loss_limit == Decimal("0.03")


def test_broken_env_file_falls_back_to_paper_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        "TBOT_TRADING_MODE=definitely_not_a_valid_mode\n", encoding="utf-8"
    )
    caplog.set_level(logging.WARNING, logger="trading_bot.config")

    settings = load_settings()

    assert settings.trading_mode == "paper"
    assert any("paper" in record.getMessage() for record in caplog.records)


def test_guard_covering_the_regular_session_falls_back_to_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_TRADING_MODE", "live")
    monkeypatch.setenv("TBOT_SESSION_GUARD_MIN", "410")

    settings = load_settings()

    assert settings.trading_mode == "paper"
    assert settings.session_guard_min == 10


def test_unknown_key_raises_explicit_config_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_SOME_UNKNOWN_PARAM", "1")

    with pytest.raises(ConfigError) as exc_info:
        load_settings()

    assert "some_unknown_param" in str(exc_info.value).lower()


def test_risk_per_trade_above_threshold_warns_but_does_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_RISK_PER_TRADE", "0.03")
    caplog.set_level(logging.WARNING, logger="trading_bot.config")

    settings = load_settings()

    assert settings.risk_per_trade == Decimal("0.03")
    assert any("risk_per_trade" in record.getMessage() for record in caplog.records)


def test_nonfinite_risk_from_live_config_falls_back_to_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_TRADING_MODE", "live")
    monkeypatch.setenv("TBOT_RISK_PER_TRADE", "NaN")
    settings = load_settings()
    assert settings.trading_mode == "paper"
    assert settings.risk_per_trade == Decimal("0.01")


def test_trading_mode_defaults_to_paper_without_any_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.trading_mode == "paper"


def test_new_risk_limits_load_and_bad_values_fall_back_to_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    defaults = load_settings()
    assert defaults.max_position_pct == Decimal("0.15")
    assert defaults.max_volume_participation == Decimal("0.05")
    assert defaults.global_drawdown_stop == Decimal("0.15")
    monkeypatch.setenv("TBOT_MAX_VOLUME_PARTICIPATION", "0.04")
    assert load_settings().max_volume_participation == Decimal("0.04")
    monkeypatch.setenv("TBOT_MAX_VOLUME_PARTICIPATION", "invalid")
    fallback = load_settings()
    assert fallback.trading_mode == "paper"
    assert fallback.max_volume_participation == Decimal("0.05")


def test_money_fields_are_decimal_not_float(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert isinstance(settings.risk_per_trade, Decimal)
    assert isinstance(settings.daily_loss_limit, Decimal)


def test_invalid_timezone_falls_back_to_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_TZ_DISPLAY", "Not/A_Real_Zone")

    settings = load_settings()

    assert settings.trading_mode == "paper"
    assert settings.tz_display == "Europe/Moscow"


def test_vk_allowed_peer_ids_with_leading_zeros_does_not_crash_settings_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Живой баг, воспроизведён Docker-запуском 2026-09-26: `.env.example` даёт
    `TBOT_VK_ALLOWED_PEER_IDS=000000000`. Когда тип поля был `tuple[int, ...]`,
    pydantic-settings пытался JSON-декодировать это значение до наших валидаторов и
    ловил `SettingsError` — причём даже в `_fallback_settings()`, так что контейнер ушёл в
    restart-loop даже в paper. Поле теперь `str`, не «комплексный» тип — этот тест не даёт
    регрессии.
    """
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_VK_ALLOWED_PEER_IDS", "000000000")

    settings = load_settings()

    assert settings.trading_mode == "paper"
    assert settings.parsed_vk_allowed_peer_ids() == (0,)


def test_vk_allowed_peer_ids_parses_comma_separated_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_VK_ALLOWED_PEER_IDS", "123, 456")

    settings = load_settings()

    assert settings.parsed_vk_allowed_peer_ids() == (123, 456)


def test_vk_allowed_peer_ids_empty_string_parses_to_empty_tuple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.parsed_vk_allowed_peer_ids() == ()


def test_vk_allowed_peer_ids_non_numeric_falls_back_to_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_VK_ALLOWED_PEER_IDS", "not-a-number")

    settings = load_settings()

    assert settings.trading_mode == "paper"
    assert settings.parsed_vk_allowed_peer_ids() == ()


def test_paper_market_source_is_offline_without_balance_and_enabled_with_balance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    assert load_settings().paper_source_enabled is False
    monkeypatch.setenv("TBOT_PAPER_STARTING_BALANCE", "100000")
    assert load_settings().paper_source_enabled is True


def test_paper_market_source_ignores_legacy_fields_but_requires_positive_capital(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TBOT_PAPER_STARTING_BALANCE", "100000")
    candidates = tuple(f"T{i}" for i in range(30))
    approved = candidates[:15]
    monkeypatch.setenv("TBOT_PAPER_CANDIDATE_TICKERS", ",".join(candidates))
    monkeypatch.setenv("TBOT_PAPER_APPROVED_TICKERS", ",".join(approved))
    monkeypatch.setenv("TBOT_PAPER_INDEX_ID", "index-id")
    settings = load_settings()
    assert settings.paper_source_enabled
    assert settings.paper_starting_balance == Decimal("100000")
    assert settings.parsed_paper_candidates() == candidates
    assert settings.parsed_paper_approved() == approved
    monkeypatch.setenv("TBOT_PAPER_APPROVED_TICKERS", "UNKNOWN")
    assert load_settings().paper_source_enabled is True
    monkeypatch.setenv("TBOT_PAPER_STARTING_BALANCE", "-1")
    assert load_settings().paper_source_enabled is False


def test_paper_feed_staleness_limits_are_explicit_and_positive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    defaults = load_settings()
    assert defaults.paper_max_bar_staleness_sec == 600
    assert defaults.paper_max_quote_staleness_sec == 30
    monkeypatch.setenv("TBOT_PAPER_MAX_QUOTE_STALENESS_SEC", "5")
    assert load_settings().paper_max_quote_staleness_sec == 5
    monkeypatch.setenv("TBOT_PAPER_MAX_QUOTE_STALENESS_SEC", "0")
    assert load_settings().paper_max_quote_staleness_sec == 30


def test_chaos_config_defaults_and_invalid_fallback_are_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _clear_tbot_env(monkeypatch)
    monkeypatch.chdir(tmp_path)
    defaults = load_settings()
    assert defaults.chaos_window == 500
    assert defaults.chaos_threshold_pct == Decimal("0.80")
    assert defaults.chaos_hysteresis == Decimal("0.05")
    assert defaults.chaos_confirm_bars == 2
    monkeypatch.setenv("TBOT_CHAOS_THRESHOLD_PCT", "2")
    assert load_settings().chaos_threshold_pct == Decimal("0.80")
