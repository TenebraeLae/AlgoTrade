-- 0002_portfolio_balance.sql — устойчивый остаток портфеля на перезапуск.
--
-- docs/06_DB_SCHEMA.md не заводит отдельной таблицы под текущий баланс (он подразумевался
-- выводимым из `trades.balance_after`), но это ломает восстановление после рестарта
-- в сценарии без единой сделки в режиме (docs/15_ACCEPTANCE.md, M6: 20 сценариев рестарта,
-- в т.ч. пустые). Решение: отдельная таблица на mode, обновляется при каждой сделке и при
-- инициализации Portfolio (см. CHANGELOG_AI.md, запись M6). Ручных ALTER TABLE нет —
-- только новый файл миграции (docs/02_CODING_RULES.md «БД и код»).

CREATE TABLE IF NOT EXISTS portfolio_balances (
    mode TEXT PRIMARY KEY,
    balance TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
