"""Календарь MOEX 2026: политика обычной сессии, не производственный календарь."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from trading_bot import utils
from trading_bot.api_client import GatewayError
from trading_bot.utils import MSK, can_trade_now, load_known_holidays

_SEED = Path(__file__).resolve().parents[1] / "data_seed" / "holidays_ru.md"
_NON_TRADING = ["2026-01-01", "2026-01-02", "2026-01-07", "2026-03-08", "2026-05-09", "2026-12-31"]
_SPECIAL = ["2026-02-23", "2026-05-01", "2026-06-12", "2026-11-04"]


class _Gateway:
    def __init__(self, *, status: str = "SECURITY_TRADING_STATUS_NORMAL_TRADING") -> None:
        self.status = status
        self.calls = 0

    def get_trading_status(self, *, instrument_id: str) -> str:
        self.calls += 1
        if self.status == "unavailable":
            raise GatewayError("offline")
        return self.status


@pytest.mark.parametrize("blocked_date", _NON_TRADING + _SPECIAL)
def test_seed_has_confirmed_closures_and_policy_blocked_special_sessions(blocked_date: str) -> None:
    assert date.fromisoformat(blocked_date) in load_known_holidays(_SEED)


@pytest.mark.parametrize("blocked_date", _NON_TRADING + _SPECIAL)
def test_calendar_and_order_checks_reject_seed_dates_without_status_call(blocked_date: str) -> None:
    holidays = frozenset(load_known_holidays(_SEED))
    now = datetime.combine(date.fromisoformat(blocked_date), datetime.min.time(), tzinfo=MSK)
    now = now.replace(hour=12)
    gateway = _Gateway()

    calendar = utils.session_calendar_decision(now=now, known_holidays=holidays)
    decision = can_trade_now(
        gateway,  # type: ignore[arg-type]
        instrument_id="uid",
        now=now,
        session_guard_min=0,
        known_holidays=holidays,
    )

    assert not calendar.allowed
    assert decision == calendar
    assert decision.refusal_filter == "session_open"
    assert gateway.calls == 0


@pytest.mark.parametrize(
    "trading_date",
    ["2026-01-05", "2026-01-06", "2026-01-08", "2026-01-09", "2026-03-09", "2026-05-11"],
)
def test_official_non_working_weekdays_can_be_regular_moex_trading_days(trading_date: str) -> None:
    holidays = frozenset(load_known_holidays(_SEED))
    now = datetime.fromisoformat(f"{trading_date}T12:00:00+03:00")
    assert utils.session_calendar_decision(now=now, known_holidays=holidays).allowed


@pytest.mark.parametrize("year", [2025, 2027, 2030])
def test_unknown_year_is_refused_even_with_normal_api_status(year: int) -> None:
    gateway = _Gateway()
    # 15 January is not a Russian public holiday; choose a weekday in each year.
    now = datetime(year, 1, 15, 12, tzinfo=MSK)
    decision = can_trade_now(
        gateway,  # type: ignore[arg-type]
        instrument_id="uid",
        now=now,
        session_guard_min=0,
        known_holidays=frozenset(load_known_holidays(_SEED)),
    )
    assert not decision.allowed
    assert "покрыт" in decision.reason
    assert gateway.calls == 0


@pytest.mark.parametrize(
    ("now", "allowed"),
    [
        (datetime(2026, 1, 6, 6, 59, tzinfo=UTC), False),
        (datetime(2026, 1, 6, 7, 0, tzinfo=UTC), True),
        (datetime(2026, 1, 6, 20, 39, 59, tzinfo=UTC), True),
        (datetime(2026, 1, 6, 20, 40, tzinfo=UTC), False),
        (datetime(2026, 1, 6, 21, 0, tzinfo=UTC), False),  # 7 January MSK
        (datetime(2026, 2, 22, 21, 0, tzinfo=UTC), False),  # special session MSK date
        (datetime(2026, 1, 17, 12, tzinfo=MSK), False),  # MOEX weekend session
        (datetime(2026, 1, 18, 12, tzinfo=MSK), False),
    ],
)
def test_calendar_uses_msk_date_weekday_and_regular_clock(now: datetime, allowed: bool) -> None:
    decision = utils.session_calendar_decision(
        now=now, known_holidays=frozenset(load_known_holidays(_SEED))
    )
    assert decision.allowed is allowed


def test_calendar_helper_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        utils.session_calendar_decision(
            now=datetime(2026, 1, 6, 12), known_holidays=frozenset()  # noqa: DTZ001
        )


@pytest.mark.parametrize("status", ["unavailable", "SECURITY_TRADING_STATUS_BREAK_IN_TRADING"])
def test_known_regular_day_never_overrides_api_refusal(status: str) -> None:
    gateway = _Gateway(status=status)
    decision = can_trade_now(
        gateway,  # type: ignore[arg-type]
        instrument_id="uid",
        now=datetime(2026, 1, 6, 12, tzinfo=MSK),
        session_guard_min=0,
        known_holidays=frozenset(load_known_holidays(_SEED)),
    )
    assert not decision.allowed
    assert decision.refusal_filter == "instrument_status_normal"
    assert gateway.calls == 1


@pytest.mark.parametrize(
    "content",
    [
        b"\xff\xfe",
        b"",
        b"broken table",
        "| 2026-02-30 | нерабочий день | source | 2026-10-16 |".encode(),
        (
            "| 2026-01-01 | нерабочий день | source | checked |\n"
            "| 2026-02-30 | нерабочий день | source | checked |"
        ).encode(),
        b"| 2026-01-01 | unknown | source | 2026-10-16 |",
        "| 2027-01-01 | нерабочий день | source | 2026-10-16 |".encode(),
    ],
    ids=[
        "bad-utf8",
        "empty",
        "bad-table",
        "bad-date",
        "partially-bad",
        "bad-status",
        "unsupported-year",
    ],
)
def test_invalid_calendar_cannot_be_loaded_as_permission_to_trade(
    tmp_path: Path, content: bytes
) -> None:
    calendar = tmp_path / "calendar.md"
    calendar.write_bytes(content)
    with pytest.raises(ValueError, match="календар"):
        load_known_holidays(calendar)
