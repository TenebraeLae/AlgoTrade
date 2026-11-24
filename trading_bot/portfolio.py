"""Учёт позиций и сделок (слой L2, docs/01_ARCHITECTURE.md, docs/06_DB_SCHEMA.md).

Разрешено: писать в `trades`/`positions` через `database.py`, считать P&L по
методу FIFO (не по средней цене входа — docs/02_CODING_RULES.md, п.6), отдавать
агрегированный снимок позиции (`Position`) для risk_manager/дашборда. Запрещено:
знать о брокере/gRPC (это `api_client.py`/`broker/`), решать, торговать ли (это
`strategy.py`), считать размер позиции (это `risk_manager.py`) — портфель только
фиксирует то, что уже произошло, и отдаёт факты.

Ключевой инвариант учёта (docs/06_DB_SCHEMA.md, «7 проверок целостности», docs/02_CODING_RULES.md):
одна строка `trades` — одна (порция) сделка, которая либо целиком открыта, либо
целиком закрыта на этой же строке; при частичном закрытии FIFO-порция расщепляется
на закрытую (эта строка) и оставшуюся открытую (новая строка), а не переписывается
как одна усреднённая позиция.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

Side = Literal["long", "short"]

#: Разрешённые причины закрытия сделки (docs/06_DB_SCHEMA.md, проверка №5).
EXIT_REASONS: frozenset[str] = frozenset(
    {"take", "stop", "trail", "timeout", "panic", "manual", "eod"}
)

#: Источник строки в `positions` (docs/06_DB_SCHEMA.md): открыта ботом, восстановлена
#: после рестарта из БД, либо расхождение с брокером, которое бот не закрывает сам
#: (docs/00_PLAN.md §10 — ждём решения человека).
PositionSource = Literal["bot", "restored", "orphan"]


def utcnow() -> datetime:
    """Единая точка получения текущего времени — tz-aware UTC (docs/02_CODING_RULES.md)."""
    return datetime.now(tz=UTC)


class PortfolioError(Exception):
    """Базовая ошибка учёта — часть будущей иерархии `TBotError`."""


class DuplicateTickerSideError(PortfolioError):
    """Попытка открыть позицию по тикеру, где уже открыта позиция другой стороны.

    docs/06_DB_SCHEMA.md, проверка №4: «нет двух открытых позиций с одним тикером».
    Наращивание в ту же сторону (scaling in) разрешено — это не дубликат.
    """


class InsufficientPositionError(PortfolioError):
    """Попытка закрыть больше лотов, чем открыто по тикеру."""


class UnknownExitReasonError(PortfolioError):
    """`exit_reason` не входит в разрешённый список (docs/06_DB_SCHEMA.md, проверка №5)."""


@dataclass(frozen=True)
class Position:
    """Агрегированный снимок открытой позиции (docs/01_ARCHITECTURE.md контракт).

    `avg_entry` — средневзвешенная цена входа по всем открытым FIFO-порциям,
    только для отображения/риск-снимка. P&L при закрытии считается по FIFO,
    не по этой средней (docs/02_CODING_RULES.md, п.6).
    """

    ticker: str
    side: Side
    lots: int
    avg_entry: Decimal
    stop: Decimal | None
    take: Decimal | None
    opened_at: datetime
    journal_id: int | None
    source: PositionSource


@dataclass(frozen=True)
class ClosedTrade:
    """Результат закрытия одной FIFO-порции — то, что было записано в `trades`."""

    trade_id: int
    ticker: str
    side: Side
    lots: int
    entry_price: Decimal
    exit_price: Decimal
    exit_reason: str
    pnl_gross: Decimal
    pnl_net: Decimal
    pnl_pct: Decimal
    balance_after: Decimal


@dataclass(frozen=True)
class _OpenLot:
    trade_id: int
    ticker: str
    side: Side
    lots: int
    lot_size: int
    entry_price: Decimal
    entry_fee: Decimal
    entry_slippage: Decimal
    ts_open: datetime
    mode: str
    journal_id: int | None


class Portfolio:
    """Учёт сделок и позиций поверх SQLite (`database.connect()`).

    Один `Portfolio` — один режим (`mode`): paper и live не смешиваются в одной
    серии данных (docs/06_DB_SCHEMA.md, проверка №7).
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        mode: str,
        starting_balance: Decimal,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._conn = conn
        self._mode = mode
        self._clock = clock
        self._balance = self._load_balance(default=starting_balance)
        self._persist_balance()
        self._conn.commit()

    @property
    def balance(self) -> Decimal:
        """Свободный денежный остаток — не включает стоимость открытых позиций."""
        return self._balance

    def refresh_balance(self) -> Decimal:
        """Перечитывает остаток из SQLite под блокировкой вызывающего paper-цикла."""
        row = self._conn.execute(
            "SELECT balance FROM portfolio_balances WHERE mode = ?", (self._mode,)
        ).fetchone()
        if row is None:
            raise PortfolioError("остаток портфеля отсутствует; нужна ручная сверка")
        self._balance = Decimal(row[0])
        return self._balance

    def _load_balance(self, *, default: Decimal) -> Decimal:
        """Читает сохранённый остаток из `portfolio_balances`.

        Баланс хранится отдельно от `trades.balance_after`, потому что после
        рестарта без единой сделки (пустой сценарий) в `trades` для данного `mode`
        может не быть ни одной строки — тогда падение на `default` тихо стёрло бы
        реально накопленный остаток (docs/15_ACCEPTANCE.md, M6: 20 сценариев рестарта).
        """
        row = self._conn.execute(
            "SELECT balance FROM portfolio_balances WHERE mode = ?",
            (self._mode,),
        ).fetchone()
        if row is None:
            return default
        return Decimal(row[0])

    def _persist_balance(self) -> None:
        self._conn.execute(
            "INSERT INTO portfolio_balances (mode, balance, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(mode) DO UPDATE SET balance=excluded.balance, "
            "updated_at=excluded.updated_at",
            (self._mode, str(self._balance), self._clock().isoformat()),
        )

    def _open_lots(self, ticker: str) -> list[_OpenLot]:
        rows = self._conn.execute(
            "SELECT id, ticker, side, lots, lot_size, price, fee, slippage, "
            "ts_open, mode, journal_id "
            "FROM trades WHERE ticker = ? AND mode = ? AND status = 'open' "
            "ORDER BY ts_open ASC, id ASC",
            (ticker, self._mode),
        ).fetchall()
        for row in rows:
            if row[4] is None:
                raise PortfolioError(
                    f"сделка id={row[0]} тикер={ticker}: лотность неизвестна; "
                    "сверить фактическую позицию и баланс до новых сделок"
                )
        return [
            _OpenLot(
                trade_id=row[0],
                ticker=row[1],
                side=row[2],
                lots=int(row[3]),
                lot_size=int(row[4]),
                entry_price=Decimal(row[5]),
                entry_fee=Decimal(row[6]),
                entry_slippage=Decimal(row[7]),
                ts_open=datetime.fromisoformat(row[8]),
                mode=row[9],
                journal_id=row[10],
            )
            for row in rows
        ]

    def open_tickers(self) -> frozenset[str]:
        """Множество тикеров с хотя бы одной открытой FIFO-порцией в текущем режиме."""
        rows = self._conn.execute(
            "SELECT DISTINCT ticker FROM trades WHERE mode = ? AND status = 'open'",
            (self._mode,),
        ).fetchall()
        return frozenset(row[0] for row in rows)

    def get_position(self, ticker: str) -> Position | None:
        """Агрегированный снимок открытой позиции по тикеру, если она есть."""
        lots = self._open_lots(ticker)
        if not lots:
            return None
        total_lots = sum(lot.lots for lot in lots)
        total_units = sum(lot.lots * lot.lot_size for lot in lots)
        weighted_price = sum(
            (lot.entry_price * lot.lots * lot.lot_size for lot in lots), Decimal(0)
        )
        avg_entry = weighted_price / Decimal(total_units)
        pos_row = self._conn.execute(
            "SELECT stop, take, source FROM positions WHERE ticker = ?", (ticker,)
        ).fetchone()
        stop = Decimal(pos_row[0]) if pos_row and pos_row[0] is not None else None
        take = Decimal(pos_row[1]) if pos_row and pos_row[1] is not None else None
        source: PositionSource = pos_row[2] if pos_row else "bot"
        return Position(
            ticker=ticker,
            side=lots[0].side,
            lots=total_lots,
            avg_entry=avg_entry,
            stop=stop,
            take=take,
            opened_at=lots[0].ts_open,
            journal_id=lots[0].journal_id,
            source=source,
        )

    def _upsert_position_row(
        self,
        ticker: str,
        *,
        stop: Decimal | None,
        take: Decimal | None,
        source: PositionSource,
    ) -> None:
        position = self.get_position(ticker)
        if position is None:
            self._conn.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
            return
        self._conn.execute(
            "INSERT INTO positions "
            "(ticker, side, lots, avg_entry, stop, take, trail_state, ts_open, mode, "
            " journal_id, source) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ticker) DO UPDATE SET "
            "side=excluded.side, lots=excluded.lots, avg_entry=excluded.avg_entry, "
            "stop=excluded.stop, take=excluded.take, mode=excluded.mode, "
            "journal_id=excluded.journal_id, source=excluded.source",
            (
                ticker,
                position.side,
                position.lots,
                str(position.avg_entry),
                str(stop) if stop is not None else None,
                str(take) if take is not None else None,
                None,
                position.opened_at.isoformat(),
                self._mode,
                position.journal_id,
                source,
            ),
        )

    def open_position(
        self,
        *,
        ticker: str,
        side: Side,
        lots: int,
        lot_size: int,
        price: Decimal,
        fee: Decimal,
        slippage: Decimal,
        journal_id: int | None = None,
        opened_at: datetime | None = None,
        stop: Decimal | None = None,
        take: Decimal | None = None,
    ) -> int:
        """Открывает новую FIFO-порцию позиции (или наращивает существующую).

        `price` задаётся за одну бумагу, `lots` — число лотов, `lot_size` — бумаг
        в лоте. Комиссия и проскальзывание — отдельные денежные суммы.
        Возврат: `id` новой строки `trades`.
        Отказ: `DuplicateTickerSideError`, если по тикеру уже открыта позиция
        противоположной стороны (docs/06_DB_SCHEMA.md, проверка №4).
        """
        if lots <= 0:
            raise ValueError("lots должен быть положительным")
        if lot_size <= 0:
            raise ValueError("lot_size должен быть положительным")
        ts_open = opened_at or self._clock()
        if ts_open.tzinfo is None or ts_open.utcoffset() is None:
            raise ValueError("время открытия должно быть tz-aware")
        ts_open = ts_open.astimezone(UTC)
        previous_balance = self._balance
        notional = price * Decimal(lots * lot_size)
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self.refresh_balance()
            existing = self.get_position(ticker)
            if existing is not None and existing.side != side:
                raise DuplicateTickerSideError(
                    f"по {ticker} уже открыта позиция {existing.side}, нельзя открыть {side}"
                )
            if side == "long":
                self._balance -= notional + fee + slippage
            else:
                self._balance += notional - fee - slippage

            cursor = self._conn.execute(
                "INSERT INTO trades "
                "(journal_id, ticker, side, lots, lot_size, price, fee, slippage, mode, ts_open, "
                " status, balance_after) "
                "VALUES (?,?,?,?,?,?,?,?,?,?, 'open', ?)",
                (
                    journal_id,
                    ticker,
                    side,
                    lots,
                    lot_size,
                    str(price),
                    str(fee),
                    str(slippage),
                    self._mode,
                    ts_open.isoformat(),
                    str(self._balance),
                ),
            )
            trade_id = cursor.lastrowid
            assert trade_id is not None
            self._upsert_position_row(ticker, stop=stop, take=take, source="bot")
            self._persist_balance()
            self._conn.commit()
        except (sqlite3.Error, PortfolioError):
            self._conn.rollback()
            self._balance = previous_balance
            raise
        return trade_id

    def close_position(
        self,
        *,
        ticker: str,
        lots: int,
        exit_price: Decimal,
        fee: Decimal,
        slippage: Decimal,
        exit_reason: str,
        closed_at: datetime | None = None,
    ) -> list[ClosedTrade]:
        """Закрывает `lots` штук по тикеру FIFO — с самых старых открытых порций.

        Если запрошенное количество меньше размера конкретной FIFO-порции, эта
        порция расщепляется: закрытая часть фиксируется на исходной строке,
        остаток переносится в новую строку `status='open'` с тем же временем
        открытия (FIFO-порядок не нарушается).

        Возврат: список `ClosedTrade` — одна запись на каждую затронутую строку.
        Отказ: `InsufficientPositionError`, если открытых лотов меньше запрошенных;
        `UnknownExitReasonError`, если `exit_reason` не из разрешённого списка.
        """
        if exit_reason not in EXIT_REASONS:
            raise UnknownExitReasonError(f"{exit_reason!r} не входит в {sorted(EXIT_REASONS)}")
        if lots <= 0:
            raise ValueError("lots должен быть положительным")

        open_lots = self._open_lots(ticker)
        available = sum(lot.lots for lot in open_lots)
        if available < lots:
            raise InsufficientPositionError(
                f"по {ticker} открыто {available} лотов, запрошено закрыть {lots}"
            )

        ts_close = closed_at or self._clock()
        remaining_to_close = lots
        results: list[ClosedTrade] = []

        for lot in open_lots:
            if remaining_to_close <= 0:
                break
            close_qty = min(lot.lots, remaining_to_close)
            ratio = Decimal(close_qty) / Decimal(lot.lots)
            entry_fee_share = lot.entry_fee * ratio
            entry_slippage_share = lot.entry_slippage * ratio
            exit_fee_share = fee * Decimal(close_qty) / Decimal(lots)
            exit_slippage_share = slippage * Decimal(close_qty) / Decimal(lots)

            closed_units = Decimal(close_qty * lot.lot_size)
            if lot.side == "long":
                pnl_gross = (exit_price - lot.entry_price) * closed_units
                self._balance += exit_price * closed_units - exit_fee_share - exit_slippage_share
            else:
                pnl_gross = (lot.entry_price - exit_price) * closed_units
                self._balance -= exit_price * closed_units + exit_fee_share + exit_slippage_share

            total_costs = (
                entry_fee_share + entry_slippage_share + exit_fee_share + exit_slippage_share
            )
            pnl_net = pnl_gross - total_costs
            entry_notional = lot.entry_price * closed_units
            pnl_pct = (
                (pnl_net / entry_notional) * Decimal(100) if entry_notional != 0 else Decimal(0)
            )

            self._conn.execute(
                "UPDATE trades SET lots=?, fee=?, slippage=?, ts_close=?, exit_reason=?, "
                "pnl_gross=?, pnl_net=?, pnl_pct=?, status='closed', balance_after=? "
                "WHERE id=?",
                (
                    close_qty,
                    str(entry_fee_share + exit_fee_share),
                    str(entry_slippage_share + exit_slippage_share),
                    ts_close.isoformat(),
                    exit_reason,
                    str(pnl_gross),
                    str(pnl_net),
                    str(pnl_pct),
                    str(self._balance),
                    lot.trade_id,
                ),
            )

            remainder = lot.lots - close_qty
            if remainder > 0:
                remainder_fee = lot.entry_fee - entry_fee_share
                remainder_slippage = lot.entry_slippage - entry_slippage_share
                self._conn.execute(
                    "INSERT INTO trades "
                    "(journal_id, ticker, side, lots, lot_size, price, fee, slippage, mode, "
                    " ts_open, status, balance_after) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?, 'open', ?)",
                    (
                        lot.journal_id,
                        lot.ticker,
                        lot.side,
                        remainder,
                        lot.lot_size,
                        str(lot.entry_price),
                        str(remainder_fee),
                        str(remainder_slippage),
                        self._mode,
                        lot.ts_open.isoformat(),
                        str(self._balance),
                    ),
                )

            results.append(
                ClosedTrade(
                    trade_id=lot.trade_id,
                    ticker=ticker,
                    side=lot.side,
                    lots=close_qty,
                    entry_price=lot.entry_price,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    pnl_gross=pnl_gross,
                    pnl_net=pnl_net,
                    pnl_pct=pnl_pct,
                    balance_after=self._balance,
                )
            )
            remaining_to_close -= close_qty

        self._upsert_position_row(ticker, stop=None, take=None, source="bot")
        self._persist_balance()
        self._conn.commit()
        return results

    def reconcile(self, broker_open_lots: Mapping[str, int]) -> frozenset[str]:
        """Сверяет открытые позиции в БД с тем, что реально видит брокер.

        Расхождение (позиция есть в БД, но брокер её не подтверждает, или наоборот)
        помечается `source='orphan'` в `positions` — бот **не закрывает** её сам,
        ждёт решения человека (docs/00_PLAN.md §10, docs/13_SECURITY.md).

        Возврат: множество тикеров, помеченных как orphan на этом вызове.
        """
        db_tickers = self.open_tickers()
        broker_tickers = frozenset(t for t, lots in broker_open_lots.items() if lots > 0)
        mismatched = db_tickers ^ broker_tickers
        for ticker in mismatched:
            if ticker in db_tickers:
                self._upsert_position_row(ticker, stop=None, take=None, source="orphan")
        if mismatched:
            self._conn.commit()
        return mismatched
