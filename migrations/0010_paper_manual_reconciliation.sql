-- Offline, operator-attested completion of exactly one paper bar claim.
-- Applied atomically by database.apply_migrations(); no runtime schema repair.
CREATE TABLE paper_manual_reconciliations (
    id INTEGER PRIMARY KEY,
    decision_id INTEGER NOT NULL UNIQUE REFERENCES decisions(id),
    previous_state TEXT NOT NULL CHECK (previous_state IN ('pending', 'failed')),
    ts TEXT NOT NULL,
    operator TEXT NOT NULL CHECK (length(operator) BETWEEN 1 AND 32),
    fingerprint TEXT NOT NULL CHECK (length(fingerprint) = 64),
    confirmations TEXT NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('no_fill', 'accepted_existing_fill')),
    snapshot TEXT NOT NULL
);

-- Append-only evidence, not an editable recovery status or a trading API.
CREATE TRIGGER paper_manual_reconciliations_no_update
BEFORE UPDATE ON paper_manual_reconciliations
BEGIN
    SELECT RAISE(ABORT, 'manual reconciliation audit is append-only');
END;
CREATE TRIGGER paper_manual_reconciliations_no_delete
BEFORE DELETE ON paper_manual_reconciliations
BEGIN
    SELECT RAISE(ABORT, 'manual reconciliation audit is append-only');
END;
