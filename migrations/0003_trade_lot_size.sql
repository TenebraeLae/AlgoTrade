-- Price in trades is per security; persist the number of securities in each lot.
-- Legacy rows have UNKNOWN lot sizes and incorrect historical cash for non-unit lots.
-- NULL intentionally blocks valuation of open legacy positions until manual reconciliation.
ALTER TABLE trades ADD COLUMN lot_size INTEGER CHECK (lot_size > 0);
