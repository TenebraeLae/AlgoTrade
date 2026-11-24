"""Paper startup failures are visible, sanitized and cannot use legacy fallbacks."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from tests.test_auto_bootstrap import _gateway, _index, _share
from trading_bot import database
from trading_bot.api_client import (
    Candle,
    CandleQuotaExhaustedError,
    CandleRequestError,
    GatewayError,
)
from trading_bot.config import Settings
from trading_bot.main import _paper_dependencies
from trading_bot.paper_feed import PaperTickFeed
from trading_bot.portfolio import Portfolio


@pytest.mark.parametrize(
    "stage", ["calendar", "share_catalog", "imoex_discovery", "liquidity", "open_positions"]
)
def test_startup_failure_never_uses_legacy_fallback_or_modifies_positions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stage: str,
) -> None:
    settings = Settings(
        db_path=tmp_path / "failure.db",
        paper_starting_balance=Decimal("100000"),
        paper_candidate_tickers="FALLBACK",
        paper_approved_tickers="FALLBACK",
        paper_index_id="ETF-FALLBACK",
        _env_file=None,
    )
    tickers = tuple(f"TEST{i:02d}" for i in range(30))
    gateway = _gateway(
        [] if stage == "share_catalog" else [_share(ticker) for ticker in tickers],
        [] if stage == "imoex_discovery" else [_index()],
    )
    if stage == "calendar":

        def broken_calendar(_path: Path) -> set[object]:
            raise ValueError("sensitive-calendar-detail")

        monkeypatch.setattr("trading_bot.main.load_known_holidays", broken_calendar)
    monkeypatch.setattr(
        PaperTickFeed,
        "daily_volume_lots",
        lambda _self, candidates, *, sessions: {
            ticker: [index + 1] * (sessions - 1 if stage == "liquidity" else sessions)
            for index, ticker in enumerate(candidates)
        },
    )
    conn = database.connect(settings.db_path)
    if stage == "open_positions":
        Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).open_position(
            ticker="UNRESOLVED",
            side="long",
            lots=1,
            lot_size=10,
            price=Decimal("100"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
            opened_at=datetime(2026, 10, 2, tzinfo=UTC),
            stop=Decimal("90"),
        )
    before = conn.execute("SELECT * FROM trades").fetchall()
    with pytest.raises((ValueError, GatewayError)):
        _paper_dependencies(settings, conn=conn, gateway=gateway)
    assert f"stage={stage}" in caplog.text
    assert "action=stop" in caplog.text and "remedy=" in caplog.text
    assert "sensitive-calendar-detail" not in caplog.text
    assert conn.execute("SELECT * FROM trades").fetchall() == before
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    "error",
    [CandleQuotaExhaustedError("quota"), CandleRequestError("auth"), GatewayError("transport")],
)
def test_liquidity_system_failure_never_ranks_partial_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: GatewayError,
) -> None:
    settings = Settings(
        paper_starting_balance=Decimal("100000"), liquidity_workers=1, _env_file=None
    )
    gateway = _gateway([_share(f"TEST{i:02d}") for i in range(30)], [_index()])
    calls: list[str] = []

    def history(**kwargs: object) -> list[Candle]:
        calls.append(str(kwargs["instrument_id"]))
        if len(calls) == 2:
            raise error
        return []

    def no_recommendation(*args: object) -> None:
        pytest.fail("partial universe must not reach recommend_pool")

    monkeypatch.setattr(gateway, "get_candles_history", history)
    monkeypatch.setattr("trading_bot.main.recommend_pool", no_recommendation)
    conn = database.connect(tmp_path / "quota.db")
    with pytest.raises(type(error)):
        _paper_dependencies(settings, conn=conn, gateway=gateway)
    assert len(calls) == 2
    assert "stage=liquidity" in caplog.text and "action=stop" in caplog.text
    assert "pool_candidate_unavailable" not in caplog.text
    conn.close()


def test_insufficient_liquidity_logs_candidate_coverage_before_stopping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO")
    settings = Settings(paper_starting_balance=Decimal("100000"), _env_file=None)
    gateway = _gateway([_share(f"TEST{i:02d}") for i in range(30)], [_index()])
    monkeypatch.setattr(
        PaperTickFeed,
        "daily_volume_lots",
        lambda _self, candidates, *, sessions: {ticker: [] for ticker in candidates},
    )
    conn = database.connect(tmp_path / "liquidity.db")
    with pytest.raises(ValueError):
        _paper_dependencies(settings, conn=conn, gateway=gateway)
    assert "event=paper_liquidity_coverage" in caplog.text
    assert "required_sessions=10" in caplog.text
    assert "percentile=0.40" in caplog.text
    assert "'TEST00': 0" in caplog.text
    conn.close()
