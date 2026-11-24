"""Загрузка и валидация конфигурации бота (слой L0, docs/01_ARCHITECTURE.md).

Разрешено: читать переменные окружения `TBOT_*` и `.env`, отдавать один
неизменяемый объект `Settings` через `load_settings()`.
Запрещено: читать `os.environ` внутри функций бизнес-логики (только здесь, один раз,
инжект в конструкторы — см. docs/02_CODING_RULES.md); публичный способ повысить
`risk_per_trade` или лимиты риска — такого API в этом модуле нет и не будет
(docs/13_SECURITY.md, границы возможностей системы, п.2).
"""

from __future__ import annotations

import contextlib
import logging
import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.exceptions import SettingsError

logger = logging.getLogger(__name__)

#: docs/07_CONFIG_SPEC.md: Moscow-clock local regime refresh interval.
DEFAULT_REGIME_REFRESH_MIN = 30

#: Operator policy: round-the-clock wall-clock pool slots, not session slots.
DEFAULT_POOL_REFRESH_MIN = 15

#: Порог из docs/07_CONFIG_SPEC.md (тест №3): выше — не отказ, но заметное предупреждение.
_RISK_PER_TRADE_WARN_THRESHOLD = Decimal("0.02")


class ConfigError(Exception):
    """Конфигурация содержит неизвестный параметр — явная ошибка запуска.

    Не путать с battыми/недоступными значениями: там фолбэк в paper (см. `load_settings`).
    Часть будущей иерархии `TBotError` (docs/02_CODING_RULES.md); переносится туда,
    когда появится `trading_bot/errors.py`.
    """


@dataclass(frozen=True)
class CandleRequestPolicy:
    """Application pacing/retry budgets, not a statement of broker rate limits."""

    min_interval_sec: float = 0.1
    max_attempts: int = 4
    retry_base_sec: float = 5.0
    max_retry_wait_sec: float = 120.0
    quota_max_attempts: int = 61
    market_data_requests_per_minute: int = 600

    def __post_init__(self) -> None:
        """Reject non-finite or unbounded request policies at construction."""
        from math import isfinite

        durations = (self.min_interval_sec, self.retry_base_sec, self.max_retry_wait_sec)
        if any(not isfinite(value) or value <= 0 for value in durations):
            raise ValueError("candle request durations must be finite and positive")
        counts = (self.max_attempts, self.quota_max_attempts, self.market_data_requests_per_minute)
        if any(type(value) is not int or value < 1 for value in counts):
            raise ValueError("request counts must be positive integers")
        if self.quota_max_attempts > 61:
            raise ValueError("quota max_attempts cannot exceed 61")
        if not 1 <= self.market_data_requests_per_minute <= 600:
            raise ValueError("MarketData budget must be between 1 and 600")


