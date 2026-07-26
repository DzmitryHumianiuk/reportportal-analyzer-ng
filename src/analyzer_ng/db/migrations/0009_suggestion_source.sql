-- Early per-item auto-analysis (docs/EARLY-ITEM-AA.md): tag which route wrote a
-- suggestion row. The early route writes 'early'; every other writer leaves the
-- column NULL (no backfill — NULL means "launch-scoped or suggest route").
--
-- Two consumers: the training frame refuses 'early' snapshots (their launch
-- context is a degenerate group of one), and the early-vs-final pairs per item
-- give the flip-rate metric that gates any widening of the early label policy.
ALTER TABLE analyzer.suggestion ADD COLUMN IF NOT EXISTS source text;
