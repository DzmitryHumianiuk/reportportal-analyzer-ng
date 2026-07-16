-- analyzer-ng initial schema (spec 02 §2). Applied in one transaction by the
-- migration runner (spec 02 §3). DDL is verbatim from spec 02 §2; the 2x16 hash
-- partition + autovacuum blocks are emitted by db/gen_partitions.py (spec §2.4).
--
-- schema_migrations (§2.1) is created by the runner itself, not by this file.

-- §1.1 Required extensions (idempotent; also ensured by the bootstrap step).
CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector >= 0.8 (halfvec + iterative scans)
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- trigram fallback for exception-name matching

SET LOCAL search_path = analyzer, public;

-- §2.2 project ---------------------------------------------------------------
CREATE TABLE project (
    project_id  bigint       PRIMARY KEY,
    settings    jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    updated_at  timestamptz  NOT NULL DEFAULT now()
);

-- §2.3 Drain3 persistence -----------------------------------------------------
-- Opaque serialized Drain3 state, one row per project.
CREATE TABLE drain3_state (
    project_id     bigint       PRIMARY KEY REFERENCES project(project_id) ON DELETE CASCADE,
    state          bytea        NOT NULL,      -- zlib-compressed JSON (drain3 default codec)
    state_version  bigint       NOT NULL DEFAULT 0,  -- optimistic-concurrency counter
    drain3_config  jsonb        NOT NULL DEFAULT '{}'::jsonb, -- sim_th, depth, masking cfg used
    updated_at     timestamptz  NOT NULL DEFAULT now()
);

-- Queryable mirror of Drain3 clusters.
CREATE TABLE log_template (
    project_id   bigint       NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
    template_id  bigint       NOT NULL,          -- drain3 cluster_id, stable per project
    pattern      text         NOT NULL,          -- template with <*> wildcards
    token_count  integer      NOT NULL,
    example      text,                            -- one raw (masked) example line, capped 2 KB
    match_count  bigint       NOT NULL DEFAULT 0, -- examples seen
    first_seen   timestamptz  NOT NULL DEFAULT now(),
    last_seen    timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, template_id)
);
CREATE INDEX log_template_last_seen_idx ON log_template (project_id, last_seen);

-- §2.4 test_item (partitioned) ------------------------------------------------
CREATE TABLE test_item (
    project_id       bigint       NOT NULL,
    item_id          bigint       NOT NULL,
    launch_id        bigint       NOT NULL,
    launch_name      text         NOT NULL DEFAULT '',
    launch_number    integer,
    test_case_hash   bigint,                      -- RP testCaseHash (int32 today; bigint for safety)
    unique_id        text,                        -- RP uniqueId
    item_name        text         NOT NULL DEFAULT '',
    start_time       timestamptz,
    is_auto_analyzed boolean      NOT NULL DEFAULT false,
    issue_type       text,                        -- RP locator ('pb001', 'ab001', ...); NULL = unlabeled
    issue_type_group text GENERATED ALWAYS AS (substring(issue_type from '^[a-z]+')) STORED,
                                                  -- 'pb'|'ab'|'si'|'nd'|'ti' for group-level features
    log_count        integer      NOT NULL DEFAULT 0,
    log_time_max     timestamptz,                 -- newest log ts, for remove_by_log_time
    indexed_at       timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, item_id)
) PARTITION BY HASH (project_id);

CREATE TABLE test_item_p00 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 0);
CREATE TABLE test_item_p01 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 1);
CREATE TABLE test_item_p02 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 2);
CREATE TABLE test_item_p03 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 3);
CREATE TABLE test_item_p04 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 4);
CREATE TABLE test_item_p05 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 5);
CREATE TABLE test_item_p06 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 6);
CREATE TABLE test_item_p07 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 7);
CREATE TABLE test_item_p08 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 8);
CREATE TABLE test_item_p09 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 9);
CREATE TABLE test_item_p10 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 10);
CREATE TABLE test_item_p11 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 11);
CREATE TABLE test_item_p12 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 12);
CREATE TABLE test_item_p13 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 13);
CREATE TABLE test_item_p14 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 14);
CREATE TABLE test_item_p15 PARTITION OF test_item
    FOR VALUES WITH (MODULUS 16, REMAINDER 15);

