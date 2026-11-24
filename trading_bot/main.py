"""Точка входа бота (слой L5, docs/01_ARCHITECTURE.md).

Разрешено: heartbeat/graceful stop и автоматический read-only paper-bootstrap
при явно заданном положительном балансе; selected ликвидного пула применяется.
Без баланса `main()` остаётся offline heartbeat-only без секретов/SDK/БД.

Ключевой инвариант (AGENTS.md §4.1): при `TRADING_MODE=live` бот отказывается
стартовать явно и с понятным сообщением — `LiveBroker` не реализован (M8),
автоматический переход в live запрещён архитектурно, а не только по конфигу.
"""

from __future__ import annotations

import logging
import signal
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType

from trading_bot import database
from trading_bot.api_client import CandleRequestTiming, InvestGateway, sandbox_target
from trading_bot.cancellation import CancellationToken, OperationCancelled
from trading_bot.config import CandleRequestPolicy, Settings, load_settings
from trading_bot.executor import PaperCycle, PaperTick
from trading_bot.logger import setup_logging
from trading_bot.market_regime import MarketRegimeClassifier, MarketRegimeConfig
from trading_bot.paper_account import PaperAccountSource
from trading_bot.paper_feed import PaperFeedError, PaperTickFeed
from trading_bot.pool import LiquidityRules, recommend_pool
from trading_bot.pool_refresh import (
    PoolJournal,
    PoolRefresh,
    RefreshDependencies,
    Universe,
    completed_dates,
    utc_slot,
)
from trading_bot.risk_manager import RiskConfig
from trading_bot.security import get_stored_token
from trading_bot.source_events import SourceRefusal
from trading_bot.strategy import StrategyConfig
from trading_bot.utils import load_known_holidays

logger = logging.getLogger(__name__)

_BOOTSTRAP_REMEDIES = {
    "credentials": "unlock_keyring_and_configure_stored_broker_token",
    "database": "check_database_path_permissions_and_migrations",
    "calendar": "restore_validated_MOEX_calendar_seed",
    "share_catalog": "inspect_MOEX_RUB_filters_and_catalog_access",
    "imoex_discovery": "verify_exact_IMOEX_INDEX_availability_no_substitutes",
    "liquidity": "check_candle_quota_transport_and_session_coverage_then_restart",
    "open_positions": "resolve_all_persisted_tickers_without_ambiguity",
    "paper_cycle": "inspect_pending_failed_journal_and_paper_account_state",
}

#: docs/13_SECURITY.md, границы возможностей системы, п.3: включить live можно только
#: через отдельное подтверждение и реализованный LiveBroker (M8, tasks/MILESTONES.md).
#: До этого — явный отказ, а не тихий останов на paper.
LIVE_NOT_READY_MESSAGE = (
    "TRADING_MODE=live запрошен, но LiveBroker не реализован (см. tasks/MILESTONES.md, M8) "
    "— переход в live без готового исполнения запрещён архитектурно (docs/13_SECURITY.md)."
)


class LiveModeNotReadyError(RuntimeError):
    """`TRADING_MODE=live`, но исполнение (M8) в кодовой базе ещё не реализовано."""


def guard_trading_mode(settings: Settings) -> None:
    """Отказывает в старте, если запрошен `live` без готового `LiveBroker`.

    Отказ: `LiveModeNotReadyError` — вызывающий код обязан остановить процесс
    с ненулевым кодом возврата, а не продолжить как если бы это был paper.
    """
    if settings.trading_mode == "live":
        raise LiveModeNotReadyError(LIVE_NOT_READY_MESSAGE)


