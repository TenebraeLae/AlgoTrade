"""Real paper bootstrap over offline SDK UNAVAILABLE pages and temporary SQLite."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from grpc import StatusCode
from tinkoff.invest.exceptions import RequestError

from tests.test_api_client import _fake_historic_candle
from tests.test_auto_bootstrap import _index, _share
from trading_bot import database
from trading_bot.api_client import (
    CandleQuotaExhaustedError,
    CandleRequestError,
    CandleRequestTiming,
    InvestGateway,
)
from trading_bot.cancellation import CancellationToken, OperationCancelled
from trading_bot.config import CandleRequestPolicy, Settings
from trading_bot.main import _paper_dependencies
from trading_bot.paper_feed import PaperTickFeed
from trading_bot.pool import recommend_pool
from trading_bot.portfolio import Portfolio

_NOW = datetime(2026, 10, 16, 9, tzinfo=UTC)
_TICKERS = tuple(f"TEST{i:02d}" for i in range(30))


class OfflineTransport:
    """Only the SDK context, candle transport and monotonic clock are synthetic."""

    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []
        self.token = CancellationToken()
        self.calls: list[tuple[str, datetime, datetime]] = []
        self.successes: list[tuple[str, datetime, datetime]] = []
        self.failure_id = "id-TEST00"
        self.failure_page: datetime | None = None
        self.failures = 0
        self.persistent = False
        self.cancel_on_wait = False
        self.instruments = SimpleNamespace(
            shares=lambda **_: SimpleNamespace(instruments=[_share(t) for t in _TICKERS]),
            find_instrument=lambda **_: SimpleNamespace(instruments=[_index()]),
        )
        self.market_data = SimpleNamespace(get_candles=self.get_candles)

    def __enter__(self) -> OfflineTransport:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        self.now += seconds
        if self.cancel_on_wait and seconds >= 5:
            self.token.request_stop()

    def get_candles(self, **kwargs: object) -> SimpleNamespace:
        """Return real complete session grids, failing only the chosen second page."""
        instrument_id = str(kwargs["instrument_id"])
        start, end = kwargs["from_"], kwargs["to"]
        assert isinstance(start, datetime) and isinstance(end, datetime)
        page = (instrument_id, start, end)
        prior = [call for call in self.calls if call[0] == instrument_id]
        self.calls.append(page)
        if instrument_id == self.failure_id:
            if self.failure_page is None and prior:
                self.failure_page = start
            if start == self.failure_page and (self.persistent or self.failures == 0):
                self.failures += 1
                raise RequestError(StatusCode.UNAVAILABLE, "synthetic transport details", None)
        candles = []
        day = start.replace(hour=7, minute=0, second=0, microsecond=0)
        while day <= end:
            if day.weekday() < 5:
                for offset in range(164):
                    ts = day + timedelta(minutes=5 * offset)
                    if start <= ts <= end:
                        volume = _TICKERS.index(instrument_id.removeprefix("id-")) + 1
                        candles.append(_fake_historic_candle(100, volume, ts))
            day += timedelta(days=1)
        self.successes.append(page)
        return SimpleNamespace(candles=candles)


@pytest.fixture
def bootstrap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Settings, sqlite3.Connection, OfflineTransport, InvestGateway, Mock]]:
    """Use production catalog, history, volume aggregation, ranking and pool journal."""
    fixed_datetime = Mock(wraps=datetime)
    fixed_datetime.now.return_value = _NOW
    monkeypatch.setattr("trading_bot.main.datetime", fixed_datetime)
    ranking = Mock(wraps=recommend_pool)
    monkeypatch.setattr("trading_bot.main.recommend_pool", ranking)
    settings = Settings(
        paper_starting_balance=Decimal("100000"),
        db_path=tmp_path / "unavailable.db",
        liquidity_workers=1,  # This fixture's monotonic clock and exact call order are serial.
        _env_file=None,
    )
    transport = OfflineTransport()
    gateway = InvestGateway(
        "synthetic-unused",
        target="offline-no-network",
        client_factory=lambda *_: transport,
        candle_policy=CandleRequestPolicy(min_interval_sec=0.1, max_attempts=3),
        candle_timing=CandleRequestTiming(
            monotonic=lambda: transport.now,
            sleep=transport.sleep,
            should_stop=transport.token.is_cancelled,
        ),
    )
    with closing(database.connect(settings.db_path)) as conn:
        yield settings, conn, transport, gateway, ranking


@pytest.fixture
def publication() -> Iterator[Mock]:
    """Observe public publication without replacing its real state transition."""
    with patch.object(
        PaperTickFeed, "apply_universe", autospec=True, side_effect=PaperTickFeed.apply_universe
    ) as publish:
        yield publish


def test_transient_second_page_reaches_full_ranking_without_replaying_success(
    bootstrap: tuple[Settings, sqlite3.Connection, OfflineTransport, InvestGateway, Mock],
    publication: Mock,
) -> None:
    """A recovered page joins all candidates before a real recommendation is published."""
    settings, conn, transport, gateway, ranking = bootstrap
    dependencies = _paper_dependencies(settings, conn=conn, gateway=gateway)

    first, second = transport.calls[:2]
    assert transport.calls[:3] == [first, second, second]
    assert first[0] == second[0] and first[1] < second[1]
    assert transport.calls.count(first) == 1
    assert transport.successes.count(second) == 1
    assert transport.waits.count(5) == 1
    assert transport.failures == 1
    ranking.assert_called_once()
    volumes = ranking.call_args.args[0]
    assert tuple(volumes) == _TICKERS
    assert volumes == {ticker: [164 * (i + 1)] * 10 for i, ticker in enumerate(_TICKERS)}
    assert dependencies.paper_cycle is not None
    publication.assert_called_once()
    payloads = conn.execute(
        "SELECT ctx FROM event_log WHERE event='pool_refresh_applied'"
    ).fetchall()
    assert len(payloads) == 1
    payload = json.loads(payloads[0][0])
    assert len(payload["selected"]) == 18
    assert payload["selected"][0] == "TEST29"
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)


def _seed_prior_paper_state(conn: sqlite3.Connection) -> None:
    """Persist an unrelated position and unresolved bar claim to protect during bootstrap."""
    from trading_bot.database import DecisionRecord, save_decision

    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    portfolio.open_position(
        ticker="PRIOR",
        side="long",
        lots=1,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_NOW - timedelta(days=1),
        stop=Decimal("90"),
    )
    save_decision(
        conn,
        DecisionRecord(
            ts=_NOW - timedelta(minutes=5),
            ticker="CLAIMED",
            regime="chaos",
            side=None,
            indicators={"status": "n/a"},
            filters_passed=(),
            filters_failed=("source unavailable",),
            size_lots=None,
            verdict="no_signal",
            reason="operator reconciliation required",
            mode="paper",
            source_bar_ts=_NOW - timedelta(minutes=5),
            processing_state="pending",
        ),
    )
    conn.commit()


def _database_state(conn: sqlite3.Connection) -> tuple[str, ...]:
    """Capture all durable SQLite content, including portfolio and claim state."""
    return tuple(conn.iterdump())


def test_persistent_unavailable_exhausts_same_page_without_partial_bootstrap(
    bootstrap: tuple[Settings, sqlite3.Connection, OfflineTransport, InvestGateway, Mock],
    caplog: pytest.LogCaptureFixture,
    publication: Mock,
) -> None:
    """Exhaustion after candidate data cannot rank, publish, exclude, or alter prior state."""
    settings, conn, transport, gateway, ranking = bootstrap
    transport.failure_id = "id-TEST01"
    transport.persistent = True
    _seed_prior_paper_state(conn)
    before = _database_state(conn)

    with pytest.raises(CandleRequestError) as error:
        _paper_dependencies(settings, conn=conn, gateway=gateway)

    assert not isinstance(error.value, CandleQuotaExhaustedError)
    assert transport.failures == 3
    assert transport.waits.count(5) == 2
    first_candidate_calls = [call for call in transport.calls if call[0] == "id-TEST00"]
    failed_candidate_calls = [call for call in transport.calls if call[0] == "id-TEST01"]
    assert first_candidate_calls and len(transport.successes) == len(first_candidate_calls) + 1
    assert len(failed_candidate_calls) == 4
    assert failed_candidate_calls[0][1] < failed_candidate_calls[1][1]
    assert len(set(failed_candidate_calls[1:])) == 1
    assert all(call[0] in {"id-TEST00", "id-TEST01"} for call in transport.calls)
    ranking.assert_not_called()
    publication.assert_not_called()
    assert _database_state(conn) == before
    assert conn.execute("SELECT processing_state FROM decisions").fetchall() == [("pending",)]
    assert conn.execute("SELECT ticker FROM positions").fetchall() == [("PRIOR",)]
    assert "pool_candidate_unavailable" not in caplog.text


def test_cancellation_during_unavailable_retry_wait_preserves_prior_state(
    bootstrap: tuple[Settings, sqlite3.Connection, OfflineTransport, InvestGateway, Mock],
    caplog: pytest.LogCaptureFixture,
    publication: Mock,
) -> None:
    """Stop during retry wait raises cancellation, before another RPC or pool side effect."""
    settings, conn, transport, gateway, ranking = bootstrap
    transport.cancel_on_wait = True
    _seed_prior_paper_state(conn)
    before = _database_state(conn)

    with pytest.raises(OperationCancelled):
        _paper_dependencies(settings, conn=conn, gateway=gateway)

    assert transport.calls[0][0] == "id-TEST00"
    assert len(transport.calls) == 2
    assert transport.waits[-1] == 5
    ranking.assert_not_called()
    publication.assert_not_called()
    assert _database_state(conn) == before
    assert conn.execute("SELECT processing_state FROM decisions").fetchall() == [("pending",)]
    assert conn.execute("SELECT ticker FROM positions").fetchall() == [("PRIOR",)]
    assert conn.execute("SELECT COUNT(*) FROM paper_source_episodes").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone() == (0,)
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