CREATE INDEX test_item_launch_idx    ON test_item (project_id, launch_id);
CREATE INDEX test_item_tch_idx       ON test_item (project_id, test_case_hash) WHERE test_case_hash IS NOT NULL;
CREATE INDEX test_item_start_idx     ON test_item (project_id, start_time);
CREATE INDEX test_item_labeled_idx   ON test_item (project_id, issue_type) WHERE issue_type IS NOT NULL;

-- Autovacuum tuning for hot partitions (spec §6).
ALTER TABLE test_item_p00 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p01 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p02 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p03 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p04 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p05 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p06 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p07 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p08 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p09 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p10 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p11 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p12 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p13 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p14 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE test_item_p15 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

-- §2.5 failure_signature (partitioned, the retrieval core) --------------------
CREATE TABLE failure_signature (
    project_id     bigint       NOT NULL,
    item_id        bigint       NOT NULL,
    exception_fp   bigint       NOT NULL,          -- xxhash64(normalized exception chain)
    error_hash     bigint       NOT NULL,          -- xxhash64(exception_fp + top frames + ordered template_ids)
    top_frames     text[]       NOT NULL DEFAULT '{}',  -- normalized top-N in-app frames
    template_ids   bigint[]     NOT NULL DEFAULT '{}',  -- ordered Drain3 error-template ids
    -- Field-separated signature text for weighted FTS:
    exc_text       text         NOT NULL DEFAULT '',    -- exception class names, chain order
    msg_text       text         NOT NULL DEFAULT '',    -- salient message terms (masked)
    frames_text    text         NOT NULL DEFAULT '',    -- top frames as tokens
    tmpl_text      text         NOT NULL DEFAULT '',    -- concatenated template patterns
    signature_text text         GENERATED ALWAYS AS
        (exc_text || ' ' || msg_text || ' ' || frames_text) STORED,  -- display/debug doc
    signature_tsv  tsvector     GENERATED ALWAYS AS (
           setweight(to_tsvector('simple', left(exc_text,    2000)),  'A')
        || setweight(to_tsvector('simple', left(msg_text,    8000)),  'B')
        || setweight(to_tsvector('simple', left(frames_text, 4000)),  'C')
        || setweight(to_tsvector('simple', left(tmpl_text,  16000)),  'D')
    ) STORED,
    -- Extracted verbatim features (ported legacy text_processing outputs):
    only_numbers   text         NOT NULL DEFAULT '',    -- digit sequences from messages
    status_codes   text[]       NOT NULL DEFAULT '{}',
    urls           text[]       NOT NULL DEFAULT '{}',
    paths          text[]       NOT NULL DEFAULT '{}',
    emb            halfvec(384),                        -- NULL until embedded (async backfill ok)
    emb_model_ver  smallint     NOT NULL DEFAULT 0,     -- 0 = not embedded yet
    created_at     timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, item_id)
) PARTITION BY HASH (project_id);

CREATE TABLE failure_signature_p00 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 0);
CREATE TABLE failure_signature_p01 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 1);
CREATE TABLE failure_signature_p02 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 2);
CREATE TABLE failure_signature_p03 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 3);
CREATE TABLE failure_signature_p04 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 4);
CREATE TABLE failure_signature_p05 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 5);
CREATE TABLE failure_signature_p06 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 6);
CREATE TABLE failure_signature_p07 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 7);
CREATE TABLE failure_signature_p08 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 8);
CREATE TABLE failure_signature_p09 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 9);
CREATE TABLE failure_signature_p10 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 10);
CREATE TABLE failure_signature_p11 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 11);
CREATE TABLE failure_signature_p12 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 12);
CREATE TABLE failure_signature_p13 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 13);
CREATE TABLE failure_signature_p14 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 14);
CREATE TABLE failure_signature_p15 PARTITION OF failure_signature
    FOR VALUES WITH (MODULUS 16, REMAINDER 15);

CREATE INDEX fs_error_hash_idx   ON failure_signature (project_id, error_hash);
CREATE INDEX fs_exception_fp_idx ON failure_signature (project_id, exception_fp);
CREATE INDEX fs_tsv_idx          ON failure_signature USING gin (signature_tsv);
CREATE INDEX fs_exc_trgm_idx     ON failure_signature USING gin (exc_text gin_trgm_ops);
CREATE INDEX fs_templates_idx    ON failure_signature USING gin (template_ids);

