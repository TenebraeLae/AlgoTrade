-- Effective planned stop loss as percent of equity at stops; NULL before risk evaluation.
ALTER TABLE decisions ADD COLUMN effective_risk_pct TEXT;
