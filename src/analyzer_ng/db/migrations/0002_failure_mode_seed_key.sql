-- Seed-KB lazy per-project copies (spec 03 §9; spec 01 §6 step 6 "keyed by a
-- stable seed_id"). The seed catalog ships as package data (seed_modes.yaml);
-- a per-project failure_mode copy is created on first match. `seed_key` records
-- which catalog mode the row was copied from and makes that copy idempotent
-- under concurrent workers: the partial unique index is the ON CONFLICT arbiter,
-- so a re-match inserts nothing and reuses the existing row. NULL for
-- organically-discovered (non-seed) modes, which the partial index ignores.

ALTER TABLE failure_mode ADD COLUMN seed_key text;

CREATE UNIQUE INDEX fm_seed_key_uq
    ON failure_mode (project_id, seed_key)
    WHERE seed_key IS NOT NULL;