-- Autovacuum tuning for hot partitions (spec §6).
ALTER TABLE failure_signature_p00 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p01 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p02 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p03 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p04 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p05 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p06 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p07 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p08 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p09 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p10 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p11 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p12 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p13 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p14 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
ALTER TABLE failure_signature_p15 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);

-- §2.6 failure_mode + mode_membership (the knowledge base) --------------------
CREATE TABLE failure_mode (
    project_id     bigint       NOT NULL REFERENCES project(project_id) ON DELETE CASCADE,
    mode_id        bigint       GENERATED ALWAYS AS IDENTITY,
    status         text         NOT NULL DEFAULT 'candidate'
                   CHECK (status IN ('seed','candidate','confirmed','retired')),
    label          text,                          -- issue-type locator; NULL while unlabeled candidate
    label_source   text CHECK (label_source IN ('seed','human','ai_suggested')),
    title          text,                          -- SLM-written, optional
    summary        text,                          -- SLM-written, optional
    purity         real         NOT NULL DEFAULT 0.0 CHECK (purity BETWEEN 0 AND 1),
    support        integer      NOT NULL DEFAULT 0,   -- member count contributing to purity
    centroid       halfvec(384),
    emb_model_ver  smallint,
    representative_template_ids bigint[] NOT NULL DEFAULT '{}',
    exception_fps  bigint[]     NOT NULL DEFAULT '{}',
    ewma_alpha     real         NOT NULL DEFAULT 0.1,  -- centroid/purity update smoothing
    ewma_hit_rate  real         NOT NULL DEFAULT 0.0,  -- recent match-rate signal for retirement
    created_at     timestamptz  NOT NULL DEFAULT now(),
    updated_at     timestamptz  NOT NULL DEFAULT now(),
    last_seen_at   timestamptz,
    PRIMARY KEY (project_id, mode_id)
);
CREATE INDEX fm_status_idx ON failure_mode (project_id, status);
CREATE INDEX fm_excfp_idx  ON failure_mode USING gin (exception_fps);

CREATE TABLE mode_membership (
    project_id  bigint       NOT NULL,
    mode_id     bigint       NOT NULL,
    item_id     bigint       NOT NULL,
    match_score real         NOT NULL,
    matched_by  text         NOT NULL CHECK (matched_by IN ('hash','vector','lexical','human')),
    created_at  timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, mode_id, item_id),
    FOREIGN KEY (project_id, mode_id)
        REFERENCES failure_mode(project_id, mode_id) ON DELETE CASCADE
);
CREATE INDEX mm_item_idx ON mode_membership (project_id, item_id);

