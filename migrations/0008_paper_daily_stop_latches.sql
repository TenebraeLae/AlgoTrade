-- Неизменяемые факты срабатывания дневного стопа paper по капиталу на стопах B.
-- Ключ — подтверждённый торговый день МСК; строки прошлых дней не удаляются.
-- Порог и B хранятся строками Decimal, не SQLite REAL.
CREATE TABLE paper_daily_stop_latches (
    trading_day TEXT NOT NULL PRIMARY KEY,
    triggered_at TEXT NOT NULL,
    capital_at_stops TEXT NOT NULL,
    threshold_equity TEXT NOT NULL
);
