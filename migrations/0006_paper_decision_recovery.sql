-- 0006_paper_decision_recovery.sql — persistent paper bar claims and source episodes.
-- database.apply_migrations() wraps this script and schema_version insert in one
-- BEGIN IMMEDIATE transaction; do not put BEGIN/COMMIT in this file.
ALTER TABLE decisions ADD COLUMN source_bar_ts TEXT;
ALTER TABLE decisions ADD COLUMN processing_state TEXT NOT NULL DEFAULT 'complete'
    CHECK (processing_state IN ('complete', 'pending', 'failed'));

CREATE UNIQUE INDEX IF NOT EXISTS idx_decisions_paper_bar
    ON decisions (mode, ticker, source_bar_ts)
    WHERE mode = 'paper' AND source_bar_ts IS NOT NULL;

CREATE TABLE IF NOT EXISTS paper_source_episodes (
    id INTEGER PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('paper', 'live')),
    ticker TEXT NOT NULL,
    stage TEXT NOT NULL,
    error_type TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('skip', 'stop')),
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    attempt_count INTEGER NOT NULL CHECK (attempt_count > 0),
    first_source_bar_ts TEXT,
    last_source_bar_ts TEXT,
    indicators TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'recovered')),
    recovered_at TEXT,
    CHECK (
        (status = 'open' AND recovered_at IS NULL)
        OR (status = 'recovered' AND recovered_at IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_source_episode_open
    ON paper_source_episodes (mode, ticker, stage, error_type)
    WHERE status = 'open';
CREATE INDEX IF NOT EXISTS idx_paper_source_episode_ticker_status
    ON paper_source_episodes (mode, ticker, status);