-- §2.7 label_event (append-only training log) ---------------------------------
CREATE TABLE label_event (
    event_id      bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id    bigint       NOT NULL,
    item_id       bigint       NOT NULL,
    old_label     text,                           -- NULL when first labeling
    new_label     text         NOT NULL,
    source        text         NOT NULL CHECK (source IN
                    ('rp_defect_update','analyzer_suggestion_accepted','human_ui')),
    suggestion_id bigint,                          -- set when tied to a suggestion row
    ts            timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX le_project_ts_idx ON label_event (project_id, ts);
CREATE INDEX le_item_idx       ON label_event (project_id, item_id, ts);

-- §2.8 suggestion -------------------------------------------------------------
CREATE TABLE suggestion (
    suggestion_id   bigint       GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id      bigint       NOT NULL,
    item_id         bigint       NOT NULL,
    launch_id       bigint       NOT NULL,
    group_id        bigint,                        -- launch_group fan-out provenance
    predicted_label text         NOT NULL,         -- locator, or 'ti' when abstained
    confidence      real         NOT NULL,         -- calibrated probability
    matched_mode_id bigint,
    matched_item_id bigint,                        -- relevantItem in RP replies
    features        jsonb        NOT NULL DEFAULT '{}'::jsonb,  -- full GBM feature vector (audit/train debug)
    model_ver       text         NOT NULL,         -- 'gbm-2026.07.01+cal-p123', stamped always
    llm_used        boolean      NOT NULL DEFAULT false,
    explanation     text,                          -- SLM explainer output, async-filled
    outcome         text         NOT NULL DEFAULT 'pending'
                    CHECK (outcome IN ('pending','accepted','corrected','ignored')),
    outcome_ts      timestamptz,
    created_at      timestamptz  NOT NULL DEFAULT now()
);
CREATE INDEX sg_item_idx   ON suggestion (project_id, item_id, created_at DESC);
CREATE INDEX sg_launch_idx ON suggestion (project_id, launch_id);
CREATE INDEX sg_pending_idx ON suggestion (project_id, created_at) WHERE outcome = 'pending';

-- §2.9 launch_group -----------------------------------------------------------
CREATE TABLE launch_group (
    project_id   bigint       NOT NULL,
    group_id     bigint       GENERATED ALWAYS AS IDENTITY,
    launch_id    bigint       NOT NULL,
    fingerprint  bigint       NOT NULL,            -- group signature hash (= dominant error_hash)
    member_count integer      NOT NULL DEFAULT 0,
    dominant     boolean      NOT NULL DEFAULT false, -- covers > burst_si_share of launch failures
    si_prior     real         NOT NULL DEFAULT 0.0,
    created_at   timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, group_id)
);
CREATE UNIQUE INDEX lg_launch_fp_idx ON launch_group (project_id, launch_id, fingerprint);

-- §2.10 test_history_stats ----------------------------------------------------
CREATE TABLE test_history_stats (
    project_id      bigint       NOT NULL,
    test_case_hash  bigint       NOT NULL,
    window_runs     integer      NOT NULL DEFAULT 0,   -- failures seen by analyzer (proxy for runs)
    window_failures integer      NOT NULL DEFAULT 0,
    window_flips    integer      NOT NULL DEFAULT 0,   -- pass->fail->pass transitions observed
    last_status     text,                              -- 'failed'|'passed' last observed
    last_failure_ts timestamptz,
    flakiness_score real         NOT NULL DEFAULT 0.0, -- EWMA of flip indicator, alpha=0.1
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, test_case_hash)
);

-- §2.11 llm_cache -------------------------------------------------------------
CREATE TABLE llm_cache (
    project_id  bigint       NOT NULL,      -- tenancy isolation: never served cross-project
    cache_key   char(64)     NOT NULL,      -- sha256(model || role || prompt_hash)
    role        text         NOT NULL CHECK (role IN ('explainer','extractor','judge','coldstart')),
    template_hash bigint,                   -- extractor: Drain3 template-set hash the output applies to
    output      jsonb        NOT NULL,
    model       text         NOT NULL,      -- 'qwen3:4b' etc, exact tag
    hits        integer      NOT NULL DEFAULT 0,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    last_hit_at timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, cache_key)
);
CREATE INDEX llmc_tmpl_idx ON llm_cache (project_id, role, template_hash);

-- §2.12 metrics_daily ---------------------------------------------------------
CREATE TABLE metrics_daily (
    project_id  bigint  NOT NULL,
    day         date    NOT NULL,
    suggestions integer NOT NULL DEFAULT 0,
    accepted    integer NOT NULL DEFAULT 0,
    corrected   integer NOT NULL DEFAULT 0,
    ignored     integer NOT NULL DEFAULT 0,
    abstained   integer NOT NULL DEFAULT 0,
    per_label   jsonb   NOT NULL DEFAULT '{}'::jsonb,  -- {"pb": {"suggested": n, "accepted": n, "corrected": n}, ...}
    PRIMARY KEY (project_id, day)
);

-- §2.13 Helper function -------------------------------------------------------
CREATE OR REPLACE FUNCTION analyzer.array_jaccard(a bigint[], b bigint[])
RETURNS real LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN a IS NULL OR b IS NULL OR (cardinality(a) = 0 AND cardinality(b) = 0) THEN 0.0
        ELSE (SELECT count(*) FROM (SELECT unnest(a) INTERSECT SELECT unnest(b)) i)::real
           / (SELECT count(*) FROM (SELECT unnest(a) UNION SELECT unnest(b)) u)::real
    END;
$$;
