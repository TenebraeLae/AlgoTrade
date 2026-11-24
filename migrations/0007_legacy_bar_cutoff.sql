-- 0007_legacy_bar_cutoff.sql — persistent cutoff for unkeyed paper history.
-- At this point, NULL source_bar_ts rows are decisions from before bar-key recovery;
-- the 0006 applied_at timestamp is the conservative cutoff boundary.
CREATE TABLE paper_recovery_meta (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    cutoff_at TEXT NOT NULL,
    has_legacy_paper_decisions INTEGER NOT NULL CHECK (has_legacy_paper_decisions IN (0, 1))
);

INSERT INTO paper_recovery_meta (singleton, cutoff_at, has_legacy_paper_decisions)
SELECT 1,
       (SELECT applied_at FROM schema_version WHERE version = 6),
       CASE WHEN EXISTS (
           SELECT 1 FROM decisions
           WHERE mode = 'paper' AND source_bar_ts IS NULL
       ) THEN 1 ELSE 0 END;