class Settings(BaseSettings):
    """Единственный источник параметров бота, инжектируется в конструкторы.

    Загружается один раз через `load_settings()`. Неизвестный ключ `TBOT_*` —
    ошибка запуска (`extra="forbid"`), а не тихое игнорирование.
    """

    model_config = SettingsConfigDict(
        env_prefix="TBOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="forbid",
    )

    trading_mode: Literal["paper", "live"] = "paper"
    secrets_path: Path = Path("secrets/store.bin")
    db_path: Path = Path("data/tbot.db")
    dashboard_port: int = Field(default=8080, gt=0, lt=65536)
    dashboard_user: str = "admin"
    tz_display: str = "Europe/Moscow"
    log_level: str = "INFO"
    vk_enabled: bool = True
    vk_allowed_peer_ids: str = ""
    risk_per_trade: Decimal = Decimal("0.01")
    daily_loss_limit: Decimal = Decimal("0.03")
    global_drawdown_stop: Decimal = Field(default=Decimal("0.15"), gt=0, lt=1)
    max_position_pct: Decimal = Field(default=Decimal("0.15"), gt=0, le=1)
    max_volume_participation: Decimal = Field(default=Decimal("0.05"), gt=0, le=1)
    atr_stop_mult: Decimal = Decimal("1.5")
    atr_take_mult: Decimal = Decimal("2.0")
    atr_trail_trigger: Decimal = Decimal("1.0")
    atr_trail_dist: Decimal = Decimal("1.0")
    max_trades_per_day: int = Field(default=30, gt=0)
    min_free_balance: Decimal = Decimal("5000")
    max_open_positions: int = Field(default=7, gt=0)
    max_per_sector: int = Field(default=2, gt=0)
    max_correlation: Decimal = Decimal("0.85")
    max_atr_ratio: Decimal = Decimal("0.05")
    max_spread_bps: Decimal = Decimal("30")
    volatile_size_scaler: Decimal = Decimal("0.5")
    session_guard_min: int = Field(default=10, ge=0, le=409)
    slippage_assumed: Decimal = Decimal("0.0005")
    fee_rate: Decimal = Decimal("0.003")
    backoff_base_sec: int = Field(default=5, gt=0)
    backoff_max_attempts: int = Field(default=10, gt=0)
    stream_reconnect_sec: int = Field(default=5, gt=0)
    # Shared unary MarketData admission; local budgets, not a verified user tariff.
    candle_min_interval_sec: float = Field(default=0.1, gt=0, allow_inf_nan=False)
    candle_max_attempts: int = Field(default=4, gt=0)
    candle_retry_base_sec: float = Field(default=5.0, gt=0, allow_inf_nan=False)
    candle_max_retry_wait_sec: float = Field(default=120.0, gt=0, allow_inf_nan=False)
    candle_quota_max_attempts: int = Field(default=61, gt=0, le=61)
    market_data_requests_per_minute: int = Field(default=600, gt=0, le=600)
    liquidity_workers: int = Field(default=4, ge=1, le=32)
    # — docs/07_CONFIG_SPEC.md «Стратегия» (M7, strategy.py — никаких
    # магических чисел в коде стратегии):
    regime_refresh_min: int = Field(default=DEFAULT_REGIME_REFRESH_MIN, gt=0)
    ema_fast: int = Field(default=9, gt=0)
    ema_mid: int = Field(default=21, gt=0)
    ema_slow: int = Field(default=50, gt=0)
    rsi_period: int = Field(default=14, gt=0)
    rsi_oversold: Decimal = Decimal("30")
    rsi_overbought: Decimal = Decimal("70")
    rsi_trend_low: Decimal = Decimal("30")
    rsi_trend_high: Decimal = Decimal("65")
    macd_fast: int = Field(default=12, gt=0)
    macd_slow: int = Field(default=26, gt=0)
    macd_signal: int = Field(default=9, gt=0)
    bb_period: int = Field(default=20, gt=0)
    bb_mult: Decimal = Decimal("2.0")
    atr_period: int = Field(default=14, gt=0)
    adx_period: int = Field(default=14, gt=0)
    adx_trend: Decimal = Decimal("25")
    adx_range: Decimal = Decimal("20")
    adx_chaos: Decimal = Decimal("40")
    vol_window: int = Field(default=20, gt=0)
    gap_pause_pct: Decimal = Decimal("0.03")
    gap_pause_min: int = Field(default=30, gt=0)
    breadth_bearish: Decimal = Decimal("0.30")
    breadth_bullish: Decimal = Decimal("0.70")
    pool_min: int = Field(default=15, gt=0)
    pool_max: int = Field(default=30, ge=15)
    pool_liquidity_sessions: int = Field(default=10, gt=0)
    liquidity_percentile: Decimal = Field(default=Decimal("0.40"), gt=0, lt=1)
    chaos_component_period: int = Field(default=14, gt=1)
    chaos_window: int = Field(default=500, gt=1)
    chaos_threshold_pct: Decimal = Field(default=Decimal("0.80"), gt=0, lt=1)
    chaos_hysteresis: Decimal = Field(default=Decimal("0.05"), ge=0, lt=1)
    chaos_confirm_bars: int = Field(default=2, gt=0)
    paper_starting_balance: Decimal | None = Field(default=None, gt=0)
    # Deprecated compatibility inputs: accepted, but ignored by automatic bootstrap.
    # Neither manual tickers nor an arbitrary index ID can override discovery.
    paper_candidate_tickers: str = ""
    paper_approved_tickers: str = ""
    paper_index_id: str = ""
    paper_max_bar_staleness_sec: int = Field(default=600, gt=0)
    paper_max_quote_staleness_sec: int = Field(default=30, gt=0)

    @staticmethod
    def _parse_tickers(value: str) -> tuple[str, ...]:
        """Разбирает deprecated-строки; результат не является runtime-разрешением."""
        return tuple(part.strip() for part in value.split(",") if part.strip())

    def parsed_paper_candidates(self) -> tuple[str, ...]:
        """Совместимость: прочитать legacy candidates; bootstrap игнорирует результат."""
        return self._parse_tickers(self.paper_candidate_tickers)

    def parsed_paper_approved(self) -> tuple[str, ...]:
        """Совместимость: прочитать legacy approved; bootstrap игнорирует результат."""
        return self._parse_tickers(self.paper_approved_tickers)

    @property
    def paper_source_enabled(self) -> bool:
        """Opt into automatic paper discovery with a positive explicit balance only.

        Legacy candidates/approved/index settings are ignored, even if populated.
        Missing balance remains offline heartbeat-only; live is never enabled here.
        """
        balance = self.paper_starting_balance
        return (
            self.trading_mode == "paper"
            and balance is not None
            and balance.is_finite()
            and balance > 0
        )

    @field_validator("vk_allowed_peer_ids")
    @classmethod
    def _validate_peer_ids_format(cls, value: str) -> str:
        """Хранит `peer_id` как сырую строку (не `tuple[int, ...]`), чтобы pydantic-settings
        не считал поле «комплексным» и не пытался ПРЕДварительно JSON-декодировать
        значение из окружения (`EnvSettingsSource.decode_complex_value`) до наших валидаторов.

        Живой баг: `TBOT_VK_ALLOWED_PEER_IDS=000000000` в Docker ломал `tuple[int, ...]`-вариант
        поля с `JSONDecodeError` («000000000» — невалидный JSON из-за ведущих нулей) ещё до
        вызова этого валидатора — и даже до вызова в `_fallback_settings()`, так как
        `EnvSettingsSource` вызывается при любом создании `Settings(...)`, независимо от явных
        kwargs (воспроизведено Docker-запуском 2026-09-26 — контейнер ушёл в restart-loop,
        так как падал даже папер-фолбэк).
        """
        for chunk in value.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if not chunk.lstrip("-").isdigit():
                raise ValueError(f"vk_allowed_peer_ids: не число: {chunk!r}")
        return value

    def parsed_vk_allowed_peer_ids(self) -> tuple[int, ...]:
        """Разбирает `vk_allowed_peer_ids` (строка `"123,456"`) в кортеж id для `vk_bot.py`."""
        return tuple(
            int(chunk.strip()) for chunk in self.vk_allowed_peer_ids.split(",") if chunk.strip()
        )

    @field_validator("tz_display")
    @classmethod
    def _validate_tz(cls, value: str) -> str:
        """Отображение времени только в реальной IANA tz (docs/02_CODING_RULES.md)."""
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"неизвестная таймзона: {value!r}") from exc
        return value

    @field_validator(
        "risk_per_trade",
        "daily_loss_limit",
        "global_drawdown_stop",
        "max_position_pct",
        "max_volume_participation",
        "atr_stop_mult",
        "atr_take_mult",
        "atr_trail_trigger",
        "atr_trail_dist",
        "min_free_balance",
        "max_correlation",
        "max_atr_ratio",
        "max_spread_bps",
        "volatile_size_scaler",
        "slippage_assumed",
        "fee_rate",
        "rsi_oversold",
        "rsi_overbought",
        "rsi_trend_low",
        "rsi_trend_high",
        "bb_mult",
        "adx_trend",
        "adx_range",
        "adx_chaos",
        "gap_pause_pct",
        "breadth_bearish",
        "breadth_bullish",
        "liquidity_percentile",
        "chaos_threshold_pct",
        "chaos_hysteresis",
        "paper_starting_balance",
        mode="before",
    )
    @classmethod
    def _decimal_from_str(cls, value: object) -> object:
        """Деньги/доли риска — только `Decimal(str(x))`, никогда через float."""
        if isinstance(value, float):
            raise TypeError("денежные/риск-параметры не принимают float, только строку/Decimal")
        if isinstance(value, (str, Decimal, int)):
            try:
                return Decimal(str(value))
            except InvalidOperation as exc:
                raise ValueError(f"не удаётся разобрать как Decimal: {value!r}") from exc
        return value


