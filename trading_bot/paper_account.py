"""Снимок paper-счёта для риск-менеджера (docs/01_ARCHITECTURE.md, docs/04_RISK.md).

Разрешено: собрать `risk_manager.AccountState` из локального paper `Portfolio`
(FIFO-учёт сделок) и устойчивых якорей капитала в SQLite (`paper_equity_anchors`,
миграция `0005_paper_equity_anchors.sql`). Запрещено: знать о брокере/gRPC (это
`broker/`/`api_client.py`), решать размер позиции или проверять лимиты (это
`risk_manager.py`) — этот модуль только читает факты и либо отдаёт их, либо
отказывает явной ошибкой, если факта не хватает.

Два якоря капитала персистентны и никогда не пересчитываются задним числом
(AGENTS.md §3 «догадки дешевле, чем проверка — проверяй, а не додумывай»):

- `starting_equity` — капитал на момент самого первого снимка пустой paper БД
  (ровно одна запись на всю жизнь paper-счёта, mode='paper' в `trades` отсутствует).
  Если в `trades` уже есть сделки, а этого якоря нет — это разрыв истории, которую
  нельзя восстановить задним числом: `MissingAnchorError`, а не догадка.
- `daily_start_balance` — капитал на открытие конкретного торгового дня по МСК;
  при первом снимке нового дня считается по переданным проверенным ценам открытия
  (`opening_prices`) для всех на тот момент открытых позиций, а не по текущим ценам.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from trading_bot import risk_manager
from trading_bot.portfolio import Portfolio
from trading_bot.utils import MSK

_ANCHOR_STARTING = "starting"
_ANCHOR_DAILY = "daily"
_GLOBAL_KEY = "global"


class PaperAccountError(Exception):
    """Базовая ошибка сборки paper-снимка счёта."""


class MissingAnchorError(PaperAccountError):
    """Нужного якоря капитала нет, а состояние уже не "чистое" — доверять
    текущим cash/позициям как стартовой точке нельзя.

    Три источника отказа: (1) в `trades` уже есть paper-сделки, а
    `starting_equity` не сохранён; (2) сделок нет, но персистентный баланс
    `portfolio_balances` не совпадает с `starting_balance` конструктора —
    значит, это не первый чистый запуск, а параметр показан неверно; (3) для
    текущего торгового дня по МСК нет `daily_start_balance`, а в этот день уже
    была торговая активность — текущие cash/позиции уже отражают её, и якорь
    из них стёр бы дневной результат. Ни один из трёх случаев не восстанавливается
    задним числом (AGENTS.md §3 «не додумывай»).
    """


class MissingMarketDataError(PaperAccountError):
    """Цена, лотность или сектор открытой позиции отсутствуют/непригодны.

    Снимок счёта не строится на частичных данных — вызывающий код обязан
    принести проверенные цену, лотность и сектор для каждой открытой позиции.
    """


def _positive_decimal(value: object) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


@dataclass(frozen=True)
class _OpenLotSummary:
    ticker: str
    side: risk_manager.Side
    lots: int
    lot_size: int
    stop: Decimal | None
    sector: str


class PaperAccountSource:
    """Собирает `risk_manager.AccountState` из paper `Portfolio` и SQLite-якорей.

    Один экземпляр — одно соединение; `snapshot()` каждый раз читает `Portfolio`
    заново, чтобы видеть сделки, записанные другими владельцами того же `conn`
    между вызовами (например, `executor.PaperCycle`), а не кэш в памяти.
    """

    def __init__(self, conn: sqlite3.Connection, starting_balance: Decimal) -> None:
        if not _positive_decimal(starting_balance):
            raise ValueError("starting_balance должен быть положительным конечным Decimal")
        self._conn = conn
        self._starting_balance = starting_balance

    def open_tickers(self) -> frozenset[str]:
        """Читает тикеры открытых paper-позиций из SQLite для рыночного источника.

        Не вычисляет цены/риск и не принимает брокерские данные; вызывающий обязан
        затем собрать полный `snapshot` по этим позициям до бумажного решения.
        """
        return Portfolio(
            self._conn, mode="paper", starting_balance=self._starting_balance
        ).open_tickers()

    def snapshot(
        self,
        now: datetime,
        prices: Mapping[str, Decimal],
        opening_prices: Mapping[str, Decimal],
        sectors: Mapping[str, str],
        *,
        trading_day: date | None = None,
        trading_day_confirmed: bool = True,
    ) -> risk_manager.AccountState:
        """Строит снимок счёта на момент `now` (МСК-дата берётся из `now`).

        `prices` — проверенные текущие цены, нужны на каждую открытую позицию
        для отметки по рынку. `opening_prices` — проверенные цены открытия дня,
        используются только при первом снимке нового торгового дня (МСК), чтобы
        зафиксировать `daily_start_balance` без догадок по текущей цене.
        `sectors` — сектор на каждый открытый тикер, для лимита по секторам.
        `trading_day_confirmed` устанавливает поставщик только после закрытой
        свечи открытия нового торгового дня. Без подтверждения якорь нового дня
        не создаётся, а старый стоп остаётся активен; прямой вызов по умолчанию
        означает, что вызывающий уже подтвердил день.

        Отказ: `MissingAnchorError` — непустая paper-история без якоря старта;
        `MissingMarketDataError` — нет пригодной цены/лотности/сектора открытой
        позиции (включая отсутствующую цену открытия в день создания якоря).
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now должен быть tz-aware")

        portfolio = Portfolio(self._conn, mode="paper", starting_balance=self._starting_balance)
        cash = portfolio.balance
        today = now.astimezone(MSK).date()
        if trading_day is not None and trading_day != today:
            raise ValueError("дата торгового бара должна совпадать с текущим МСК-днём")
        msk_date = today.isoformat()

        summaries = [
            self._open_lot_summary(portfolio, ticker, sectors)
            for ticker in sorted(portfolio.open_tickers())
        ]

        # Текущие цены проверяются до того, как коммитится любой якорь — отказ
        # на неполных данных не должен оставить в SQLite частично посчитанный
        # якорь дня/старта, на который опёрся бы следующий вызов.
        validated_prices = self._validate_current_prices(summaries, prices)

        starting_equity = self._load_or_init_starting_equity(now=now, cash=cash)
        if trading_day_confirmed:
            daily_start_balance = self._load_or_init_daily_start_balance(
                msk_date=msk_date,
                cash=cash,
                summaries=summaries,
                opening_prices=opening_prices,
                now=now,
            )
            anchor_day = today
        else:
            row = self._conn.execute(
                "SELECT anchor_key, equity FROM paper_equity_anchors "
                "WHERE anchor_type = ? AND anchor_key <= ? ORDER BY anchor_key DESC LIMIT 1",
                (_ANCHOR_DAILY, msk_date),
            ).fetchone()
            if row is None:
                raise MissingAnchorError("нет подтверждённого якоря торгового дня")
            anchor_day = date.fromisoformat(row[0])
            daily_start_balance = Decimal(row[1])
        stopped = (
            self._conn.execute(
                "SELECT 1 FROM paper_daily_stop_latches WHERE trading_day = ?",
                (anchor_day.isoformat(),),
            ).fetchone()
            is not None
        )

        open_positions: list[risk_manager.OpenPositionState] = []
        by_sector: dict[str, int] = {}
        marked_equity = cash
        for summary in summaries:
            price = validated_prices[summary.ticker]
            value = Decimal(summary.lots * summary.lot_size) * price
            marked_equity += value if summary.side == "long" else -value
            by_sector[summary.sector] = by_sector.get(summary.sector, 0) + 1
            open_positions.append(
                risk_manager.OpenPositionState(
                    ticker=summary.ticker,
                    side=summary.side,
                    lots=summary.lots,
                    lot_size=summary.lot_size,
                    stop_price=summary.stop,
                    market_price=price,
                )
            )

        trades_today = self._count_trades_today(anchor_day.isoformat())
        daily_pnl = marked_equity - daily_start_balance

        return risk_manager.AccountState(
            trading_mode="paper",
            balance_free=cash,
            trades_today=trades_today,
            daily_pnl=daily_pnl,
            daily_start_balance=daily_start_balance,
            starting_equity=starting_equity,
            open_positions=tuple(open_positions),
            open_positions_count=len(open_positions),
            open_positions_by_sector=by_sector,
            open_tickers=frozenset(summary.ticker for summary in summaries),
            daily_anchor_day=anchor_day,
            trading_day_confirmed=anchor_day == today,
            daily_stop_active=stopped,
        )

    def _validate_current_prices(
        self, summaries: list[_OpenLotSummary], prices: Mapping[str, Decimal]
    ) -> dict[str, Decimal]:
        validated: dict[str, Decimal] = {}
        for summary in summaries:
            price = prices.get(summary.ticker)
            if not _positive_decimal(price):
                raise MissingMarketDataError(
                    f"нет пригодной текущей цены для открытой позиции {summary.ticker}"
                )
            assert price is not None  # для mypy — проверено выше через _positive_decimal
            validated[summary.ticker] = price
        return validated

    def _open_lot_summary(
        self, portfolio: Portfolio, ticker: str, sectors: Mapping[str, str]
    ) -> _OpenLotSummary:
        position = portfolio.get_position(ticker)
        assert position is not None  # ticker взят из portfolio.open_tickers()
        rows = self._conn.execute(
            "SELECT DISTINCT lot_size FROM trades WHERE ticker = ? AND mode = 'paper' "
            "AND status = 'open'",
            (ticker,),
        ).fetchall()
        lot_sizes = {row[0] for row in rows}
        if len(lot_sizes) != 1 or None in lot_sizes:
            raise MissingMarketDataError(
                f"лотность открытой позиции {ticker} неизвестна или неоднозначна в БД"
            )
        sector = sectors.get(ticker)
        if not sector:
            raise MissingMarketDataError(f"нет сектора для открытой позиции {ticker}")
        return _OpenLotSummary(
            ticker=ticker,
            side=position.side,
            lots=position.lots,
            lot_size=int(lot_sizes.pop()),
            stop=position.stop,
            sector=sector,
        )

    def _load_or_init_starting_equity(self, *, now: datetime, cash: Decimal) -> Decimal:
        row = self._conn.execute(
            "SELECT equity FROM paper_equity_anchors WHERE anchor_type = ? AND anchor_key = ?",
            (_ANCHOR_STARTING, _GLOBAL_KEY),
        ).fetchone()
        if row is not None:
            return Decimal(row[0])

        has_trades = (
            self._conn.execute("SELECT 1 FROM trades WHERE mode = 'paper' LIMIT 1").fetchone()
            is not None
        )
        if has_trades:
            raise MissingAnchorError(
                "в paper БД уже есть сделки, а starting_equity не сохранён — "
                "восстановить его задним числом нельзя"
            )
        if cash != self._starting_balance:
            # Сделок нет, но персистентный cash (portfolio_balances) уже отличается
            # от starting_balance этого запуска — кто-то уже вёл этот paper-счёт с
            # другим стартовым капиталом. Подставлять новый параметр как факт — догадка.
            raise MissingAnchorError(
                f"сохранённый баланс paper-счёта ({cash}) не совпадает со starting_balance "
                f"конструктора ({self._starting_balance}) при отсутствии сделок — "
                "стартовый капитал нельзя угадать"
            )

        self._insert_anchor(_ANCHOR_STARTING, _GLOBAL_KEY, self._starting_balance, now=now)
        return self._starting_balance

    def _load_or_init_daily_start_balance(
        self,
        *,
        msk_date: str,
        cash: Decimal,
        summaries: list[_OpenLotSummary],
        opening_prices: Mapping[str, Decimal],
        now: datetime,
    ) -> Decimal:
        row = self._conn.execute(
            "SELECT equity FROM paper_equity_anchors WHERE anchor_type = ? AND anchor_key = ?",
            (_ANCHOR_DAILY, msk_date),
        ).fetchone()
        if row is not None:
            return Decimal(row[0])

        if self._trade_activity_on(msk_date):
            # Якоря дня нет, но в этот день уже открывали/закрывали сделки без него
            # (например, якорь потерян при рестарте среди дня). Текущие cash/позиции
            # уже отражают эту активность — закладывать их в начало дня стёрло бы
            # фактический внутридневной результат, а не зафиксировало бы его честно.
            raise MissingAnchorError(
                f"дневной якорь капитала за {msk_date} отсутствует, но в этот день уже "
                "была торговая активность — восстановить стартовый капитал дня задним "
                "числом нельзя"
            )

        equity = cash
        for summary in summaries:
            opening_price = opening_prices.get(summary.ticker)
            if not _positive_decimal(opening_price):
                raise MissingMarketDataError(
                    f"нет проверенной цены открытия дня для открытой позиции "
                    f"{summary.ticker} — нельзя зафиксировать daily_start_balance"
                )
            assert opening_price is not None
            value = Decimal(summary.lots * summary.lot_size) * opening_price
            equity += value if summary.side == "long" else -value

        self._insert_anchor(_ANCHOR_DAILY, msk_date, equity, now=now)
        return equity

    def _insert_anchor(
        self, anchor_type: str, anchor_key: str, equity: Decimal, *, now: datetime
    ) -> None:
        self._conn.execute(
            "INSERT INTO paper_equity_anchors (anchor_type, anchor_key, equity, created_at) "
            "VALUES (?, ?, ?, ?)",
            (anchor_type, anchor_key, str(equity), now.astimezone(UTC).isoformat()),
        )
        self._conn.commit()

    def _count_trades_today(self, msk_date: str) -> int:
        """Считает входы по journal_id, не удваивая FIFO-расщепления.

        Для legacy-сделок без journal_id сохраняется группировка по времени и
        тикеру; новые входы с разными решениями не сливаются при одинаковом ts.
        """
        rows = self._conn.execute(
            "SELECT journal_id, ticker, side, ts_open FROM trades WHERE mode = 'paper'"
        ).fetchall()
        entries: set[tuple[str, str, str]] = set()
        for journal_id, ticker, side, ts_open in rows:
            opened_msk_date = datetime.fromisoformat(ts_open).astimezone(MSK).date().isoformat()
            if opened_msk_date == msk_date:
                key = (
                    ("journal", str(journal_id), "")
                    if journal_id is not None
                    else (ticker, side, ts_open)
                )
                entries.add(key)
        return len(entries)

    def _trade_activity_on(self, msk_date: str) -> bool:
        """Есть ли в `trades` (mode='paper') сделка, открытая или закрытая в этот
        МСК-день — открытие и закрытие одинаково мутируют cash/позиции дня."""
        rows = self._conn.execute(
            "SELECT ts_open, ts_close FROM trades WHERE mode = 'paper'"
        ).fetchall()
        for ts_open, ts_close in rows:
            if datetime.fromisoformat(ts_open).astimezone(MSK).date().isoformat() == msk_date:
                return True
            if (
                ts_close is not None
                and datetime.fromisoformat(ts_close).astimezone(MSK).date().isoformat() == msk_date
            ):
                return True
        return False
