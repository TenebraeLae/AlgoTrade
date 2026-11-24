"""Benchmark safety boundaries are exercised offline, never against sandbox."""

import signal
from datetime import UTC, datetime

import pytest

from tests.benchmark_liquidity import Budget, MeasuredGateway, SimulatedClient, main
from tests.test_candle_requests import Clock
from trading_bot.cancellation import OperationCancelled


def test_benchmark_hard_actual_request_cap_includes_all_callbacks() -> None:
    clock = Clock()
    budget = Budget(limit=2)
    client = SimulatedClient(0.001)
    gateway = MeasuredGateway(
        "synthetic-unused",
        target="offline",
        budget=budget,
        client_factory=lambda *_: client,
        candle_timing=clock.timing(),
    )
    now = datetime(2026, 10, 1, tzinfo=UTC)
    for _ in range(2):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=now, to=now)
    with pytest.raises(OperationCancelled):
        gateway.get_candles(instrument_id="ID", timeframe="5m", from_=now, to=now)
    assert budget.count == len(gateway.starts) == 2
    assert gateway.inflight == 0
    assert gateway.peak == 1


def test_sandbox_benchmark_rejects_missing_explicit_range_before_keyring(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def forbidden_keyring(_name: str) -> str:
        pytest.fail("missing explicit historical range must not access credentials")

    before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    monkeypatch.setattr("tests.benchmark_liquidity.get_stored_token", forbidden_keyring)
    monkeypatch.setattr("sys.argv", ["benchmark", "--sandbox"])
    assert main() == 1
    assert '"error_type": "ValueError"' in capsys.readouterr().out
    assert before == {sig: signal.getsignal(sig) for sig in before}
