"""Тесты paper_account.py: якоря капитала paper-счёта, их устойчивость к рестарту,
расчёт отмеченного капитала/дневного P&L и отказы на неполных данных (AGENTS.md §3)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from trading_bot import database
from trading_bot.paper_account import (
    MissingAnchorError,
    MissingMarketDataError,
    PaperAccountSource,
)
from trading_bot.portfolio import Portfolio, PortfolioError

_TS0 = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)  # 13:00 МСК
_TS0_NEXT_DAY = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)


def _conn(tmp_path: Path, name: str = "tbot.db") -> sqlite3.Connection:
    return database.connect(tmp_path / name)


# --- Пустая БД: инициализация якорей ---------------------------------------------


def test_empty_db_initializes_starting_equity_and_daily_start_balance(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))

    account = source.snapshot(_TS0, {}, {}, {})

    assert account.trading_mode == "paper"
    assert account.starting_equity == Decimal("100000")
    assert account.daily_start_balance == Decimal("100000")
    assert account.balance_free == Decimal("100000")
    assert account.daily_pnl == Decimal("0")
    assert account.trades_today == 0
    assert account.open_positions == ()
    assert account.open_positions_count == 0
    assert account.open_tickers == frozenset()


def test_second_snapshot_same_day_reuses_persisted_anchors(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})

    later_same_day = _TS0.replace(hour=15)
    account = source.snapshot(later_same_day, {}, {}, {})

    assert account.starting_equity == Decimal("100000")
    assert account.daily_start_balance == Decimal("100000")


def test_open_tickers_exposes_current_paper_positions_without_parsing_errors(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    assert source.open_tickers() == frozenset()
    source.snapshot(_TS0, {}, {}, {})
    Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).open_position(
        ticker="SBER",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal("250"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_TS0,
        stop=Decimal("240"),
    )
    assert source.open_tickers() == frozenset({"SBER"})


# --- Устойчивость к рестарту -------------------------------------------------------


def test_anchors_survive_restart_with_new_source_instance(tmp_path: Path) -> None:
    conn = _conn(tmp_path, name="restart.db")
    PaperAccountSource(conn, Decimal("100000")).snapshot(_TS0, {}, {}, {})

    # "Рестарт": новый PaperAccountSource на том же соединении с другим
    # стартовым балансом конструктора — он не должен переопределить якорь.
    restarted = PaperAccountSource(conn, Decimal("999999"))
    account = restarted.snapshot(_TS0, {}, {}, {})

    assert account.starting_equity == Decimal("100000")
    assert account.daily_start_balance == Decimal("100000")


def test_anchors_survive_restart_across_real_connections(tmp_path: Path) -> None:
    db_path = tmp_path / "restart_conn.db"
    conn1 = database.connect(db_path)
    PaperAccountSource(conn1, Decimal("100000")).snapshot(_TS0, {}, {}, {})
    conn1.close()

    conn2 = database.connect(db_path)
    account = PaperAccountSource(conn2, Decimal("100000")).snapshot(_TS0, {}, {}, {})

    assert account.starting_equity == Decimal("100000")
    assert account.daily_start_balance == Decimal("100000")


# --- Непустая история без якоря: отказ, не догадка --------------------------------


def test_nonempty_trades_without_starting_anchor_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    # Сделка появляется в БД до того, как кто-либо вызвал snapshot() —
    # якорь starting_equity никогда не создавался.
    Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).open_position(
        ticker="SBER",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal("250"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_TS0,
        stop=Decimal("240"),
    )

    source = PaperAccountSource(conn, Decimal("100000"))
    with pytest.raises(MissingAnchorError):
        source.snapshot(
            _TS0,
            {"SBER": Decimal("251")},
            {"SBER": Decimal("250")},
            {"SBER": "financials"},
        )


# --- Новый день: опорные цены открытия, не текущие --------------------------------


def _open_sber(
    conn: sqlite3.Connection, *, lots: int = 1, lot_size: int = 10, opened_at: datetime = _TS0
) -> None:
    Portfolio(conn, mode="paper", starting_balance=Decimal("100000")).open_position(
        ticker="SBER",
        side="long",
        lots=lots,
        lot_size=lot_size,
        price=Decimal("250"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=opened_at,
        stop=Decimal("240"),
    )


def test_new_day_anchors_equity_with_opening_prices_not_current_prices(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    # День 0 пуст (сделок ещё нет) — снимок здесь закладывает starting_equity и
    # дневной якорь для дня 0 без открытых позиций (реалистичный порядок: риск
    # проверяется до открытия сделки, не после).
    source.snapshot(_TS0, {}, {}, {})
    _open_sber(conn)

    # Новый торговый день (МСК): SBER уже открыт. Якорь дня 1 обязан считаться по
    # проверенной цене открытия 270, а не по текущей 280/290.
    day1 = source.snapshot(
        _TS0_NEXT_DAY, {"SBER": Decimal("280")}, {"SBER": Decimal("270")}, {"SBER": "financials"}
    )
    # cash после открытия: 100000 - 1*10*250 = 97500; якорь дня 1 = 97500 + 10*270 = 100200.
    assert day1.daily_start_balance == Decimal("100200")
    # daily_pnl = marked(280) - anchor(100200) = (97500 + 2800) - 100200 = 100.
    assert day1.daily_pnl == Decimal("100")

    # Повторный снимок того же дня 1 — игнорирует новую (другую) цену открытия,
    # использует уже закреплённый якорь, а не пересчитывает его.
    day1_again = source.snapshot(
        _TS0_NEXT_DAY, {"SBER": Decimal("290")}, {"SBER": Decimal("999")}, {"SBER": "financials"}
    )
    assert day1_again.daily_start_balance == Decimal("100200")
    assert day1_again.daily_pnl == (Decimal("97500") + Decimal("2900")) - Decimal("100200")


# --- Отказы на неполных данных ------------------------------------------------------


def test_missing_current_price_for_open_position_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})
    _open_sber(conn)

    with pytest.raises(MissingMarketDataError):
        source.snapshot(_TS0, {}, {"SBER": Decimal("250")}, {"SBER": "financials"})


def test_invalid_current_price_for_open_position_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})
    _open_sber(conn)

    with pytest.raises(MissingMarketDataError):
        source.snapshot(
            _TS0, {"SBER": Decimal("-1")}, {"SBER": Decimal("250")}, {"SBER": "financials"}
        )


def test_missing_opening_price_on_new_day_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})
    _open_sber(conn)

    with pytest.raises(MissingMarketDataError):
        source.snapshot(_TS0_NEXT_DAY, {"SBER": Decimal("260")}, {}, {"SBER": "financials"})


def test_missing_sector_for_open_position_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    _open_sber(conn)

    with pytest.raises(MissingMarketDataError):
        source.snapshot(_TS0, {"SBER": Decimal("260")}, {"SBER": Decimal("250")}, {})


def test_missing_lot_size_for_legacy_open_position_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    # Легаси-строка до миграции 0003: лотность неизвестна (docs/06_DB_SCHEMA.md).
    conn.execute(
        "INSERT INTO trades "
        "(ticker, side, lots, lot_size, price, fee, slippage, mode, ts_open, status, "
        " balance_after) "
        "VALUES ('GAZP', 'long', 1, NULL, '150', '0', '0', 'paper', ?, 'open', '100000')",
        (_TS0.isoformat(),),
    )
    conn.commit()

    with pytest.raises(PortfolioError):
        source.snapshot(
            _TS0, {"GAZP": Decimal("151")}, {"GAZP": Decimal("150")}, {"GAZP": "energy"}
        )


# --- Отмеченный капитал: long и short, несколько позиций ----------------------------


def test_marked_equity_handles_long_and_short_positions(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal("250"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_TS0,
        stop=Decimal("240"),
    )
    portfolio.open_position(
        ticker="GAZP",
        side="short",
        lots=2,
        lot_size=10,
        price=Decimal("150"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_TS0,
        stop=Decimal("160"),
    )
    # Баланс после long (-2500) и short (+3000): 100000 - 2500 + 3000 = 100500.
    assert portfolio.balance == Decimal("100500")

    account = source.snapshot(
        _TS0,
        {"SBER": Decimal("255"), "GAZP": Decimal("145")},
        {"SBER": Decimal("250"), "GAZP": Decimal("150")},
        {"SBER": "financials", "GAZP": "energy"},
    )

    assert account.balance_free == Decimal("100500")
    assert account.open_positions_count == 2
    assert account.open_tickers == frozenset({"SBER", "GAZP"})
    assert account.open_positions_by_sector == {"financials": 1, "energy": 1}
    by_ticker = {pos.ticker: pos for pos in account.open_positions}
    assert by_ticker["SBER"].market_price == Decimal("255")
    assert by_ticker["SBER"].lot_size == 10
    assert by_ticker["SBER"].stop_price == Decimal("240")
    assert by_ticker["GAZP"].side == "short"
    # marked equity = cash(100500) + long(1*10*255=2550) - short(2*10*145=2900) = 100150
    # daily_start_balance тот же день = cash(100500) + long(2500) - short(3000) = 100000
    assert account.daily_start_balance == Decimal("100000")
    assert account.daily_pnl == Decimal("100150") - Decimal("100000")


# --- trades_today: считает сделки, не FIFO-расщепления ------------------------------


def test_trades_today_counts_distinct_journal_entries_at_same_timestamp(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})
    conn.executemany(
        "INSERT INTO decisions (id, ts, ticker, mode) VALUES (?, ?, 'SBER', 'paper')",
        [(journal_id, _TS0.isoformat()) for journal_id in (101, 102)],
    )
    conn.commit()
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    for journal_id in (101, 102):
        portfolio.open_position(
            ticker="SBER",
            side="long",
            lots=2,
            lot_size=1,
            price=Decimal("100"),
            fee=Decimal("0"),
            slippage=Decimal("0"),
            journal_id=journal_id,
            opened_at=_TS0,
            stop=Decimal("90"),
        )
    portfolio.close_position(
        ticker="SBER",
        lots=1,
        exit_price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="manual",
        closed_at=_TS0,
    )

    account = source.snapshot(_TS0, {"SBER": Decimal("100")}, {}, {"SBER": "financials"})

    assert account.trades_today == 2
    conn.close()


def test_trades_today_counts_entries_not_partial_close_splits(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=10,
        lot_size=1,
        price=Decimal("100"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_TS0,
        stop=Decimal("90"),
    )
    # Частичное закрытие в тот же день расщепляет FIFO-порцию на closed+remainder —
    # это по-прежнему одна и та же сделка, не вторая.
    portfolio.close_position(
        ticker="SBER",
        lots=4,
        exit_price=Decimal("105"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="manual",
        closed_at=_TS0.replace(hour=12),
    )

    account = source.snapshot(
        _TS0.replace(hour=13),
        {"SBER": Decimal("106")},
        {"SBER": Decimal("100")},
        {"SBER": "financials"},
    )
    assert account.trades_today == 1

    # Вторая реальная сделка в тот же день — теперь действительно 2.
    portfolio.open_position(
        ticker="GAZP",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal("150"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_TS0.replace(hour=14),
        stop=Decimal("140"),
    )
    account2 = source.snapshot(
        _TS0.replace(hour=15),
        {"SBER": Decimal("106"), "GAZP": Decimal("151")},
        {"SBER": Decimal("100"), "GAZP": Decimal("150")},
        {"SBER": "financials", "GAZP": "energy"},
    )
    assert account2.trades_today == 2


# --- Якорь не угадывается по текущему (уже изменённому) состоянию ------------------


def test_missing_daily_anchor_with_same_day_trade_activity_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    # День 0: снимок на пустой БД закладывает starting_equity и якорь дня 0.
    source.snapshot(_TS0, {}, {}, {})
    # Сделка открыта прямо на день 1 в обход PaperAccountSource — снимок дня 1 ни разу
    # не вызывался до этого, поэтому его якорь отсутствует, а cash/позиции уже не
    # соответствуют состоянию на открытие дня 1.
    _open_sber(conn, opened_at=_TS0_NEXT_DAY)

    with pytest.raises(MissingAnchorError):
        source.snapshot(
            _TS0_NEXT_DAY,
            {"SBER": Decimal("260")},
            {"SBER": Decimal("250")},
            {"SBER": "financials"},
        )


def test_missing_daily_anchor_with_same_day_partial_close_is_rejected(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})
    # Позиция открыта предыдущим днём (уже заякоренным), но закрыта частично уже на
    # дне 1 — тоже торговая активность дня 1, хоть ts_open и не совпадает с ним.
    portfolio = Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    portfolio.open_position(
        ticker="SBER",
        side="long",
        lots=1,
        lot_size=10,
        price=Decimal("250"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        opened_at=_TS0,
        stop=Decimal("240"),
    )
    portfolio.close_position(
        ticker="SBER",
        lots=1,
        exit_price=Decimal("260"),
        fee=Decimal("0"),
        slippage=Decimal("0"),
        exit_reason="manual",
        closed_at=_TS0_NEXT_DAY,
    )

    with pytest.raises(MissingAnchorError):
        source.snapshot(_TS0_NEXT_DAY, {}, {}, {})


def test_starting_anchor_rejects_mismatched_persisted_balance_without_trades(
    tmp_path: Path,
) -> None:
    conn = _conn(tmp_path)
    # Баланс paper-счёта уже персистентен (100000), но ни одной сделки и ни одного
    # якоря ещё нет. "Рестарт" с другим starting_balance не должен угадать 50000 как
    # стартовый капитал поверх уже существующего факта.
    Portfolio(conn, mode="paper", starting_balance=Decimal("100000"))
    source = PaperAccountSource(conn, Decimal("50000"))

    with pytest.raises(MissingAnchorError):
        source.snapshot(_TS0, {}, {}, {})


def test_invalid_current_price_on_new_day_does_not_persist_daily_anchor(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    source.snapshot(_TS0, {}, {}, {})
    _open_sber(conn)

    with pytest.raises(MissingMarketDataError):
        source.snapshot(
            _TS0_NEXT_DAY,
            {"SBER": Decimal("-1")},
            {"SBER": Decimal("270")},
            {"SBER": "financials"},
        )

    # Текущая цена проверяется раньше, чем коммитится якорь дня 1 — несмотря на то,
    # что opening_prices были пригодны, в БД не должно остаться частично посчитанного
    # якоря для дня 1.
    day1_key = _TS0_NEXT_DAY.date().isoformat()
    row = conn.execute(
        "SELECT 1 FROM paper_equity_anchors WHERE anchor_type = 'daily' AND anchor_key = ?",
        (day1_key,),
    ).fetchone()
    assert row is None


# --- Валидация аргументов конструктора/snapshot --------------------------------------


def test_constructor_rejects_non_positive_starting_balance(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    with pytest.raises(ValueError):
        PaperAccountSource(conn, Decimal("0"))


def test_snapshot_rejects_naive_now(tmp_path: Path) -> None:
    conn = _conn(tmp_path)
    source = PaperAccountSource(conn, Decimal("100000"))
    with pytest.raises(ValueError):
        source.snapshot(datetime(2026, 1, 5, 10, 0), {}, {}, {})  # noqa: DTZ001
