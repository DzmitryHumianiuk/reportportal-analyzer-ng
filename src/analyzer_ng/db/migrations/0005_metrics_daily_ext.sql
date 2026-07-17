-- metrics_daily §10.3 extension: auto-analysis + provenance columns (spec 03 §10.3).
--
-- The 0001 metrics_daily table carries suggestions/accepted/corrected/ignored/
-- abstained/per_label. §10.3 additionally requires the auto-analysis safety counters
-- and the active-model provenance per (project, day): how many suggestions were
-- auto-labeled, how many of those were later auto-corrected (a wrong auto-label a
-- user had to fix — the key safety metric), and which model / embedding version was
-- active. The nightly rollup (analyzer_ng.ml.reporting) populates these.
--
-- Version note: 0004 is reserved by the concurrent Phase-4 branch (llm_event /
-- llm_role_state) and is not yet on develop, so this extension is 0005; the runner
-- tolerates the reserved-version gap (see db/migrate.py) and closes it when 0004 lands.

ALTER TABLE metrics_daily
    ADD COLUMN IF NOT EXISTS auto_labeled   integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS auto_corrected integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS model_ver      text,
    ADD COLUMN IF NOT EXISTS emb_model_ver  text;
