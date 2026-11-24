-- 0001_init.sql — начальная схема (docs/06_DB_SCHEMA.md). Применяется идемпотентно
-- через database.apply_migrations(); ручных ALTER TABLE в рантайме нет (AGENTS.md,
-- docs/02_CODING_RULES.md «БД и код»).

CREATE TABLE IF NOT EXISTS instruments (
    uid TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    name TEXT,
    sector TEXT,
    lot_size NUMERIC,
    min_price_increment TEXT,
    currency TEXT,
    last_seen_at TEXT,
    is_tradable INTEGER
);

CREATE TABLE IF NOT EXISTS candles_cache (
    ticker TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    ts_open TEXT NOT NULL,
    o TEXT NOT NULL,
    h TEXT NOT NULL,
    l TEXT NOT NULL,
    c TEXT NOT NULL,
    v REAL,
    PRIMARY KEY (ticker, timeframe, ts_open)
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    ticker TEXT NOT NULL,
    regime TEXT,
    side TEXT,
    indicators TEXT,
    filters_passed TEXT,
    filters_failed TEXT,
    size_lots NUMERIC,
    verdict TEXT,
    reason TEXT,
    mode TEXT
);

-- trades: одна строка = одна сделка (или FIFO-порция сделки), никогда не
-- перезаписывается целиком — только переход open -> closed на своей же строке
-- (docs/06_DB_SCHEMA.md, docs/02_CODING_RULES.md «БД и код»).
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY,
    journal_id INTEGER REFERENCES decisions (id),
    ticker TEXT NOT NULL,
    side TEXT NOT NULL,
    lots NUMERIC NOT NULL,
    price TEXT NOT NULL,
    fee TEXT NOT NULL,
    slippage TEXT NOT NULL,
    mode TEXT NOT NULL,
    ts_open TEXT NOT NULL,
    ts_close TEXT,
    exit_reason TEXT,
    pnl_gross TEXT,
    pnl_net TEXT,
    pnl_pct TEXT,
    broker_order_id TEXT,
    status TEXT NOT NULL,
    balance_after TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions (
    ticker TEXT PRIMARY KEY,
    side TEXT NOT NULL,
    lots NUMERIC NOT NULL,
    avg_entry TEXT NOT NULL,
    stop TEXT,
    take TEXT,
    trail_state TEXT,
    ts_open TEXT NOT NULL,
    mode TEXT NOT NULL,
    journal_id INTEGER,
    source TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS day_stats (
    date TEXT PRIMARY KEY,
    pnl TEXT,
    pnl_pct TEXT,
    trades INTEGER,
    wins INTEGER,
    losses INTEGER,
    max_dd TEXT,
    imoex_pct TEXT,
    alpha TEXT,
    stopped_by_limit INTEGER,
    balance_start TEXT,
    balance_end TEXT
);

CREATE TABLE IF NOT EXISTS optimizer_runs (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    level INTEGER NOT NULL,
    trigger TEXT,
    window_from TEXT,
    window_to TEXT,
    changes TEXT,
    metrics_before TEXT,
    metrics_after TEXT,
    status TEXT NOT NULL,
    backup_tag TEXT
);

CREATE TABLE IF NOT EXISTS proposals (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    params TEXT,
    sharpe_new TEXT,
    sharpe_old TEXT,
    forward_pnl TEXT,
    forward_winrate TEXT,
    status TEXT NOT NULL,
    decided_at TEXT
);

-- secrets_meta: только маска токена (t.xxxx…9f3a), никогда открытый токен
-- (docs/13_SECURITY.md). В этой сессии не используется — токен живёт в OS keyring
-- (security.py), таблица заведена для соответствия схеме на будущее.
CREATE TABLE IF NOT EXISTS secrets_meta (
    name TEXT PRIMARY KEY,
    cipher BLOB,
    kdf_salt BLOB,
    kdf_iters INTEGER,
    created_at TEXT,
    hint TEXT
);

CREATE TABLE IF NOT EXISTS event_log (
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    module TEXT NOT NULL,
    event TEXT NOT NULL,
    ctx TEXT,
    notify_vk INTEGER
);

CREATE INDEX IF NOT EXISTS idx_trades_ticker_status ON trades (ticker, status);
CREATE INDEX IF NOT EXISTS idx_trades_ts_close ON trades (ts_close);
