-- Early per-item auto-analysis (docs/EARLY-ITEM-AA.md): tag which route wrote a
-- suggestion row. The early route writes 'early'; every other writer leaves the
-- column NULL (no backfill — NULL means "launch-scoped or suggest route").
--
-- Consumer today: the training frame refuses 'early' snapshots (their launch
-- context is a degenerate group of one). The early/final pairs per item also
-- enable a future on-traffic flip-rate comparison; until that exists, the
-- flip-rate gate is computed offline by tools/replay-early-aa/.
ALTER TABLE analyzer.suggestion ADD COLUMN IF NOT EXISTS source text;
