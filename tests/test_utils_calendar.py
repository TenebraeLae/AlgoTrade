"""Тесты utils.py: календарь/сессия MOEX, консервативные фолбэки."""

from __future__ import annotations

from datetime import date, datetime, tzinfo
from pathlib import Path

import pytest

from trading_bot.api_client import GatewayError
from trading_bot.utils import (
    MSK,
    TradingDecision,
    can_trade_now,
    load_known_holidays,
    session_guard_active,
)

_MSK_MIDDAY = datetime(2026, 1, 6, 12, 0, tzinfo=MSK)  # вторник, торговое окно


class _FakeGateway:
    def __init__(self, *, status: str | None = None, error: Exception | None = None) -> None:
        self._status = status
        self._error = error
        self.calls = 0

    def get_trading_status(self, *, instrument_id: str) -> str:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._status is not None
        return self._status


def test_allows_trading_when_normal_status_and_inside_window() -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    decision = can_trade_now(
        gateway,
        instrument_id="BBG004730N88",
        now=_MSK_MIDDAY,
        session_guard_min=0,
    )  # type: ignore[arg-type]

    assert decision == TradingDecision(
        allowed=True,
        reason="SECURITY_TRADING_STATUS_NORMAL_TRADING",
        trading_status="SECURITY_TRADING_STATUS_NORMAL_TRADING",
    )
    assert decision.trading_status == "SECURITY_TRADING_STATUS_NORMAL_TRADING"


def test_rejects_weekend_without_calling_api() -> None:
    saturday = datetime(2026, 1, 10, 12, 0, tzinfo=MSK)
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    decision = can_trade_now(
        gateway, instrument_id="BBG004730N88", now=saturday, session_guard_min=10
    )  # type: ignore[arg-type]

    assert decision.allowed is False
    assert "выходной" in decision.reason


def test_rejects_known_holiday_without_calling_api() -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")
    holiday = date(2026, 1, 6)

    decision = can_trade_now(
        gateway,  # type: ignore[arg-type]
        instrument_id="BBG004730N88",
        now=_MSK_MIDDAY,
        session_guard_min=10,
        known_holidays=frozenset({holiday}),
    )

    assert decision.allowed is False
    assert "праздничный" in decision.reason


def test_rejects_outside_session_window() -> None:
    before_open = datetime(2026, 1, 6, 8, 0, tzinfo=MSK)
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    decision = can_trade_now(
        gateway, instrument_id="BBG004730N88", now=before_open, session_guard_min=10
    )  # type: ignore[arg-type]

    assert decision.allowed is False
    assert "вне торгового окна" in decision.reason


@pytest.mark.parametrize(
    "auction_time",
    [
        datetime(2026, 1, 6, 9, 50, tzinfo=MSK),
        datetime(2026, 1, 6, 9, 55, tzinfo=MSK),
        datetime(2026, 1, 6, 23, 40, tzinfo=MSK),
        datetime(2026, 1, 6, 23, 45, tzinfo=MSK),
    ],
    ids=["opening-start", "opening-middle", "closing-start", "closing-middle"],
)
def test_rejects_auctions_even_when_session_guard_is_disabled(
    auction_time: datetime,
) -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    decision = can_trade_now(
        gateway,
        instrument_id="BBG004730N88",
        now=auction_time,
        session_guard_min=0,
    )  # type: ignore[arg-type]

    assert decision.allowed is False
    assert "аукцион" in decision.reason
    assert gateway.calls == 0


@pytest.mark.parametrize(
    "regular_session_time",
    [datetime(2026, 1, 6, 10, 0, tzinfo=MSK), datetime(2026, 1, 6, 23, 39, tzinfo=MSK)],
    ids=["first-minute", "last-minute-before-closing-auction"],
)
def test_allows_regular_session_boundaries_before_auction(
    regular_session_time: datetime,
) -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    decision = can_trade_now(
        gateway,
        instrument_id="BBG004730N88",
        now=regular_session_time,
        session_guard_min=0,
    )  # type: ignore[arg-type]

    assert decision.allowed is True
    assert gateway.calls == 1


def test_rejects_at_end_of_closing_auction_as_outside_session() -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    decision = can_trade_now(
        gateway,
        instrument_id="BBG004730N88",
        now=datetime(2026, 1, 6, 23, 50, tzinfo=MSK),
        session_guard_min=0,
    )  # type: ignore[arg-type]

    assert decision.allowed is False
    assert "вне торгового окна" in decision.reason
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("time_of_day", "expected_allowed", "expected_guard"),
    [
        (datetime(2026, 1, 6, 10, 0, tzinfo=MSK), False, True),
        (datetime(2026, 1, 6, 10, 9, 59, tzinfo=MSK), False, True),
        (datetime(2026, 1, 6, 10, 10, tzinfo=MSK), True, False),
        (datetime(2026, 1, 6, 23, 30, tzinfo=MSK), False, True),
        (datetime(2026, 1, 6, 23, 39, 59, tzinfo=MSK), False, True),
        (datetime(2026, 1, 6, 23, 40, tzinfo=MSK), False, False),
    ],
    ids=[
        "open-start",
        "open-before-guard-end",
        "open-guard-end",
        "close-start",
        "close-end",
        "auction",
    ],
)
def test_can_trade_now_applies_regular_session_guard(
    time_of_day: datetime, expected_allowed: bool, expected_guard: bool
) -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    decision = can_trade_now(
        gateway,
        instrument_id="BBG004730N88",
        now=time_of_day,
        session_guard_min=10,
    )  # type: ignore[arg-type]

    assert decision.allowed is expected_allowed
    assert decision.guard_active is expected_guard
    assert gateway.calls == int(expected_allowed)