def _fallback_settings() -> Settings:
    """Безопасные значения по умолчанию — используются при любой ошибке чтения конфига.

    Явно перечисляет каждое поле, чтобы обломки `.env`/окружения не просочились
    в результат через слои приоритета pydantic-settings (AGENTS.md §4.1: paper всегда).
    """
    return Settings(
        trading_mode="paper",
        secrets_path=Path("secrets/store.bin"),
        db_path=Path("data/tbot.db"),
        dashboard_port=8080,
        dashboard_user="admin",
        tz_display="Europe/Moscow",
        log_level="INFO",
        vk_enabled=True,
        vk_allowed_peer_ids="",
        risk_per_trade=Decimal("0.01"),
        daily_loss_limit=Decimal("0.03"),
        global_drawdown_stop=Decimal("0.15"),
        max_position_pct=Decimal("0.15"),
        max_volume_participation=Decimal("0.05"),
        atr_stop_mult=Decimal("1.5"),
        atr_take_mult=Decimal("2.0"),
        atr_trail_trigger=Decimal("1.0"),
        atr_trail_dist=Decimal("1.0"),
        max_trades_per_day=30,
        min_free_balance=Decimal("5000"),
        max_open_positions=7,
        max_per_sector=2,
        max_correlation=Decimal("0.85"),
        max_atr_ratio=Decimal("0.05"),
        max_spread_bps=Decimal("30"),
        volatile_size_scaler=Decimal("0.5"),
        session_guard_min=10,
        slippage_assumed=Decimal("0.0005"),
        fee_rate=Decimal("0.003"),
        backoff_base_sec=5,
        backoff_max_attempts=10,
        stream_reconnect_sec=5,
        candle_min_interval_sec=0.1,
        candle_max_attempts=4,
        candle_retry_base_sec=5.0,
        candle_max_retry_wait_sec=120.0,
        candle_quota_max_attempts=61,
        market_data_requests_per_minute=600,
        liquidity_workers=4,
        regime_refresh_min=DEFAULT_REGIME_REFRESH_MIN,
        ema_fast=9,
        ema_mid=21,
        ema_slow=50,
        rsi_period=14,
        rsi_oversold=Decimal("30"),
        rsi_overbought=Decimal("70"),
        rsi_trend_low=Decimal("30"),
        rsi_trend_high=Decimal("65"),
        macd_fast=12,
        macd_slow=26,
        macd_signal=9,
        bb_period=20,
        bb_mult=Decimal("2.0"),
        atr_period=14,
        adx_period=14,
        adx_trend=Decimal("25"),
        adx_range=Decimal("20"),
        adx_chaos=Decimal("40"),
        vol_window=20,
        gap_pause_pct=Decimal("0.03"),
        gap_pause_min=30,
        breadth_bearish=Decimal("0.30"),
        breadth_bullish=Decimal("0.70"),
        pool_min=15,
        pool_max=30,
        pool_liquidity_sessions=10,
        liquidity_percentile=Decimal("0.40"),
        chaos_component_period=14,
        chaos_window=500,
        chaos_threshold_pct=Decimal("0.80"),
        chaos_hysteresis=Decimal("0.05"),
        chaos_confirm_bars=2,
        paper_starting_balance=None,
        paper_candidate_tickers="",
        paper_approved_tickers="",
        paper_index_id="",
        paper_max_bar_staleness_sec=600,
        paper_max_quote_staleness_sec=30,
        _env_file=None,  # type: ignore[call-arg]
    )


