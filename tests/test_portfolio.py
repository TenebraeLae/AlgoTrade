"""Тесты portfolio.py: FIFO-учёт, 7 проверок целостности (docs/06_DB_SCHEMA.md)."""

from __future__ import annotations

import random
import sqlite3
import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from trading_bot import database
from trading_bot.portfolio import (
    DuplicateTickerSideError,
    InsufficientPositionError,
    Portfolio,
    PortfolioError,
    UnknownExitReasonError,
)

_TS0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)


def _portfolio(
    tmp_path: Path, *, mode: str = "paper", starting_balance: str = "100000", name: str = "tbot.db"
) -> Portfolio:
    conn = database.connect(tmp_path / name)
    return Portfolio(
        conn, mode=mode, starting_balance=Decimal(starting_balance), clock=lambda: _TS0
    )


# --- Открытие позиции ------------------------------------------------------------


def test_open_position_creates_open_position_and_debits_balance(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    position = portfolio.get_position("SBER")
    assert position is not None
    assert position.lots == 10
    assert position.avg_entry == Decimal("100")
    # 100000 - (10*100 + 3 + 0.5) = 98996.5
    assert portfolio.balance == Decimal("98996.5")


def test_lot_size_changes_cash_and_realized_pnl_without_changing_lot_count(tmp_path: Path) -> None:
    """Цена за бумагу, но деньги и P&L считаются по lots × lot_size."""
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=2,
        lot_size=10,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )
    assert portfolio.balance == Decimal("98000")
    assert portfolio.get_position("SBER") is not None
    closed = portfolio.close_position(
        ticker="SBER",
        lots=2,
        exit_price=Decimal("90"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="stop",
    )
    assert closed[0].pnl_gross == Decimal("-200")
    assert closed[0].pnl_pct == Decimal("-10")
    assert portfolio.balance == Decimal("99800")


def test_open_position_short_credits_balance(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="short",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    # 100000 + (1000 - 3 - 0.5) = 100996.5
    assert portfolio.balance == Decimal("100996.5")


def test_open_position_scaling_in_same_side_is_allowed(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=5,
        lot_size=1,
        price=Decimal("110"),
        fee=Decimal("1.65"),
        slippage=Decimal("0.25"),
    )
    position = portfolio.get_position("SBER")
    assert position is not None
    assert position.lots == 15
    # avg_entry = (10*100 + 5*110) / 15
    assert position.avg_entry == (Decimal(1000) + Decimal(550)) / Decimal(15)


def test_open_position_opposite_side_raises_duplicate_ticker_error(tmp_path: Path) -> None:
    """docs/06_DB_SCHEMA.md, проверка №4: нет двух открытых позиций с одним тикером."""
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    with pytest.raises(DuplicateTickerSideError):
        portfolio.open_position(
            ticker="SBER",
            side="short",
            lots=5,
            lot_size=1,
            price=Decimal("100"),
            fee=Decimal("1"),
            slippage=Decimal("0.1"),
        )


def test_open_position_rejects_non_positive_lots(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    with pytest.raises(ValueError):
        portfolio.open_position(
            ticker="SBER",
            side="long",
            lots=0,
            lot_size=1,
            price=Decimal("100"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
        )


def test_get_position_returns_none_without_open_lots(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    assert portfolio.get_position("SBER") is None


# --- Закрытие: FIFO, одна строка = одна сделка ----------------------------------


def test_close_position_full_single_lot(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    closed = portfolio.close_position(
        ticker="SBER",
        lots=10,
        exit_price=Decimal("110"),
        fee=Decimal("3.3"),
        slippage=Decimal("0.5"),
        exit_reason="take",
    )
    assert len(closed) == 1
    trade = closed[0]
    assert trade.lots == 10
    assert trade.pnl_gross == Decimal("100")  # (110-100)*10
    assert portfolio.get_position("SBER") is None


def test_close_position_fifo_closes_oldest_lot_first(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("200"),
        fee=Decimal("6"),
        slippage=Decimal("1"),
    )
    closed = portfolio.close_position(
        ticker="SBER",
        lots=5,
        exit_price=Decimal("300"),
        fee=Decimal("1.5"),
        slippage=Decimal("0.25"),
        exit_reason="take",
    )
    assert len(closed) == 1
    # Закрыта старейшая порция (цена входа 100), а не новая (200).
    assert closed[0].entry_price == Decimal("100")
    remaining = portfolio.get_position("SBER")
    assert remaining is not None
    assert remaining.lots == 15  # 5 осталось от первой порции + 10 второй


def test_close_position_partial_split_leaves_remainder_open(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    portfolio.close_position(
        ticker="SBER",
        lots=4,
        exit_price=Decimal("110"),
        fee=Decimal("1.32"),
        slippage=Decimal("0.2"),
        exit_reason="take",
    )
    remaining = portfolio.get_position("SBER")
    assert remaining is not None
    assert remaining.lots == 6


def test_partial_close_preserves_lot_size_across_restart(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path, name="nonunit_lots.db")
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=3,
        lot_size=10,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )
    first = portfolio.close_position(
        ticker="SBER",
        lots=1,
        exit_price=Decimal("110"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="take",
    )
    assert first[0].pnl_gross == Decimal("100")
    restored = _portfolio(tmp_path, name="nonunit_lots.db", starting_balance="999999")
    second = restored.close_position(
        ticker="SBER",
        lots=2,
        exit_price=Decimal("90"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="stop",
    )
    assert second[0].pnl_gross == Decimal("-200")
    assert restored.balance == Decimal("99900")
    conn = database.connect(tmp_path / "nonunit_lots.db")
    rows = conn.execute("SELECT lot_size FROM trades WHERE ticker='SBER'").fetchall()
    conn.close()
    assert rows == [(10,), (10,)]


def test_close_position_across_multiple_fifo_batches(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=5,
        lot_size=1,
        price=Decimal("110"),
        fee=Decimal("1.65"),
        slippage=Decimal("0.25"),
    )
    closed = portfolio.close_position(
        ticker="SBER",
        lots=12,
        exit_price=Decimal("120"),
        fee=Decimal("3.6"),
        slippage=Decimal("0.6"),
        exit_reason="take",
    )
    assert len(closed) == 2
    assert closed[0].lots == 10
    assert closed[0].entry_price == Decimal("100")
    assert closed[1].lots == 2
    assert closed[1].entry_price == Decimal("110")
    remaining = portfolio.get_position("SBER")
    assert remaining is not None
    assert remaining.lots == 3


def test_close_position_short_pnl_is_inverted(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="short",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("3"),
        slippage=Decimal("0.5"),
    )
    closed = portfolio.close_position(
        ticker="SBER",
        lots=10,
        exit_price=Decimal("90"),
        fee=Decimal("2.7"),
        slippage=Decimal("0.45"),
        exit_reason="stop",
    )
    assert closed[0].pnl_gross == Decimal("100")  # (100-90)*10 -- шорт в прибыли


def test_close_position_more_than_open_raises_insufficient_position(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=5,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )
    with pytest.raises(InsufficientPositionError):
        portfolio.close_position(
            ticker="SBER",
            lots=6,
            exit_price=Decimal("100"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
            exit_reason="manual",
        )


def test_close_position_rejects_unknown_exit_reason_value(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=5,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )
    with pytest.raises(UnknownExitReasonError):
        portfolio.close_position(
            ticker="SBER",
            lots=5,
            exit_price=Decimal("100"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
            exit_reason="whim",
        )


# --- Свойство: сумма pnl_net закрытых = изменение баланса (docs/06 проверка №2) --


@settings(
    max_examples=200, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    entry_price=st.decimals(min_value="10", max_value="1000", places=2),
    exit_price=st.decimals(min_value="10", max_value="1000", places=2),
    lots=st.integers(min_value=1, max_value=500),
    side=st.sampled_from(["long", "short"]),
)
def test_property_sum_pnl_net_equals_balance_change_when_fully_closed(
    tmp_path: Path, entry_price: Decimal, exit_price: Decimal, lots: int, side: str
) -> None:
    """docs/06_DB_SCHEMA.md, проверка №2: сумма `pnl_net` закрытых = изменение
    баланса за период — здесь на сценарии «открыли и полностью закрыли одну позицию».
    """
    portfolio = _portfolio(tmp_path, name=f"tbot_{random.randint(0, 10**9)}.db")
    balance_start = portfolio.balance
    portfolio.open_position(
        ticker="SBER",
        side=side,  # type: ignore[arg-type]
        lots=lots,
        lot_size=1,
        price=entry_price,
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )
    closed = portfolio.close_position(
        ticker="SBER",
        lots=lots,
        exit_price=exit_price,
        fee=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="manual",
    )
    balance_end = portfolio.balance
    total_pnl_net = sum((trade.pnl_net for trade in closed), Decimal(0))
    assert balance_end - balance_start == total_pnl_net


# --- Восстановление после рестарта: 20 сценариев (docs/15_ACCEPTANCE.md, M6) -----


def _build_scenario(seed: int) -> list[tuple[str, str, int, Decimal]]:
    """Детерминированно строит один из 20 сценариев: список операций
    `(op, ticker, lots, price)`, `op` — "open" или "close_all".
    """
    rng = random.Random(seed)
    tickers = rng.sample(["SBER", "GAZP", "LKOH", "VTBR"], k=rng.randint(0, 3))
    operations: list[tuple[str, str, int, Decimal]] = []
    for ticker in tickers:
        lots = rng.randint(1, 20)
        price = Decimal(str(rng.randint(50, 500)))
        operations.append(("open", ticker, lots, price))
        if rng.random() < 0.5:
            close_lots = rng.randint(1, lots)
            close_price = Decimal(str(rng.randint(50, 500)))
            operations.append(("close_partial", ticker, close_lots, close_price))
    return operations


@pytest.mark.parametrize("seed", list(range(20)))
def test_restart_recovery_20_scenarios(tmp_path: Path, seed: int) -> None:
    """Открываем/частично закрываем позиции согласно сценарию `seed`, «перезапускаем»
    процесс (новый `Portfolio` на том же файле БД) и проверяем, что баланс и все
    открытые позиции восстановились в точности.
    """
    db_name = f"restart_{seed}.db"
    portfolio_before = _portfolio(tmp_path, name=db_name)
    for op, ticker, lots, price in _build_scenario(seed):
        if op == "open":
            portfolio_before.open_position(
                ticker=ticker,
                side="long",
                lots=lots,
                lot_size=1,
                price=price,
                fee=Decimal("1"),
                slippage=Decimal("0.1"),
            )
        elif op == "close_partial":
            portfolio_before.close_position(
                ticker=ticker,
                lots=lots,
                exit_price=price,
                fee=Decimal("0.5"),
                slippage=Decimal("0.05"),
                exit_reason="manual",
            )

    balance_before = portfolio_before.balance
    tickers_before = portfolio_before.open_tickers()
    positions_before = {t: portfolio_before.get_position(t) for t in tickers_before}

    # "Рестарт" -- новое соединение и новый Portfolio на том же файле БД.
    portfolio_after = _portfolio(tmp_path, name=db_name, starting_balance="999999999")

    assert portfolio_after.balance == balance_before
    assert portfolio_after.open_tickers() == tickers_before
    for ticker, position_before in positions_before.items():
        position_after = portfolio_after.get_position(ticker)
        assert position_after == position_before


def test_reconcile_marks_missing_position_as_orphan_without_closing(tmp_path: Path) -> None:
    """docs/00_PLAN.md §10: расхождение -> orphan, автозакрытия нет."""
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )

    orphans = portfolio.reconcile({})  # брокер не подтверждает ни одной позиции

    assert orphans == frozenset({"SBER"})
    position = portfolio.get_position("SBER")
    assert position is not None  # НЕ закрыта автоматически
    assert position.lots == 10
    assert position.source == "orphan"


def test_reconcile_no_mismatch_returns_empty_set(tmp_path: Path) -> None:
    portfolio = _portfolio(tmp_path)
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )
    orphans = portfolio.reconcile({"SBER": 10})
    assert orphans == frozenset()
    position = portfolio.get_position("SBER")
    assert position is not None
    assert position.source == "bot"


# --- Изоляция режимов: paper и live не смешиваются (docs/06 проверка №7) --------


def test_paper_and_live_modes_do_not_share_balance_or_positions(tmp_path: Path) -> None:
    conn = database.connect(tmp_path / "shared.db")
    paper = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"), clock=lambda: _TS0)
    live = Portfolio(conn, mode="live", starting_balance=Decimal("50000"), clock=lambda: _TS0)

    paper.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )

    assert paper.open_tickers() == frozenset({"SBER"})
    assert live.open_tickers() == frozenset()
    assert live.balance == Decimal("50000")
    assert paper.balance == Decimal("99000")


# --- Два независимых SQLite-соединения: стейл-экземпляр -------------------------
# Каждый `_portfolio(tmp_path, name="shared.db")` открывает собственное соединение
# `database.connect()` к одному файлу -- так моделируется второй процесс/экземпляр.


def test_stale_instance_cannot_overwrite_newer_balance(tmp_path: Path) -> None:
    """Экземпляр со старым кешем баланса не перезаписывает более новый баланс в БД.

    `fresh` списывает 1000 (баланс в БД -> 99000). `stale` был создан до этого и
    держит кеш 100000; его открытие на 500 по другому тикеру должно либо учесть
    списание `fresh` (итог 98500), либо быть отказано (итог 99000). Итог 99500 --
    потеря списания `fresh`: стейл-кеш записан поверх более нового баланса.
    """
    stale = _portfolio(tmp_path, name="shared.db")
    fresh = _portfolio(tmp_path, name="shared.db")
    fresh.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )

    stale_accepted = True
    try:
        stale.open_position(
            ticker="GAZP",
            side="long",
            lots=5,
            lot_size=1,
            price=Decimal("100"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
        )
    except PortfolioError:
        stale_accepted = False

    expected = Decimal("98500") if stale_accepted else Decimal("99000")
    reloaded = _portfolio(tmp_path, name="shared.db")
    assert reloaded.balance == expected


def test_stale_instance_sees_position_from_other_connection_and_refuses_opposite_side(
    tmp_path: Path,
) -> None:
    """Конкурентный стейл-экземпляр видит позицию, открытую другим соединением,
    и отказывает в открытии противоположной стороны по тому же тикеру.
    """
    stale = _portfolio(tmp_path, name="shared.db")
    other = _portfolio(tmp_path, name="shared.db")
    assert stale.get_position("SBER") is None  # стейл-экземпляр видит пусто до гонки

    other.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
    )

    with pytest.raises(DuplicateTickerSideError):
        stale.open_position(
            ticker="SBER",
            side="short",
            lots=5,
            lot_size=1,
            price=Decimal("100"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
        )

    reloaded = _portfolio(tmp_path, name="shared.db")
    position = reloaded.get_position("SBER")
    assert position is not None
    assert position.side == "long"
    assert position.lots == 10
    assert reloaded.balance == Decimal("99000")


# --- Атомарность: SQL-ошибка посреди open_position ------------------------------


def test_open_position_is_atomic_when_sql_fails_after_trade_insert(tmp_path: Path) -> None:
    """Реальная SQL-ошибка (триггер RAISE(ABORT) на вставке в `positions`) после
    записи в `trades` не оставляет ни строки сделки, ни позиции, ни изменённого баланса.
    """
    conn = database.connect(tmp_path / "atomic.db")
    portfolio = Portfolio(
        conn, mode="paper", starting_balance=Decimal("100000"), clock=lambda: _TS0
    )
    conn.execute(
        "CREATE TRIGGER fail_position_insert BEFORE INSERT ON positions "
        "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
    )
    conn.commit()

    with pytest.raises(sqlite3.Error):
        portfolio.open_position(
            ticker="SBER",
            side="long",
            lots=10,
            lot_size=1,
            price=Decimal("100"),
            fee=Decimal("3"),
            slippage=Decimal("0.5"),
        )

    assert portfolio.balance == Decimal("100000")
    assert portfolio.open_tickers() == frozenset()
    assert portfolio.get_position("SBER") is None
    assert conn.execute("SELECT COUNT(*) FROM trades").fetchone() == (0,)
    conn.close()

    reloaded = _portfolio(tmp_path, name="atomic.db")
    assert reloaded.balance == Decimal("100000")
    assert reloaded.open_tickers() == frozenset()


# --- Реальная конкуренция: два потока, два соединения, один тикер ------------------


def test_concurrent_opposite_side_open_admits_exactly_one(tmp_path: Path) -> None:
    """Два потока одновременно открывают противоположные стороны по SBER, каждый на
    своём соединении к одному файлу. Ровно одна попытка проходит, вторая получает
    `DuplicateTickerSideError`; в БД остаётся одна сторона, баланс соответствует ей.
    Повторяется по 20 свежим файлам, чтобы гонка проявлялась стабильно.
    """
    for attempt in range(20):
        name = f"race_{attempt}.db"
        _portfolio(tmp_path, name=name)  # схема и баланс создаются до гонки
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def worker(
            side: str,
            name: str = name,
            barrier: threading.Barrier = barrier,
            outcomes: list[str] = outcomes,
            lock: threading.Lock = lock,
        ) -> None:
            portfolio = _portfolio(tmp_path, name=name)
            barrier.wait()
            try:
                portfolio.open_position(
                    ticker="SBER",
                    side=side,  # type: ignore[arg-type]
                    lots=10,
                    lot_size=1,
                    price=Decimal("100"),
                    fee=Decimal("0"),
                    slippage=Decimal("0"),
                )
                outcome = "opened"
            except DuplicateTickerSideError:
                outcome = "duplicate"
            except Exception as exc:  # тест фиксирует неожиданную ошибку, не маскирует её
                outcome = f"error:{type(exc).__name__}:{exc}"
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=worker, args=(side,)) for side in ("long", "short")]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        assert sorted(outcomes) == ["duplicate", "opened"], f"attempt {attempt}: {outcomes}"
        reloaded = _portfolio(tmp_path, name=name)
        position = reloaded.get_position("SBER")
        assert position is not None
        assert position.lots == 10
        expected_balance = Decimal("99000") if position.side == "long" else Decimal("101000")
        assert reloaded.balance == expected_balance
