-- 0005_paper_equity_anchors.sql — устойчивые якоря капитала для paper-счёта
-- (trading_bot/paper_account.py, risk_manager.AccountState.{starting_equity,
-- daily_start_balance}). Ручных ALTER TABLE в рантайме нет — только новый файл
-- миграции (docs/02_CODING_RULES.md «БД и код»).
--
-- anchor_type='starting': ровно одна строка (anchor_key='global') на всю жизнь
-- paper-счёта — капитал на момент самого первого снимка пустой paper БД.
-- anchor_type='daily': одна строка на торговый день по МСК (anchor_key —
-- дата ISO 'YYYY-MM-DD') — капитал на открытие этого дня, посчитанный по
-- проверенным ценам открытия для всех на тот момент открытых позиций.
--
-- Якорь не пересчитывается задним числом и не переписывается: если он отсутствует
-- для непустой истории сделок, trading_bot/paper_account.py отказывает явной
-- ошибкой вместо того, чтобы угадывать капитал (AGENTS.md §3 «не додумывай»).
CREATE TABLE IF NOT EXISTS paper_equity_anchors (
    anchor_type TEXT NOT NULL CHECK (anchor_type IN ('starting', 'daily')),
    anchor_key TEXT NOT NULL,
    equity TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (anchor_type, anchor_key)
);