def test_can_trade_now_requires_explicit_guard_policy() -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    with pytest.raises(TypeError, match="session_guard_min"):
        can_trade_now(
            gateway, instrument_id="BBG004730N88", now=_MSK_MIDDAY
        )  # type: ignore[call-arg, arg-type]
    assert gateway.calls == 0


def test_session_guard_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        session_guard_active(now=datetime(2026, 1, 6, 10, 5), guard_min=10)  # noqa: DTZ001


def test_session_guard_rejects_overlapping_open_and_close_windows() -> None:
    with pytest.raises(ValueError, match="overlap"):
        session_guard_active(now=_MSK_MIDDAY, guard_min=410)


@pytest.mark.parametrize(
    ("minute", "expected_guard"),
    [(49, False), (50, False), (51, True)],
    ids=["opening-edge", "free-gap", "closing-edge"],
)
def test_session_guard_409_minutes_leaves_only_the_middle_gap(
    minute: int, expected_guard: bool
) -> None:
    now = datetime(2026, 1, 6, 16, minute, tzinfo=MSK)
    assert session_guard_active(now=now, guard_min=409) is expected_guard


def test_session_guard_rejects_negative_window() -> None:
    with pytest.raises(ValueError, match="guard_min"):
        session_guard_active(now=_MSK_MIDDAY, guard_min=-1)


@pytest.mark.parametrize(
    "outside_time",
    [
        datetime(2026, 1, 6, 8, 0, tzinfo=MSK),
        datetime(2026, 1, 6, 9, 55, tzinfo=MSK),
        datetime(2026, 1, 6, 23, 45, tzinfo=MSK),
        datetime(2026, 1, 6, 23, 50, tzinfo=MSK),
    ],
)
def test_session_guard_is_inactive_outside_regular_trading(outside_time: datetime) -> None:
    assert session_guard_active(now=outside_time, guard_min=10) is False


def test_can_trade_now_rejects_tzinfo_without_utc_offset() -> None:
    class NoOffsetZone(tzinfo):
        def utcoffset(self, dt: datetime | None) -> None:
            return None

        def dst(self, dt: datetime | None) -> None:
            return None

        def tzname(self, dt: datetime | None) -> str:
            return "unknown"

    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")
    with pytest.raises(ValueError, match="tz-aware"):
        can_trade_now(
            gateway,
            instrument_id="BBG004730N88",
            now=datetime(2026, 1, 6, 12, tzinfo=NoOffsetZone()),
            session_guard_min=0,
        )  # type: ignore[arg-type]
    assert gateway.calls == 0


def test_rejects_non_normal_trading_status() -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_BREAK_IN_TRADING")

    decision = can_trade_now(
        gateway, instrument_id="BBG004730N88", now=_MSK_MIDDAY, session_guard_min=10
    )  # type: ignore[arg-type]

    assert decision.allowed is False
    assert "BREAK_IN_TRADING" in decision.reason


def test_conservative_rejection_when_api_unavailable() -> None:
    """data_seed/holidays_ru.md: «никогда не открывать позиции на всякий случай»."""
    gateway = _FakeGateway(error=GatewayError("сеть недоступна"))

    decision = can_trade_now(
        gateway, instrument_id="BBG004730N88", now=_MSK_MIDDAY, session_guard_min=10
    )  # type: ignore[arg-type]

    assert decision.allowed is False
    assert "недоступен" in decision.reason


def test_status_error_details_never_enter_decision_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    gateway = _FakeGateway(error=GatewayError("sensitive-test-payload"))

    decision = can_trade_now(
        gateway, instrument_id="BBG004730N88", now=_MSK_MIDDAY, session_guard_min=10
    )  # type: ignore[arg-type]

    assert decision.allowed is False
    assert "sensitive-test-payload" not in decision.reason
    assert "sensitive-test-payload" not in caplog.text


def test_rejects_naive_datetime() -> None:
    gateway = _FakeGateway(status="SECURITY_TRADING_STATUS_NORMAL_TRADING")

    with pytest.raises(ValueError):
        can_trade_now(
            gateway,
            instrument_id="BBG004730N88",
            now=datetime(2026, 1, 6, 12, 0),  # noqa: DTZ001
            session_guard_min=10,
        )  # type: ignore[arg-type]


def test_load_known_holidays_rejects_template_file(tmp_path: Path) -> None:
    """Шаблон без подтверждённых дат не должен разрешать запуск runtime."""
    template = tmp_path / "holidays_ru.md"
    template.write_text(
        "| Дата | Статус | Источник | Проверено |\n"
        "|---|---|---|---|\n"
        "| YYYY-MM-DD | нерабочий день | VERIFY | - |\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="календар"):
        load_known_holidays(template)


def test_load_known_holidays_parses_confirmed_rows(tmp_path: Path) -> None:
    confirmed = tmp_path / "holidays_ru.md"
    confirmed.write_text(
        "| Дата | Статус | Источник | Проверено |\n"
        "|---|---|---|---|\n"
        "| 2026-01-01 | нерабочий день | moex.ru/calendar | 2026-01-01 |\n"
        "| 2026-01-02 | рабочий день | moex.ru/calendar | 2026-01-01 |\n",
        encoding="utf-8",
    )

    holidays = load_known_holidays(confirmed)

    assert holidays == {date(2026, 1, 1)}


def test_load_known_holidays_missing_file_stops_startup(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="календар"):
        load_known_holidays(tmp_path / "does_not_exist.md")