def _unknown_keys(exc: ValidationError) -> list[str]:
    return [
        ".".join(str(part) for part in error["loc"])
        for error in exc.errors()
        if error["type"] == "extra_forbidden"
    ]


def _unknown_prefixed_env_keys() -> list[str]:
    """Находит `TBOT_*` ключи (в окружении и в `.env`), которых нет среди полей `Settings`.

    pydantic-settings молча игнорирует переменные окружения без соответствующего поля
    модели (это НЕ то же самое, что `extra="forbid"` для явных kwargs), поэтому
    неизвестный ключ приходится искать руками — иначе тест из docs/07_CONFIG_SPEC.md
    №2 («неизвестный ключ → явная ошибка») никогда не сработает через саму библиотеку.
    """
    known_suffixes = {name.lower() for name in Settings.model_fields}
    prefix = "TBOT_"

    candidate_keys: set[str] = set(os.environ)
    env_file = Path(".env")
    if env_file.is_file():
        # Битый .env сам по себе — это фолбэк на paper, а не повод потерять проверку
        # неизвестных ключей; при ошибке чтения просто нет доп. кандидатов из файла.
        with contextlib.suppress(OSError, UnicodeDecodeError):
            candidate_keys |= {key for key in dotenv_values(env_file) if key}

    unknown = []
    for key in candidate_keys:
        if not key.upper().startswith(prefix):
            continue
        suffix = key[len(prefix) :].lower()
        if suffix not in known_suffixes:
            unknown.append(key)
    return sorted(unknown)


