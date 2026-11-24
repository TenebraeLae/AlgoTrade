"""Real installed SIGINT handler must cancel startup without entering the paper loop."""

import signal
import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Timer
from time import monotonic

import pytest

from tests.test_auto_bootstrap import _gateway, _index, _share
from tests.test_paper_cycle_integration import _cycle, _tick
from trading_bot import database
from trading_bot.api_client import Candle, CandleRequestTiming, InvestGateway
from trading_bot.cancellation import CancellationToken, OperationCancelled
from trading_bot.config import CandleRequestPolicy, Settings
from trading_bot.executor import PaperTick
from trading_bot.main import RunDependencies, _install_signal_handlers, main, run


def test_sigint_during_bootstrap_closes_database_without_starting_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = Settings(
        db_path=tmp_path / "cancel.db",
        paper_starting_balance=Decimal("100000"),
        _env_file=None,
    )
    monkeypatch.setattr("trading_bot.main.load_settings", lambda: settings)
    monkeypatch.setattr("trading_bot.main.setup_logging", lambda _level: None)
    monkeypatch.setattr("trading_bot.main.get_stored_token", lambda _name: "synthetic")
    monkeypatch.setattr("trading_bot.main.sandbox_target", lambda: "sandbox")
    monkeypatch.setattr("trading_bot.main.InvestGateway", lambda *_args, **_kwargs: object())
    connections: list[sqlite3.Connection] = []
    runs: list[bool] = []

    def dependencies(
        _settings: Settings, *, conn: sqlite3.Connection, gateway: object
    ) -> RunDependencies:
        connections.append(conn)
        signal.raise_signal(signal.SIGINT)
        return RunDependencies()

    monkeypatch.setattr("trading_bot.main._paper_dependencies", dependencies)
    monkeypatch.setattr("trading_bot.main.run", lambda *_args, **_kwargs: runs.append(True))
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    caplog.set_level("INFO")
    try:
        code = main()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    assert code == 0
    assert runs == []
    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError):
        connections[0].execute("SELECT 1")
    assert not (tmp_path / "heartbeat").exists()
    assert "event=tbot_stopped reason=operator_request" in caplog.text
    assert not [record for record in caplog.records if record.levelname == "ERROR"]


def test_sigint_in_actual_liquidity_stage_stops_before_next_candidate_and_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Cancellation propagates through real main/bootstrap/feed and closes SQLite."""
    settings = Settings(
        db_path=tmp_path / "liquidity-cancel.db",
        paper_starting_balance=Decimal("100000"),
        _env_file=None,
    )
    monkeypatch.setattr("trading_bot.main.load_settings", lambda: settings)
    monkeypatch.setattr("trading_bot.main.setup_logging", lambda _level: None)
    monkeypatch.setattr("trading_bot.main.get_stored_token", lambda _name: "synthetic")
    monkeypatch.setattr("trading_bot.main.sandbox_target", lambda: "sandbox")
    catalog_gateway = _gateway([_share(f"TEST{i:02d}") for i in range(30)], [_index()])
    client = catalog_gateway._make_client()
    requests: list[str] = []

    def history(
        *, instrument_id: str, timeframe: str, from_: datetime, to: datetime
    ) -> list[Candle]:
        requests.append(instrument_id)
        signal.raise_signal(signal.SIGINT)
        return []

    def gateway_factory(
        token: str,
        *,
        target: str,
        candle_policy: CandleRequestPolicy,
        candle_timing: CandleRequestTiming,
    ) -> InvestGateway:
        gateway = InvestGateway(
            token,
            target=target,
            client_factory=lambda *_args: client,
            candle_policy=candle_policy,
            candle_timing=candle_timing,
        )
        monkeypatch.setattr(gateway, "get_candles_history", history)
        return gateway

    monkeypatch.setattr("trading_bot.main.InvestGateway", gateway_factory)
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    caplog.set_level("INFO")
    try:
        code = main()
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)

    assert code == 0
    assert requests == ["id-TEST00"]
    assert "event=paper_bootstrap_cancelled stage=liquidity" in caplog.text
    assert "paper_pool_applied" not in caplog.text
    assert "pool_candidate_unavailable" not in caplog.text
    assert not [record for record in caplog.records if record.levelname == "ERROR"]
    with sqlite3.connect(settings.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0


def test_sigint_wakes_long_wait_through_installed_handler() -> None:
    """Exercise signal dispatch during a quota-style wait, not KeyboardInterrupt."""
    token = CancellationToken()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    _install_signal_handlers(token)
    timer = Timer(0.02, lambda: signal.raise_signal(signal.SIGINT))
    started = monotonic()
    try:
        timer.start()
        with pytest.raises(OperationCancelled):
            token.sleep(10)
    finally:
        timer.cancel()
        timer.join()
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    assert token.is_cancelled()
    assert monotonic() - started < 2


def test_stop_token_is_one_way_and_wait_does_not_consume_stop() -> None:
    token = CancellationToken()
    assert not token.is_cancelled()
    token.sleep(0)
    token.request_stop()
    for _ in range(2):
        with pytest.raises(OperationCancelled):
            token.sleep(100)
        with pytest.raises(OperationCancelled):
            token.check()
        assert token.is_cancelled()


def test_sigint_inside_accepted_fill_finishes_decision_and_ack_without_next_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stop event must not tear a pending paper claim in the middle of a fill."""
    conn = database.connect(tmp_path / "accepted.db")
    cycle = _cycle(conn)
    token = CancellationToken()
    fill = cycle._broker.fill_market_order
    ticks: list[PaperTick] = []
    acks: list[PaperTick] = []
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    _install_signal_handlers(token)

    def fill_and_stop(*args: object, **kwargs: object) -> object:
        signal.raise_signal(signal.SIGINT)
        return fill(*args, **kwargs)

    def next_tick() -> PaperTick:
        tick = _tick()
        ticks.append(tick)
        return tick

    monkeypatch.setattr(cycle._broker, "fill_market_order", fill_and_stop)
    try:
        run(
            Settings(_env_file=None),
            heartbeat_path=tmp_path / "heartbeat",
            dependencies=RunDependencies(
                paper_cycle=cycle,
                next_paper_tick=next_tick,
                ack_paper_tick=acks.append,
                should_stop=token.is_cancelled,
                sleep=token.sleep,
            ),
        )
        assert token.is_cancelled()
        assert len(ticks) == 1
        assert acks == ticks
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
        states = conn.execute("SELECT processing_state FROM decisions").fetchall()
        assert [row[0] for row in states] == ["complete"]
    finally:
        conn.close()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


@pytest.mark.parametrize("duration", [-1, float("inf"), float("nan")])
def test_wait_rejects_invalid_duration(duration: float) -> None:
    with pytest.raises(ValueError):
        CancellationToken().sleep(duration)
