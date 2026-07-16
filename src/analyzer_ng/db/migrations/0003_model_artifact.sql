-- Model artifact storage for the learning loop (spec 03 §6.5).
--
-- The install-wide LightGBM classifier and the per-project isotonic calibrators
-- are stored as opaque `bytea` blobs here — no filesystem: containers are
-- stateless, PostgreSQL is the only required store, and the artifacts are < 5 MB
-- (spec 03 §6.5 "Artifact storage: PG model_artifact table, bytea"). Every
-- retrain inserts a new versioned row; activation is an atomic swap guarded by a
-- partial unique index so at most one artifact of a given (kind, project) is live
-- and serving picks the latest shipped model concurrently-safely.

CREATE TABLE model_artifact (
    model_id           bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind               text         NOT NULL CHECK (kind IN ('gbm','calib')),
    project_id         bigint,                       -- NULL: install-wide (gbm, or install-wide calib)
    version            text         NOT NULL,        -- e.g. 'gbm-2026.07.16T02:00:00Z'
    feature_schema_ver integer      NOT NULL,        -- must match serving features.FEATURE_SCHEMA_VER
    n_events           integer      NOT NULL DEFAULT 0,   -- training-set size (audit)
    metrics            jsonb        NOT NULL DEFAULT '{}'::jsonb,
    blob               bytea        NOT NULL,        -- self-contained serialized model (§6.5)
    is_active          boolean      NOT NULL DEFAULT false,
    trained_at         timestamptz  NOT NULL DEFAULT now(),
    created_at         timestamptz  NOT NULL DEFAULT now()
);

-- At most one active artifact per (kind, project). COALESCE folds the install-wide
-- NULL project into a sentinel so the partial-unique predicate covers it too; the
-- swap (deactivate siblings + activate new) runs in one transaction, so a
-- concurrent activation serializes on this index instead of doubling actives.
CREATE UNIQUE INDEX model_artifact_active_uq
    ON model_artifact (kind, COALESCE(project_id, -1))
    WHERE is_active;

-- Cheap "what is live right now" lookup for the serving refresh check (§6.5).
CREATE INDEX model_artifact_active_lookup_idx
    ON model_artifact (kind, project_id, model_id DESC)
    WHERE is_active;
