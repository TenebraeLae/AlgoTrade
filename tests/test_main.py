"""Тесты main.py: отказ live без LiveBroker, heartbeat-цикл, graceful stop."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest

from tests.test_auto_bootstrap import _gateway, _index, _share
from tests.test_paper_cycle_integration import _account_snapshot, _cycle, _tick
from trading_bot import database
from trading_bot.api_client import CandleRequestTiming
from trading_bot.config import CandleRequestPolicy, Settings
from trading_bot.executor import PaperTick
from trading_bot.main import (
    LiveModeNotReadyError,
    RunDependencies,
    _paper_dependencies,
    guard_trading_mode,
    main,
    run,
    write_heartbeat,
)
from trading_bot.paper_feed import PaperFeedError, PaperTickFeed
from trading_bot.source_events import SourceRefusal


def _settings(**overrides: object) -> Settings:
    return Settings(
        trading_mode=overrides.get("trading_mode", "paper"),
        secrets_path=Path("secrets/store.bin"),
        db_path=Path("data/tbot.db"),
        dashboard_port=8080,
        dashboard_user="admin",
        tz_display="Europe/Moscow",
        log_level="INFO",
        vk_enabled=True,
        vk_allowed_peer_ids="",
        risk_per_trade=overrides.get("risk_per_trade", "0.01"),
        daily_loss_limit="0.03",
        backoff_base_sec=5,
        backoff_max_attempts=10,
        stream_reconnect_sec=5,
        _env_file=None,  # type: ignore[call-arg]
    )


def test_guard_trading_mode_allows_paper() -> None:
    guard_trading_mode(_settings(trading_mode="paper"))  # не бросает


def test_guard_trading_mode_rejects_live_without_live_broker() -> None:
    with pytest.raises(LiveModeNotReadyError):
        guard_trading_mode(_settings(trading_mode="live"))


def test_write_heartbeat_creates_parent_dirs_and_file(tmp_path: Path) -> None:
    heartbeat = tmp_path / "nested" / "heartbeat"

    write_heartbeat(heartbeat)

    assert heartbeat.is_file()
    assert heartbeat.read_text(encoding="utf-8").strip() != ""


def test_run_writes_heartbeat_and_stops_when_asked(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"
    calls = {"count": 0}

    def fake_sleep(_seconds: float) -> None:
        calls["count"] += 1

    def should_stop() -> bool:
        return calls["count"] >= 3

    run(
        _settings(trading_mode="paper"),
        heartbeat_path=heartbeat,
        dependencies=RunDependencies(sleep=fake_sleep, should_stop=should_stop),
    )

    assert heartbeat.is_file()
    assert calls["count"] == 3


def test_run_rejects_live_before_writing_any_heartbeat(tmp_path: Path) -> None:
    heartbeat = tmp_path / "heartbeat"

    with pytest.raises(LiveModeNotReadyError):
        run(
            _settings(trading_mode="live"),
            heartbeat_path=heartbeat,
            dependencies=RunDependencies(sleep=lambda _s: None),
        )

    assert not heartbeat.exists()


def test_run_dispatches_provided_paper_ticks_into_persistent_cycle(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    heartbeat = tmp_path / "heartbeat"
    calls = {"ticks": 0, "sleeps": 0}

    def next_tick() -> PaperTick | None:
        calls["ticks"] += 1
        return _tick() if calls["ticks"] == 1 else None

    def sleep(_seconds: float) -> None:
        calls["sleeps"] += 1

    run(
        _settings(trading_mode="paper"),
        heartbeat_path=heartbeat,
        dependencies=RunDependencies(
            paper_cycle=_cycle(conn),
            next_paper_tick=next_tick,
            sleep=sleep,
            should_stop=lambda: calls["sleeps"] == 2,
        ),
    )

    assert heartbeat.is_file()
    assert conn.execute("SELECT COUNT(*) FROM decisions WHERE mode='paper'").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM trades WHERE mode='paper'").fetchone()[0] == 1
    conn.close()


def test_run_logs_tick_failure_and_stops_without_trade(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = database.connect(tmp_path / "paper.db")
    bad_tick = _tick(account=replace(_account_snapshot(), trading_mode="live"))

    with pytest.raises(ValueError, match="paper"):
        run(
            _settings(trading_mode="paper"),
            heartbeat_path=tmp_path / "heartbeat",
            dependencies=RunDependencies(
                paper_cycle=_cycle(conn),
                next_paper_tick=lambda: bad_tick,
                sleep=lambda _s: None,
            ),
        )

    assert "event=paper_tick_failed ticker=SBER" in caplog.text
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_run_stops_on_provider_failure_without_trading(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = database.connect(tmp_path / "paper.db")

    def unavailable() -> PaperTick | None:
        raise ValueError("история недоступна")

    with pytest.raises(ValueError, match="история недоступна"):
        run(
            _settings(trading_mode="paper"),
            heartbeat_path=tmp_path / "heartbeat",
            dependencies=RunDependencies(
                paper_cycle=_cycle(conn),
                next_paper_tick=unavailable,
                sleep=lambda _s: None,
            ),
        )
    assert "event=paper_source_failed" in caplog.text
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize("error_class", [TypeError, RuntimeError, AssertionError])
def test_run_logs_evaluate_failure_without_ack_or_raw_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_class: type[Exception],
) -> None:
    conn = database.connect(tmp_path / "evaluate_main.db")
    cycle = _cycle(conn)
    acks: list[PaperTick] = []

    def fail_evaluate(**_kwargs: object) -> object:
        raise error_class("sensitive evaluation detail")

    monkeypatch.setattr("trading_bot.executor.evaluate", fail_evaluate)
    with pytest.raises(error_class, match="sensitive evaluation detail"):
        run(
            _settings(),
            heartbeat_path=tmp_path / "heartbeat",
            dependencies=RunDependencies(
                paper_cycle=cycle,
                next_paper_tick=_tick,
                ack_paper_tick=acks.append,
                sleep=lambda _seconds: None,
            ),
        )
    assert acks == []
    assert "event=paper_tick_failed ticker=SBER" in caplog.text
    assert "sensitive evaluation detail" not in caplog.text
    assert conn.execute(
        "SELECT stage, error_type, status FROM paper_source_episodes"
    ).fetchone() == ("strategy_evaluate", error_class.__name__, "open")
    conn.close()


def test_run_requires_both_tick_source_and_paper_cycle(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "paper.db")
    heartbeat = tmp_path / "heartbeat"
    with pytest.raises(ValueError, match="поставщик тиков"):
        run(
            _settings(trading_mode="paper"),
            heartbeat_path=heartbeat,
            dependencies=RunDependencies(paper_cycle=_cycle(conn), should_stop=lambda: True),
        )
    assert not heartbeat.exists()
    conn.close()


def test_run_acknowledges_bar_only_after_persistent_paper_cycle(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "ack.db")
    tick = _tick()
    acknowledgements: list[PaperTick] = []
    calls = {"count": 0}

    def next_tick() -> PaperTick | None:
        calls["count"] += 1
        return tick if calls["count"] == 1 else None

    run(
        _settings(),
        heartbeat_path=tmp_path / "heartbeat",
        dependencies=RunDependencies(
            paper_cycle=_cycle(conn),
            next_paper_tick=next_tick,
            ack_paper_tick=acknowledgements.append,
            sleep=lambda _seconds: None,
            should_stop=lambda: calls["count"] >= 2,
        ),
    )
    assert acknowledgements == [tick]
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1
    conn.close()


def test_run_never_acknowledges_bar_when_journal_write_fails(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "broken.db")
    cycle = _cycle(conn)
    conn.execute(
        "CREATE TRIGGER reject_decision BEFORE INSERT ON decisions "
        "BEGIN SELECT RAISE(ABORT, 'journal unavailable'); END"
    )
    conn.commit()
    acknowledgements: list[PaperTick] = []
    with pytest.raises(sqlite3.IntegrityError, match="journal unavailable"):
        run(
            _settings(),
            heartbeat_path=tmp_path / "heartbeat",
            dependencies=RunDependencies(
                paper_cycle=cycle,
                next_paper_tick=_tick,
                ack_paper_tick=acknowledgements.append,
                sleep=lambda _seconds: None,
            ),
        )
    assert acknowledgements == []
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


def test_automatic_paper_bootstrap_applies_recommendation_not_legacy_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    candidates = tuple(f"TEST{i:02d}" for i in range(30))
    approved = candidates[:15]  # намеренно не входит в верхние 18 рекомендаций
    settings = Settings(
        db_path=tmp_path / "paper.db",
        paper_starting_balance=Decimal("100000"),
        paper_candidate_tickers=",".join(candidates),
        paper_approved_tickers=",".join(approved),
        paper_index_id="INDEX-ID",
        regime_refresh_min=60,
        _env_file=None,  # type: ignore[call-arg]
    )
    captured: dict[str, object] = {}

    class FakeFeed:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        def daily_volume_lots(
            self, tickers: tuple[str, ...], *, sessions: int
        ) -> dict[str, list[int]]:
            return {ticker: [index + 1] * sessions for index, ticker in enumerate(tickers)}

        def next_paper_tick(self) -> PaperTick | None:
            return None

        def poll_regimes(self) -> None:
            raise AssertionError("bootstrap не должен запускать опрос")

        def ack(self, _tick: PaperTick) -> None:
            raise AssertionError("не вызывать без решения")

        def set_pool_gate(self, gate: object) -> None:
            captured["pool_gate"] = gate

        def apply_universe(self, universe: object) -> None:
            captured["universe"] = universe

    monkeypatch.setattr("trading_bot.main.PaperTickFeed", FakeFeed)
    conn = database.connect(settings.db_path)
    gateway = _gateway([_share(ticker) for ticker in candidates], [_index()])
    deps = _paper_dependencies(settings, conn=conn, gateway=gateway)
    assert captured["approved_tickers"] == tuple(reversed(candidates[12:]))
    assert captured["index_instrument_id"] == "index-uid"
    assert captured["max_bar_staleness"] == timedelta(seconds=600)
    assert captured["max_quote_staleness"] == timedelta(seconds=30)
    assert captured["regime_refresh_min"] == 60
    assert deps.paper_cycle is not None and deps.next_paper_tick is not None
    conn.close()


def test_paper_bootstrap_constructs_real_feed_with_validated_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tickers = tuple(f"TEST{i:02d}" for i in range(30))
    settings = Settings(
        db_path=tmp_path / "paper.db",
        paper_starting_balance=Decimal("100000"),
        paper_candidate_tickers=",".join(tickers),
        paper_approved_tickers=",".join(tickers[:15]),
        paper_index_id="INDEX-ID",
        paper_max_bar_staleness_sec=420,
        paper_max_quote_staleness_sec=12,
        _env_file=None,  # type: ignore[call-arg]
    )
    monkeypatch.setattr(
        PaperTickFeed,
        "daily_volume_lots",
        lambda _self, candidates, *, sessions: {
            ticker: [index + 1] * sessions for index, ticker in enumerate(candidates)
        },
    )
    conn = database.connect(settings.db_path)
    gateway = _gateway([_share(ticker) for ticker in tickers], [_index()])
    deps = _paper_dependencies(settings, conn=conn, gateway=gateway)
    assert deps.next_paper_tick is not None
    feed = deps.next_paper_tick.__self__
    assert isinstance(feed, PaperTickFeed)
    assert feed._max_bar_staleness == timedelta(seconds=420)
    assert feed._max_quote_staleness == timedelta(seconds=12)
    assert deps.ack_paper_tick is not None and deps.ack_paper_tick.__self__ is feed
    assert deps.poll_regimes is not None and deps.poll_regimes.__self__ is feed
    assert date(2026, 1, 1) in feed._known_holidays
    assert date(2026, 11, 4) in feed._known_holidays
    conn.close()


def test_main_with_balance_only_stops_if_keyring_token_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trading_bot.security import SecurityError

    settings = Settings(
        db_path=tmp_path / "paper.db",
        paper_starting_balance=Decimal("100000"),
        _env_file=None,  # type: ignore[call-arg]
    )
    assert settings.paper_source_enabled
    monkeypatch.setattr("trading_bot.main.load_settings", lambda: settings)
    monkeypatch.setattr("trading_bot.main._install_signal_handlers", lambda _flag: None)

    def no_token(_name: str) -> str:
        raise SecurityError("секрет не найден")

    monkeypatch.setattr("trading_bot.main.get_stored_token", no_token)
    assert main() == 1
    assert not (tmp_path / "heartbeat").exists()


def test_main_with_balance_only_opens_only_sandbox_read_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(
        db_path=tmp_path / "paper.db",
        paper_starting_balance=Decimal("100000"),
        candle_min_interval_sec=2.5,
        candle_max_attempts=3,
        candle_retry_base_sec=8.0,
        candle_max_retry_wait_sec=90.0,
        _env_file=None,  # type: ignore[call-arg]
    )
    captured: dict[str, object] = {}
    monkeypatch.setattr("trading_bot.main.load_settings", lambda: settings)
    monkeypatch.setattr("trading_bot.main._install_signal_handlers", lambda _flag: None)
    monkeypatch.setattr("trading_bot.main.sandbox_target", lambda: "sandbox-only")

    def stored_token(name: str) -> str:
        captured["keyring_name"] = name
        return "synthetic-test-token"

    def fake_gateway(
        token: str,
        *,
        target: str,
        candle_policy: CandleRequestPolicy,
        candle_timing: CandleRequestTiming,
    ) -> object:
        captured["target"] = target
        captured["token"] = token
        captured["candle_policy"] = candle_policy
        captured["candle_timing"] = candle_timing
        return object()

    def fake_dependencies(
        _settings: Settings, *, conn: sqlite3.Connection, gateway: object
    ) -> RunDependencies:
        captured["gateway"] = gateway
        return RunDependencies(paper_cycle=_cycle(conn), next_paper_tick=lambda: None)

    def fake_run(
        _settings: Settings, *, heartbeat_path: Path, dependencies: RunDependencies
    ) -> None:
        captured["configured_cycle"] = dependencies.paper_cycle is not None
        captured["heartbeat_path"] = heartbeat_path

    monkeypatch.setattr("trading_bot.main.get_stored_token", stored_token)
    monkeypatch.setattr("trading_bot.main.InvestGateway", fake_gateway)
    monkeypatch.setattr("trading_bot.main._paper_dependencies", fake_dependencies)
    monkeypatch.setattr("trading_bot.main.run", fake_run)
    assert main() == 0
    assert captured["keyring_name"] == "broker_token"
    assert captured["target"] == "sandbox-only"
    assert captured["configured_cycle"] is True
    assert captured["candle_policy"] == CandleRequestPolicy(
        min_interval_sec=settings.candle_min_interval_sec,
        max_attempts=settings.candle_max_attempts,
        retry_base_sec=settings.candle_retry_base_sec,
        max_retry_wait_sec=settings.candle_max_retry_wait_sec,
    )
    timing = captured["candle_timing"]
    assert isinstance(timing, CandleRequestTiming)
    assert not timing.should_stop()
    assert captured["heartbeat_path"] == tmp_path / "heartbeat"


def test_main_without_complete_bootstrap_does_not_read_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(db_path=tmp_path / "paper.db", _env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr("trading_bot.main.load_settings", lambda: settings)
    monkeypatch.setattr("trading_bot.main._install_signal_handlers", lambda _flag: None)

    def no_token_read(_name: str) -> str:
        raise AssertionError("секрет не нужен без полного bootstrap")

    monkeypatch.setattr("trading_bot.main.get_stored_token", no_token_read)
    monkeypatch.setattr(
        "trading_bot.main.run",
        lambda _settings, *, heartbeat_path, dependencies: None,
    )
    assert main() == 0
    assert not settings.db_path.exists()


def test_run_polls_regime_before_tick_even_when_no_bar(tmp_path: Path) -> None:
    events: list[str] = []
    conn = database.connect(tmp_path / "poll.db")

    def poll() -> None:
        events.append("regime")

    def next_tick() -> None:
        events.append("tick")

    run(
        _settings(),
        heartbeat_path=tmp_path / "heartbeat",
        dependencies=RunDependencies(
            paper_cycle=_cycle(conn),
            next_paper_tick=next_tick,
            poll_regimes=poll,
            sleep=lambda _seconds: None,
            should_stop=lambda: len(events) >= 4,
        ),
    )
    assert events == ["regime", "tick", "regime", "tick"]
    assert conn.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
    conn.close()


def test_run_regime_poll_failure_stops_before_tick_without_raw_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    conn = database.connect(tmp_path / "poll_failure.db")

    def poll() -> None:
        raise RuntimeError("sensitive regime detail")

    def next_tick() -> None:
        raise AssertionError("tick must not run after poll failure")

    with pytest.raises(RuntimeError, match="sensitive regime detail"):
        run(
            _settings(),
            heartbeat_path=tmp_path / "heartbeat",
            dependencies=RunDependencies(
                paper_cycle=_cycle(conn), next_paper_tick=next_tick, poll_regimes=poll
            ),
        )
    assert "event=paper_source_failed" in caplog.text
    assert "sensitive regime detail" not in caplog.text
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
    conn.close()


_REFUSAL_NOW = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)
_REFUSAL_BAR = datetime(2026, 3, 2, 8, 55, tzinfo=UTC)


class FakePaperCycle:
    """Записывает вызовы run() к paper-циклу; не трогает БД и сеть."""

    def __init__(self, *, fail_record: bool = False) -> None:
        self.fail_record = fail_record
        self.recorded: list[SourceRefusal] = []
        self.resolved: list[tuple[str, datetime]] = []
        self.ran_ticks: list[PaperTick] = []

    def record_source_refusal(self, refusal: SourceRefusal) -> int:
        if self.fail_record:
            raise sqlite3.OperationalError("injected refusal save failure")
        self.recorded.append(refusal)
        return 1

    def resolve_source_refusals(self, ticker: str, at: datetime) -> int:
        self.resolved.append((ticker, at))
        return 0

    def run_tick(self, tick: PaperTick) -> None:
        self.ran_ticks.append(tick)


def _refusal(
    *, action: Literal["skip", "stop"] = "skip", error_type: str = "StaleDataError"
) -> SourceRefusal:
    return SourceRefusal(
        ticker="SBER",
        stage="quote",
        observed_at=_REFUSAL_NOW,
        source_bar_ts=_REFUSAL_BAR,
        action=action,
        error_type=error_type,
    )


def test_run_skip_refusal_saved_once_not_acked_and_resolved_after_success(
    tmp_path: Path,
) -> None:
    cycle = FakePaperCycle()
    refusal = _refusal(action="skip")
    tick = _tick()
    acks: list[PaperTick] = []
    calls = {"n": 0}

    def next_tick() -> PaperTick | SourceRefusal | None:
        calls["n"] += 1
        if calls["n"] == 1:
            return refusal
        if calls["n"] == 2:
            return tick
        return None

    run(
        _settings(),
        heartbeat_path=tmp_path / "heartbeat",
        dependencies=RunDependencies(
            paper_cycle=cycle,
            next_paper_tick=next_tick,
            ack_paper_tick=acks.append,
            sleep=lambda _seconds: None,
            should_stop=lambda: calls["n"] >= 3,
        ),
    )

    assert cycle.recorded == [refusal]  # skip сохранён ровно один раз
    assert acks == [tick]  # отказ не ack, подтверждён только успешный тик
    assert cycle.ran_ticks == [tick]  # следующий тик обработан
    assert len(cycle.resolved) == 1
    assert cycle.resolved[0][0] == "SBER"  # resolve после успеха
    assert cycle.resolved[0][1].tzinfo is not None
    # токен и сырое исключение не записываются
    assert cycle.recorded[0].error_type == "StaleDataError"
    assert "TOKEN-SENTINEL" not in repr(cycle.recorded)
    assert "Traceback" not in repr(cycle.recorded)


def test_run_stop_refusal_saved_before_exception_and_not_acked(tmp_path: Path) -> None:
    cycle = FakePaperCycle()
    refusal = _refusal(action="stop", error_type="GatewayError")
    acks: list[PaperTick] = []
    calls = {"n": 0}

    def next_tick() -> PaperTick | SourceRefusal | None:
        calls["n"] += 1
        return refusal

    with pytest.raises(PaperFeedError):
        run(
            _settings(),
            heartbeat_path=tmp_path / "heartbeat",
            dependencies=RunDependencies(
                paper_cycle=cycle,
                next_paper_tick=next_tick,
                ack_paper_tick=acks.append,
                sleep=lambda _seconds: None,
                should_stop=lambda: calls["n"] >= 2,
            ),
        )

    assert cycle.recorded == [refusal]  # stop сохранён до исключения
    assert acks == []  # не ack
    assert cycle.ran_ticks == []  # тик не обрабатывался


def test_run_sqlite_failure_on_refusal_save_stops_without_processing(
    tmp_path: Path,
) -> None:
    cycle = FakePaperCycle(fail_record=True)
    refusal = _refusal(action="skip")
    tick = _tick()
    acks: list[PaperTick] = []
    calls = {"n": 0}

    def next_tick() -> PaperTick | SourceRefusal | None:
        calls["n"] += 1
        return refusal if calls["n"] == 1 else tick

    with pytest.raises(sqlite3.OperationalError):
        run(
            _settings(),
            heartbeat_path=tmp_path / "heartbeat",
            dependencies=RunDependencies(
                paper_cycle=cycle,
                next_paper_tick=next_tick,
                ack_paper_tick=acks.append,
                sleep=lambda _seconds: None,
                should_stop=lambda: calls["n"] >= 2,
            ),
        )

    assert acks == []  # ничего не подтверждено
    assert cycle.ran_ticks == []  # ни один тик не обработан
