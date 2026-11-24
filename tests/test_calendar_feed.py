"""Календарь runtime блокирует feed до RPC и до стратегического решения."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_paper_feed import FakeGateway, _classifier, _config
from trading_bot.paper_feed import PaperTickFeed, _regular_session_expected
from trading_bot.utils import load_known_holidays

_SEED = Path(__file__).resolve().parent.parent / "data_seed/holidays_ru.md"


@pytest.mark.parametrize(
    "now",
    [
        datetime(2026, 1, 1, 9, tzinfo=UTC),
        datetime(2026, 2, 23, 9, tzinfo=UTC),
        datetime(2026, 11, 4, 9, tzinfo=UTC),
        datetime(2026, 10, 17, 9, tzinfo=UTC),
        datetime(2027, 1, 4, 9, tzinfo=UTC),
    ],
)
def test_calendar_denial_prevents_feed_rpc_and_explains_reason(
    now: datetime, caplog: pytest.LogCaptureFixture
) -> None:
    gateway = FakeGateway(fail_share={"SBER"})
    feed = PaperTickFeed(
        gateway=gateway,  # type: ignore[arg-type]
        account_source=object(),  # type: ignore[arg-type]
        regime_classifier=_classifier(),
        strategy_config=_config(),
        approved_tickers=["SBER"],
        index_instrument_id="INDEX-ID",
        clock=lambda: now,
        max_bar_staleness=timedelta(minutes=10),
        max_quote_staleness=timedelta(seconds=30),
        known_holidays=frozenset(load_known_holidays(_SEED)),
    )
    assert feed.next_paper_tick() is None
    assert "event=paper_feed_calendar_closed" in caplog.text
    assert "reason=" in caplog.text
    assert gateway.status_calls == []


def test_unknown_calendar_year_does_not_expect_regular_bars() -> None:
    assert not _regular_session_expected(
        datetime(2027, 1, 4, 9, tzinfo=UTC), frozenset(load_known_holidays(_SEED))
    )
