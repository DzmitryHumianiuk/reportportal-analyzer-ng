-- suggestion.method + suggestion.abstain_reason — the decision provenance the
-- Inspector surfaces verbatim in a journey's Technical Details.
--
-- The analyzer already computes both on every decision (DecisionResult.method =
-- 'hash'|'kb'|'gbm'|'rule_cold'; DecisionResult.abstain_reason =
-- 'gbm_below_suggest'|'gbm_boilerplate_only_neighbor'|'no_confident_rule'|NULL),
-- but they were only ever folded into the features jsonb / model_ver string. Lifting
-- them to real columns lets the read path (and the Inspector, which runtime-detects
-- the columns) show HOW a decision was reached without parsing model_ver.
--
-- Both nullable and additive: rows written before this migration keep NULL (no
-- backfill — a NULL honestly means "not captured", never a guessed value). Cold-start
-- rubric inserts stamp method='coldstart'.
ALTER TABLE suggestion ADD COLUMN IF NOT EXISTS method text;
ALTER TABLE suggestion ADD COLUMN IF NOT EXISTS abstain_reason text;