def load_settings() -> Settings:
    """Загружает конфиг один раз; результат инжектируется в конструкторы выше по стеку.

    Возврат: валидный `Settings`.
    Отказ: `ConfigError`, только если ключ `TBOT_*` неизвестен (явная ошибка запуска).
    Любая другая проблема чтения/валидации — WARNING в лог и `paper` + дефолтные лимиты,
    как того требует AGENTS.md §4.1 и docs/07_CONFIG_SPEC.md (тест №1).
    """
    unknown = _unknown_prefixed_env_keys()
    if unknown:
        raise ConfigError(f"неизвестный(е) параметр(ы) конфигурации: {', '.join(unknown)}")

    try:
        settings = Settings()
    except ValidationError as exc:
        extra = _unknown_keys(exc)
        if extra:
            raise ConfigError(
                f"неизвестный(е) параметр(ы) конфигурации: {', '.join(extra)}"
            ) from exc
        logger.warning(
            "Конфигурация не прошла валидацию, фолбэк на paper + дефолтные лимиты: %s",
            exc,
        )
        return _fallback_settings()
    except (OSError, UnicodeDecodeError, ValueError, SettingsError) as exc:
        # Осознанный catch-all: файл .env физически повреждён/не читается, либо
        # `EnvSettingsSource` сама бросила `SettingsError` до наших валидаторов (было
        # воспроизведено в Docker из-за комплексного типа поля, сейчас таких полей нет,
        # но защита остаётся). Решение — не «продолжить как ни в чём не бывало», а
        # безопасный paper-фолбэк с явным логом причины (см. docs/02_CODING_RULES.md, «Исключения»).
        logger.warning("Не удалось прочитать .env, фолбэк на paper + дефолтные лимиты: %s", exc)
        return _fallback_settings()

    if settings.risk_per_trade > _RISK_PER_TRADE_WARN_THRESHOLD:
        logger.warning(
            "risk_per_trade=%s выше рекомендованного порога %s — сделка не отказана, "
            "но это заметное отклонение (docs/07_CONFIG_SPEC.md, тест №3)",
            settings.risk_per_trade,
            _RISK_PER_TRADE_WARN_THRESHOLD,
        )
    return settings