def write_heartbeat(path: Path) -> None:
    """Отмечает, что торговый процесс жив — Docker `HEALTHCHECK` читает mtime файла."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(time.time()), encoding="utf-8")


@dataclass(frozen=True)
class RunDependencies:
    """Неизменяемые функции управления и необязательный источник paper-тиков."""

    sleep: Callable[[float], None] = time.sleep
    should_stop: Callable[[], bool] = lambda: False
    paper_cycle: PaperCycle | None = None
    next_paper_tick: Callable[[], PaperTick | SourceRefusal | None] | None = None
    ack_paper_tick: Callable[[PaperTick], None] | None = None
    poll_regimes: Callable[[], None] | None = None
    check_pool_due: Callable[[], None] | None = None
    refresh_pool_step: Callable[[], None] | None = None
    close_workers: Callable[[], None] | None = None


@contextmanager
def _bootstrap_stage(stage: str) -> Iterator[None]:
    """Log actionable startup stages without SDK text, credentials or traceback."""
    logger.info("event=paper_bootstrap_stage stage=%s action=start", stage)
    try:
        yield
    except OperationCancelled:
        logger.info("event=paper_bootstrap_cancelled stage=%s action=stop", stage)
        raise
    except Exception as exc:
        logger.error(
            "event=paper_bootstrap_failed stage=%s ts=%s error_type=%s action=stop " "remedy=%s",
            stage,
            datetime.now(tz=UTC).isoformat(),
            type(exc).__name__,
            _BOOTSTRAP_REMEDIES[stage],
        )
        raise


def _paper_dependencies(
    settings: Settings, *, conn: sqlite3.Connection, gateway: InvestGateway
) -> RunDependencies:
    """Bootstrap a journaled pool and wire cooperative 24x7 refresh, never live.

    Legacy manual inputs are ignored. Calendar/index/catalog/liquidity failures
    stop startup. Existing positions outside selected remain monitoring-only.
    """
    if not settings.paper_source_enabled or settings.paper_starting_balance is None:
        raise ValueError("для автономного paper нужен положительный стартовый баланс")
    if any(
        (settings.paper_candidate_tickers, settings.paper_approved_tickers, settings.paper_index_id)
    ):
        logger.warning(
            "event=paper_legacy_bootstrap_ignored fields=candidates,approved,index_id "
            "action=automatic_discovery remedy=remove_deprecated_environment_fields"
        )
    gateway.check_cancelled()
    with _bootstrap_stage("calendar"):
        holidays = frozenset(
            load_known_holidays(Path(__file__).resolve().parent.parent / "data_seed/holidays_ru.md")
        )
    gateway.check_cancelled()
    with _bootstrap_stage("share_catalog"):
        catalog_slot = utc_slot(datetime.now(tz=UTC))
        catalog = gateway.discover_paper_shares()
        candidates = tuple(catalog.shares)
        logger.info("event=paper_catalog candidates=%s excluded=%s", candidates, catalog.excluded)
        if len(candidates) < settings.pool_min:
            raise ValueError("недостаточно подходящих MOEX RUB акций")
    gateway.check_cancelled()
    with _bootstrap_stage("imoex_discovery"):
        index_id = gateway.resolve_imoex_id()
    strategy_config = StrategyConfig(
        **{item.name: getattr(settings, item.name) for item in fields(StrategyConfig)}
    )
    risk_config = RiskConfig(
        **{item.name: getattr(settings, item.name) for item in fields(RiskConfig)}
    )
    classifier = MarketRegimeClassifier(
        MarketRegimeConfig(
            period=settings.chaos_component_period,
            chaos_window=settings.chaos_window,
            threshold_pct=settings.chaos_threshold_pct,
            hysteresis=settings.chaos_hysteresis,
            confirm_bars=settings.chaos_confirm_bars,
        )
    )
    account_source = PaperAccountSource(conn, settings.paper_starting_balance)

    def make_feed(tickers: tuple[str, ...], monitoring: tuple[str, ...] = ()) -> PaperTickFeed:
        return PaperTickFeed(
            gateway=gateway,
            account_source=account_source,
            regime_classifier=classifier,
            strategy_config=strategy_config,
            approved_tickers=tickers,
            monitoring_tickers=monitoring,
            index_instrument_id=index_id,
            clock=lambda: datetime.now(tz=UTC),
            max_bar_staleness=timedelta(seconds=settings.paper_max_bar_staleness_sec),
            max_quote_staleness=timedelta(seconds=settings.paper_max_quote_staleness_sec),
            volume_avg_window=settings.vol_window,
            regime_refresh_min=settings.regime_refresh_min,
            known_holidays=holidays,
            liquidity_workers=settings.liquidity_workers,
        )

    gateway.check_cancelled()
    with _bootstrap_stage("liquidity"):
        volume_dates = completed_dates(
            datetime.now(tz=UTC), settings.pool_liquidity_sessions, holidays
        )
        volumes = make_feed(candidates).daily_volume_lots(
            candidates, sessions=settings.pool_liquidity_sessions
        )
        logger.info(
            "event=paper_liquidity_coverage required_sessions=%s percentile=%s "
            "minimum=%s maximum=%s confirmed_sessions=%s",
            settings.pool_liquidity_sessions,
            settings.liquidity_percentile,
            settings.pool_min,
            settings.pool_max,
            {ticker: len(values) for ticker, values in volumes.items()},
        )
        gateway.check_cancelled()
        recommendation = recommend_pool(
            volumes,
            LiquidityRules(
                sessions=settings.pool_liquidity_sessions,
                percentile=settings.liquidity_percentile,
                minimum=settings.pool_min,
                maximum=settings.pool_max,
            ),
        )
    # Startup can itself span slots. Revalidate catalog/index for the latest slot,
    # retaining only volumes whose identity, lot and completed dates still match.
    while catalog_slot != utc_slot(datetime.now(tz=UTC)):
        gateway.check_cancelled()
        previous_catalog, previous_volumes, previous_dates = catalog, volumes, volume_dates
        catalog_slot = utc_slot(datetime.now(tz=UTC))
        with _bootstrap_stage("share_catalog"):
            catalog = gateway.discover_paper_shares()
            candidates = tuple(catalog.shares)
            if len(candidates) < settings.pool_min:
                raise ValueError("недостаточно подходящих MOEX RUB акций")
        with _bootstrap_stage("imoex_discovery"):
            index_id = gateway.resolve_imoex_id()
        with _bootstrap_stage("liquidity"):
            volume_dates = completed_dates(
                datetime.now(tz=UTC), settings.pool_liquidity_sessions, holidays
            )
            volumes = {
                ticker: previous_volumes[ticker]
                for ticker, share in catalog.shares.items()
                if previous_dates == volume_dates
                and ticker in previous_volumes
                and len(previous_volumes[ticker]) == settings.pool_liquidity_sessions
                and ticker in previous_catalog.shares
                and (share.instrument_id, share.lot_size)
                == (
                    previous_catalog.shares[ticker].instrument_id,
                    previous_catalog.shares[ticker].lot_size,
                )
            }
            missing = tuple(t for t in candidates if t not in volumes)
            if missing:
                volumes.update(
                    make_feed(candidates).daily_volume_lots(
                        missing, sessions=settings.pool_liquidity_sessions
                    )
                )
            gateway.check_cancelled()
            recommendation = recommend_pool(
                volumes,
                LiquidityRules(
                    settings.pool_liquidity_sessions,
                    settings.liquidity_percentile,
                    settings.pool_min,
                    settings.pool_max,
                ),
            )
    gateway.check_cancelled()
    with _bootstrap_stage("open_positions"):
        journal = PoolJournal(conn)
        opens = account_source.open_tickers()
        pinned = journal.recover(opens)
        conflicts = {
            ticker
            for ticker in opens
            if ticker not in catalog.shares
            or pinned[ticker].instrument_id != catalog.shares[ticker].instrument_id
            or pinned[ticker].lot_size != catalog.shares[ticker].lot_size
        }
        monitoring = tuple(sorted(opens - set(recommendation.selected)))
        feed = make_feed(recommendation.selected, monitoring)
    gateway.check_cancelled()
    with _bootstrap_stage("paper_cycle"):
        cycle = PaperCycle(
            conn,
            starting_balance=settings.paper_starting_balance,
            strategy_config=strategy_config,
            risk_config=risk_config,
        )
    gateway.check_cancelled()
    refresh = PoolRefresh(
        RefreshDependencies(
            gateway=gateway,
            journal=journal,
            clock=lambda: datetime.now(tz=UTC),
            open_tickers=account_source.open_tickers,
            publish=feed.apply_universe,
            rules=LiquidityRules(
                settings.pool_liquidity_sessions,
                settings.liquidity_percentile,
                settings.pool_min,
                settings.pool_max,
            ),
            holidays=holidays,
            workers=settings.liquidity_workers,
        )
    )
    feed.set_pool_gate(refresh.gate)
    snapshot = Universe(
        journal.generation() + 1,
        catalog_slot,
        recommendation.selected,
        monitoring,
        {**catalog.shares, **pinned},
        index_id,
    )
    refresh.seed(
        snapshot,
        {t: v for t, v in volumes.items() if t not in conflicts},
        dates=volume_dates,
        details={
            "threshold_lots": str(recommendation.threshold_lots),
            "exclusions": {**catalog.excluded, **recommendation.excluded},
        },
    )
    if conflicts:
        refresh.gate.block("pool_open_identity_conflict")
        journal.append(
            "failed",
            datetime.now(tz=UTC),
            {
                "slot": snapshot.slot.isoformat(),
                "generation": snapshot.generation,
                "reason": "open_identity_conflict",
                "conflicts": sorted(conflicts),
            },
        )
        logger.error(
            "event=pool_open_identity_conflict tickers=%s action=monitor_only", sorted(conflicts)
        )
    logger.info(
        "event=paper_pool_applied selected=%s threshold_lots=%s sessions=%s "
        "percentile=%s average_daily_lots=%s excluded=%s monitoring_only=%s "
        "index_id=%s refresh=wall_15min_24x7",
        recommendation.selected,
        recommendation.threshold_lots,
        settings.pool_liquidity_sessions,
        settings.liquidity_percentile,
        recommendation.average_daily_lots,
        recommendation.excluded,
        monitoring,
        index_id,
    )
    return RunDependencies(
        paper_cycle=cycle,
        next_paper_tick=feed.next_paper_tick,
        ack_paper_tick=feed.ack,
        poll_regimes=feed.poll_regimes,
        check_pool_due=refresh.check_due,
        refresh_pool_step=refresh.step,
        close_workers=refresh.close,
    )


def run(
    settings: Settings,
    *,
    heartbeat_path: Path,
    heartbeat_interval_sec: float = 5.0,
    dependencies: RunDependencies | None = None,
) -> None:
    """Run paper work and always drain read-only workers before returning to SQLite owner."""
    try:
        _run(
            settings,
            heartbeat_path=heartbeat_path,
            heartbeat_interval_sec=heartbeat_interval_sec,
            dependencies=dependencies,
        )
    finally:
        if dependencies is not None and dependencies.close_workers is not None:
            dependencies.close_workers()


def _run(
    settings: Settings,
    *,
    heartbeat_path: Path,
    heartbeat_interval_sec: float = 5.0,
    dependencies: RunDependencies | None = None,
) -> None:
    """Поддерживает heartbeat и обрабатывает поданные готовые paper-такты.

    Цикл и поставщик передаются только вместе в `RunDependencies`. Без них
    не создаётся пустой или синтетический рыночный тик. Ошибки останавливают цикл.
    """
    logger.info(
        "tbot стартует: mode=%s tz_display=%s dashboard_port=%s",
        settings.trading_mode,
        settings.tz_display,
        settings.dashboard_port,
    )
    guard_trading_mode(settings)
    deps = dependencies if dependencies is not None else RunDependencies()
    if (deps.paper_cycle is None) != (deps.next_paper_tick is None):
        raise ValueError("paper_cycle и поставщик тиков должны передаваться вместе")
    if deps.paper_cycle is None:
        logger.warning("Нет поставщика paper-тиков — heartbeat-only, решений нет")
    while not deps.should_stop():
        write_heartbeat(heartbeat_path)
        if deps.paper_cycle is not None and deps.next_paper_tick is not None:
            try:
                if deps.check_pool_due is not None:
                    deps.check_pool_due()
                if deps.poll_regimes is not None:
                    deps.poll_regimes()
                if deps.should_stop():
                    break
                tick = deps.next_paper_tick()
            except OperationCancelled:
                raise
            except Exception as exc:
                # Неожиданный сбой поставщика тоже останавливает цикл без сырого текста.
                logger.error(
                    "event=paper_source_failed ts=%s error_type=%s action=stop",
                    datetime.now(tz=UTC).isoformat(),
                    type(exc).__name__,
                )
                raise
            if deps.should_stop():
                break
            if isinstance(tick, SourceRefusal):
                try:
                    deps.paper_cycle.record_source_refusal(tick)
                except sqlite3.Error:
                    logger.exception(
                        "event=source_refusal_persist_failed ticker=%s stage=%s "
                        "error_type=%s action=stop",
                        tick.ticker,
                        tick.stage,
                        tick.error_type,
                    )
                    raise
                logger.warning(
                    "event=paper_source_refusal ticker=%s stage=%s error_type=%s action=%s",
                    tick.ticker,
                    tick.stage,
                    tick.error_type,
                    tick.action,
                )
                if tick.action == "stop":
                    raise PaperFeedError(
                        f"paper-цикл остановлен после отказа источника: "
                        f"ticker={tick.ticker} stage={tick.stage}"
                    )
            elif tick is not None:
                try:
                    deps.paper_cycle.run_tick(tick)
                    deps.paper_cycle.resolve_source_refusals(
                        tick.strategy.ticker, tick.strategy.now
                    )
                    if deps.ack_paper_tick is not None:
                        deps.ack_paper_tick(tick)
                except Exception as exc:
                    # Любой сбой тика: не ack, не retry fill, остановка с контекстом.
                    logger.error(
                        "event=paper_tick_failed ticker=%s ts=%s error_type=%s action=stop",
                        tick.strategy.ticker,
                        tick.strategy.now.isoformat(),
                        type(exc).__name__,
                    )
                    raise
        if not deps.should_stop() and deps.refresh_pool_step is not None:
            deps.refresh_pool_step()
        if not deps.should_stop():
            deps.sleep(heartbeat_interval_sec)
    logger.info("tbot корректно остановлен")


def _install_signal_handlers(cancellation: CancellationToken) -> None:
    def handler(signum: int, frame: FrameType | None) -> None:
        cancellation.request_stop()
        logger.info("event=tbot_stop_requested signal=%s action=cancel", signum)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main() -> int:
    """Точка входа процесса (`python -m trading_bot.main`).

    Возврат: код выхода — `0` при корректной остановке, `1` при отказе старта
    (например, `TRADING_MODE=live` без готового `LiveBroker`).
    """
    settings = load_settings()
    setup_logging(settings.log_level)

    cancellation = CancellationToken()
    _install_signal_handlers(cancellation)

    heartbeat = settings.db_path.parent / "heartbeat"
    try:
        guard_trading_mode(settings)
        cancellation.check()
        if settings.paper_source_enabled:
            # Секрет не пишется в переменную окружения/SQLite и никогда не
            # запрашивается интерактивно в автономном процессе.
            with _bootstrap_stage("credentials"):
                cancellation.check()
                token = get_stored_token("broker_token")
                cancellation.check()
                gateway = InvestGateway(
                    token,
                    target=sandbox_target(),
                    candle_policy=CandleRequestPolicy(
                        min_interval_sec=settings.candle_min_interval_sec,
                        max_attempts=settings.candle_max_attempts,
                        retry_base_sec=settings.candle_retry_base_sec,
                        max_retry_wait_sec=settings.candle_max_retry_wait_sec,
                        quota_max_attempts=settings.candle_quota_max_attempts,
                        market_data_requests_per_minute=settings.market_data_requests_per_minute,
                    ),
                    candle_timing=CandleRequestTiming(
                        sleep=cancellation.sleep, should_stop=cancellation.is_cancelled
                    ),
                )
            with _bootstrap_stage("database"):
                cancellation.check()
                connection = database.connect(settings.db_path)
            with closing(connection) as conn:
                cancellation.check()
                deps = _paper_dependencies(settings, conn=conn, gateway=gateway)
                cancellation.check()
                run(
                    settings,
                    heartbeat_path=heartbeat,
                    dependencies=replace(
                        deps, should_stop=cancellation.is_cancelled, sleep=cancellation.sleep
                    ),
                )
        else:
            run(
                settings,
                heartbeat_path=heartbeat,
                dependencies=RunDependencies(
                    should_stop=cancellation.is_cancelled, sleep=cancellation.sleep
                ),
            )
        cancellation.check()
    except OperationCancelled:
        logger.info("event=tbot_stopped reason=operator_request action=stop")
        return 0
    except LiveModeNotReadyError as exc:
        logger.critical(str(exc))
        return 1
    except Exception as exc:
        # Ошибки paper-runtime не раскрывают текст/metadata SDK и дают ненулевой код.
        logger.error(
            "event=paper_runtime_stopped ts=%s error_type=%s action=stop",
            datetime.now(tz=UTC).isoformat(),
            type(exc).__name__,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
