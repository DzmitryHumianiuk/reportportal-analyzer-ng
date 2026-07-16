# Spec 02 — Database: PostgreSQL Schema, Migrations, RetrievalStore Contract

Status: **implementation-grade**. Authoritative context: `../CONTEXT.md` (do not relitigate
decisions restated here). Target: PostgreSQL **16+** with **pgvector >= 0.8**. All analyzer
state lives in the **`analyzer` schema** of its own logical database (default DB name
`analyzer`), never in ReportPortal's main tables.

---

## 0. Conventions (binding)

- All DDL lives in ordered SQL files under `db/migrations/` (see §3). The DDL in §2 is the
  literal content of `0001_init.sql` split into annotated blocks.
- Every table lives in schema `analyzer`. Application sets
  `search_path = analyzer, public` per connection (`options=-csearch_path%3Danalyzer,public`
  in the DSN or `SET search_path` on pool checkout). `public` stays in the path because
  pgvector installs its types/operators there.
- **IDs**: `project_id`, `item_id`, `launch_id` are `bigint` (ReportPortal numeric IDs
  arriving over AMQP). 64-bit content hashes (`error_hash`, `exception_fp`, `fingerprint`,
  `template_hash`) are xxhash64 values stored as **signed `bigint`** (cast the uint64 with
  wraparound: `struct.unpack('q', struct.pack('Q', h))[0]`).
- **Enums**: modeled as `text` + `CHECK` constraints, **not** native PG enum types.
  Rationale: `ALTER TYPE ... ADD VALUE` cannot run inside a transaction, which would break
  the transactional migration runner (§3). CHECK constraints evolve with a plain
  transactional `ALTER TABLE`.
- **Timestamps**: always `timestamptz`, always UTC.
- **Vectors**: `halfvec(384)` (see §2.5 justification). Every vector column is paired with
  `emb_model_ver smallint`; similarity queries MUST filter on it (CONTEXT §3: never mix
  versions in one similarity computation).
- **Multi-tenancy**: every query carries `project_id`. Hot large tables are
  **hash-partitioned by `project_id`, 16 partitions** by default (§6 for guidance).
  Partitioned tables include `project_id` in the PK (PG requirement).
- Foreign keys: used between small/metadata tables. **No FKs onto partitioned hot tables**
  (`test_item`, `failure_signature`) — ingestion order over AMQP is not guaranteed and FK
  validation on partitioned targets costs more than it protects. Referential hygiene is
  enforced by the repository layer and by cascading delete methods (§4).

---

## 1. Extensions & bootstrap

### 1.1 Required extensions

```sql
CREATE EXTENSION IF NOT EXISTS vector;    -- pgvector >= 0.8 (halfvec + iterative scans)
CREATE EXTENSION IF NOT EXISTS pg_trgm;   -- trigram fallback for exception-name matching
```

Both extensions are marked `trusted` in their control files, so the **database owner** can
create them without superuser. They are created in `0001_init.sql` (first statements).

**Fail-fast when pgvector is missing.** Before running migrations the bootstrap executes:

```sql
SELECT name, default_version, installed_version
FROM pg_available_extensions WHERE name IN ('vector', 'pg_trgm');
```

- If `vector` is absent from `pg_available_extensions` → **exit code 3** with:
  `FATAL: pgvector is not installed on this PostgreSQL server. analyzer-ng requires the
  'vector' extension (>= 0.8). Use the pgvector/pgvector:pg16 image or install the
  postgresql-16-pgvector package, then restart.`
- If installed version parses to `< 0.8.0` → same exit code, message names the found
  version and the minimum.
- If `server_version_num < 160000` → exit code 3, "PostgreSQL 16+ required".

No retry loop for these conditions — they are operator errors, and crash-looping with a
clear message is the correct docker-compose behavior. (Transient connection failures DO
retry: 30 attempts, 2 s apart, then exit 4.)

### 1.2 Database / schema creation at first container start

Configuration (CONTEXT §4): `ANALYZER_PG_DSN` (full DSN) or discrete
`ANALYZER_PG_HOST/PORT/USER/PASSWORD/DB`; `ANALYZER_PG_SCHEMA` (default `analyzer`).

Bootstrap sequence (runs in the container entrypoint before AMQP consumers start):

1. Connect to the DSN's target database. If that fails with `3D000` (database does not
   exist) **and** `ANALYZER_PG_CREATE_DB=true` (default `true`), connect to the maintenance
   DB (`postgres`) with the same credentials and run
   `CREATE DATABASE analyzer` (name from DSN). Requires `CREATEDB` or superuser; if the
   role lacks it → exit 3 with instructions (least-privilege path below).
2. `CREATE SCHEMA IF NOT EXISTS analyzer AUTHORIZATION CURRENT_USER;`
3. `CREATE EXTENSION` block (§1.1) — idempotent.
4. Run the migration runner (§3).

All steps are idempotent; a container restart at any point re-converges.

### 1.3 Least-privilege role option

For operators who won't hand the service CREATEDB, ship
`db/bootstrap/create_role_and_db.sql`, to be run **once by a DBA**:

```sql
-- Run as a superuser / DBA. Password supplied via psql -v svc_password=...
CREATE ROLE analyzer_svc LOGIN PASSWORD :'svc_password' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE analyzer OWNER analyzer_svc;
\connect analyzer
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;
-- The service owns its schema and nothing else:
CREATE SCHEMA analyzer AUTHORIZATION analyzer_svc;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO analyzer_svc;  -- pgvector types live here
```

The service then starts with `ANALYZER_PG_CREATE_DB=false` and only ever touches schema
`analyzer`.

---

## 2. Full DDL (`db/migrations/0001_init.sql`)

All statements below run inside one transaction (§3). `SET LOCAL search_path =
analyzer, public;` is the first statement of the file after the extension block.

### 2.1 `schema_migrations`

Created by the runner itself (not by a migration file), shown here for completeness:

```sql
CREATE TABLE IF NOT EXISTS analyzer.schema_migrations (
    version     integer      PRIMARY KEY,
    filename    text         NOT NULL,
    checksum    char(64)     NOT NULL,           -- sha256 hex of file bytes (LF-normalized)
    applied_at  timestamptz  NOT NULL DEFAULT now(),
    duration_ms integer      NOT NULL DEFAULT 0
);
```

### 2.2 `project`

```sql
CREATE TABLE project (
    project_id  bigint       PRIMARY KEY,
    settings    jsonb        NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz  NOT NULL DEFAULT now(),
    updated_at  timestamptz  NOT NULL DEFAULT now()
);
```

`settings` documented keys (defaults applied in code when absent; jsonb is the override):

| key | type | default | meaning |
|---|---|---|---|
| `accept_threshold` | float | 0.65 | GBM calibrated prob below which we abstain → `ti` |
| `judge_band` | [float,float] | [0.40, 0.65] | LLM-judge gating band (max-prob) |
| `burst_si_share` | float | 0.5 | dominant-fingerprint launch share for `si` prior |
| `llm_enabled` | bool | false | mirrors `ANALYZER_LLM_ENABLED`, per-project override |
| `llm_roles` | list[str] | ["explainer"] | enabled subset of explainer/extractor/judge/coldstart |
| `auto_analyze_enabled` | bool | true | |
| `min_should_match` | int | 80 | legacy-compat suggest field |
| `retention_days` | int | 365 | test_item/failure_signature TTL (§5) |

Rows are auto-created (`INSERT ... ON CONFLICT DO NOTHING`) on first message for a project.

### 2.3 Drain3 persistence — `drain3_state` + `log_template`

Drain3 supports pluggable persistence via its `PersistenceHandler` interface
(`save_state(state: bytes)` / `load_state() -> bytes`). We implement
`PostgresPersistenceHandler(project_id, store)` — **one Drain3 instance per project**,
serialized state stored as a single row, plus queryable per-template rows maintained by the
ingest pipeline (Drain3's state blob is opaque; templates are mirrored explicitly).

```sql
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
```

**Persistence mapping.** `save_state` executes:

```sql
INSERT INTO drain3_state (project_id, state, state_version, drain3_config)
VALUES ($1, $2, 1, $3)
ON CONFLICT (project_id) DO UPDATE
SET state = EXCLUDED.state,
    state_version = drain3_state.state_version + 1,
    updated_at = now()
WHERE drain3_state.state_version = $4;   -- expected version read at load_state
```

Zero rows updated ⇒ another worker persisted first ⇒ reload state, replay the batch of new
log lines through the fresh miner, save again (bounded retry ×3, then hard error). Snapshot
cadence: Drain3 `snapshot_interval_minutes=1` **and** forced save at the end of every
`index` batch. Template mirror rows are upserted in the same transaction as the state blob:
after mining a batch, for each touched cluster:
`INSERT ... ON CONFLICT (project_id, template_id) DO UPDATE SET pattern = EXCLUDED.pattern,
match_count = log_template.match_count + EXCLUDED.match_count, last_seen = now()`.
(Drain3 may rewrite a cluster's pattern as it generalizes — the mirror takes the newest.)

### 2.4 `test_item` — partitioned

```sql
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
-- 16 partitions:
--   CREATE TABLE test_item_p00 PARTITION OF test_item
--     FOR VALUES WITH (MODULUS 16, REMAINDER 0);   ... through _p15 / REMAINDER 15
-- (0001_init.sql contains all 16 statements verbatim; generator script db/gen_partitions.py
--  emits them for both partitioned tables to avoid copy errors.)

CREATE INDEX test_item_launch_idx    ON test_item (project_id, launch_id);
CREATE INDEX test_item_tch_idx       ON test_item (project_id, test_case_hash) WHERE test_case_hash IS NOT NULL;
CREATE INDEX test_item_start_idx     ON test_item (project_id, start_time);
CREATE INDEX test_item_labeled_idx   ON test_item (project_id, issue_type) WHERE issue_type IS NOT NULL;
```

Semantics: a row per **failed test item received via `index`**. `issue_type` mirrors RP's
current defect locator, updated by `defect_update` messages and by accepted suggestions.
`issue_type IS NULL OR issue_type_group = 'ti'` ⇒ not usable as retrieval evidence.

### 2.5 `failure_signature` — partitioned, the retrieval core

**Vector type choice — `halfvec(384)` (decided here, per CONTEXT latitude):** the encoder is
a 384-dim small multilingual model. `halfvec` stores fp16: 768 B/vector vs 1 536 B for
`vector`, halving both heap and HNSW index size (the index must live in RAM for latency,
§7). pgvector >= 0.7 supports `halfvec_cosine_ops` for HNSW natively. Published pgvector
benchmarks and e5-family results show < 0.5 % recall@10 loss from fp16 at 384 dims —
irrelevant next to RRF fusion noise. `vector(384)` would only be needed for exotic
distance ops we don't use. **All vector columns in this schema are `halfvec(384)`.**

```sql
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
    signature_text text GENERATED ALWAYS AS
        (exc_text || ' ' || msg_text || ' ' || frames_text) STORED,  -- display/debug doc
    signature_tsv  tsvector GENERATED ALWAYS AS (
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
-- 16 partitions failure_signature_p00 .. _p15, as in §2.4.

CREATE INDEX fs_error_hash_idx   ON failure_signature (project_id, error_hash);
CREATE INDEX fs_exception_fp_idx ON failure_signature (project_id, exception_fp);
CREATE INDEX fs_tsv_idx          ON failure_signature USING gin (signature_tsv);
CREATE INDEX fs_exc_trgm_idx     ON failure_signature USING gin (exc_text gin_trgm_ops);
CREATE INDEX fs_templates_idx    ON failure_signature USING gin (template_ids);
```

**HNSW strategy (decided):** the initial migration creates **no** HNSW index. Vector
queries run as exact scans (correct, and fast under partition pruning while partitions are
small). A maintenance job (hourly, and after every `index` batch that inserted > 5 000
rows) evaluates per partition:

```sql
SELECT c.relname, c.reltuples::bigint
FROM pg_class c JOIN pg_inherits i ON i.inhrelid = c.oid
WHERE i.inhparent = 'analyzer.failure_signature'::regclass;
```

For each partition with `reltuples > 50_000` (`ANALYZER_HNSW_ROW_THRESHOLD`) lacking an
HNSW index, it runs **outside any transaction**:

```sql
CREATE INDEX CONCURRENTLY IF NOT EXISTS <part>_emb_hnsw
ON analyzer.<part> USING hnsw (emb halfvec_cosine_ops)
WITH (m = 16, ef_construction = 96);
```

Query-time: `SET LOCAL hnsw.ef_search = 80; SET LOCAL hnsw.iterative_scan = relaxed_order;`
(pgvector 0.8 iterative scans make the post-filter on `issue_type`/`emb_model_ver` safe —
the scan keeps pulling until LIMIT is satisfied). Below the threshold the planner's exact
scan is both exact and cheap; this is the CONTEXT §3 rule made operational.

### 2.6 `failure_mode` + `mode_membership` — the knowledge base

Hundreds of rows per project (hot search space) — **not partitioned**, plain tables.

```sql
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
-- Mode count is O(hundreds)/project: centroid matching is an exact scan; no HNSW ever.

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
```

The **seed KB** (~40 generic modes, CONTEXT §2.4, catalog defined in spec 03) is inserted
by the application on project-row creation with `status='seed'`, `label_source='seed'`,
centroids embedded at startup with the current model.

### 2.7 `label_event` — append-only training log

```sql
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
```

**Never UPDATE or DELETE** (except project deletion). `defect_update` AMQP messages append
here (CONTEXT §4 — this is the feedback signal); the current label additionally overwrites
`test_item.issue_type`.

### 2.8 `suggestion`

```sql
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
```

The `suggest` AMQP handler **reads precomputed rows** from here (CONTEXT §2.9); analysis
workers write them.

### 2.9 `launch_group`

```sql
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
```

Membership is recorded on `suggestion.group_id` (one suggestion per member item), avoiding
a separate junction table.

### 2.10 `test_history_stats`

```sql
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
```

**Incremental update on `index`** (single upsert per item, same transaction as
`test_item`):

```sql
INSERT INTO test_history_stats AS s
    (project_id, test_case_hash, window_runs, window_failures, window_flips,
     last_status, last_failure_ts, flakiness_score)
VALUES ($1, $2, 1, 1, 0, 'failed', $3, 0.0)
ON CONFLICT (project_id, test_case_hash) DO UPDATE SET
    window_runs     = s.window_runs + 1,
    window_failures = s.window_failures + 1,
    window_flips    = s.window_flips + CASE WHEN s.last_status = 'passed' THEN 1 ELSE 0 END,
    flakiness_score = s.flakiness_score * (1 - 0.1)
                      + 0.1 * CASE WHEN s.last_status = 'passed' THEN 1 ELSE 0 END,
    last_status     = 'failed',
    last_failure_ts = $3,
    updated_at      = now();
```

A `fail -> pass` flip (completing the flip pair) is recorded when a previously-failed item
appears with a passing status via `defect_update`/re-index signals; the same upsert with
`last_status='passed'` and no failure-count bump. Window truncation: a weekly job halves
all counters (`window_* = window_* / 2`) — cheap exponential windowing without per-run rows.

### 2.11 `llm_cache`

```sql
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
```

`project_id` inside the PK enforces CONTEXT §6 "no cross-project leakage including LLM
prompt caches" at the schema level.

### 2.12 `metrics_daily`

```sql
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
```

Updated via `INSERT ... ON CONFLICT DO UPDATE SET col = metrics_daily.col + 1, per_label =
jsonb_set(...)` at suggestion-write and outcome-record time (no batch job needed).

### 2.13 Helper function

```sql
CREATE OR REPLACE FUNCTION analyzer.array_jaccard(a bigint[], b bigint[])
RETURNS real LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
    SELECT CASE
        WHEN a IS NULL OR b IS NULL OR (cardinality(a) = 0 AND cardinality(b) = 0) THEN 0.0
        ELSE (SELECT count(*) FROM (SELECT unnest(a) INTERSECT SELECT unnest(b)) i)::real
           / (SELECT count(*) FROM (SELECT unnest(a) UNION SELECT unnest(b)) u)::real
    END;
$$;
```

Used only over <= 100 fused candidates per query — cost is negligible.

---

## 3. Migration runner

### 3.1 Layout

```
db/
  migrations/
    0001_init.sql
    0002_<short_name>.sql      # future
  bootstrap/
    create_role_and_db.sql
  gen_partitions.py            # dev-time generator for the 2×16 partition DDL blocks
```

- Version = leading zero-padded integer of the filename; strictly increasing, **no gaps**
  (runner errors on a gap — prevents merge accidents).
- Files are **immutable once released**: checksum (sha256 over bytes with CRLF→LF
  normalization) is recorded and re-verified on every start; mismatch ⇒ exit 5 with the
  offending filename ("migration file changed after being applied — restore it or write a
  new migration").

### 3.2 Algorithm (exact)

```
ADVISORY_LOCK_KEY = 0x616E7A6D69677230  # ascii 'anzmigr0' as int64, project-wide constant

1. conn = connect(dsn); ensure_schema_and_extensions()          # §1
2. SELECT pg_advisory_lock(ADVISORY_LOCK_KEY)                    # session-level, blocks
3. CREATE TABLE IF NOT EXISTS analyzer.schema_migrations (...)   # §2.1
4. applied = SELECT version, checksum FROM schema_migrations ORDER BY version
5. verify: every applied version has a file with matching checksum → else exit 5
6. pending = files with version > max(applied), sorted
7. for each pending file:
     if header contains '-- analyzer:no-transaction':
         run statements one by one, autocommit          # CREATE INDEX CONCURRENTLY etc.
     else:
         BEGIN;
         SET LOCAL search_path = analyzer, public;
         execute file (multi-statement);
         INSERT INTO schema_migrations(version, filename, checksum, duration_ms) VALUES (...);
         COMMIT;
     on error: ROLLBACK, log statement + position, exit 5
8. SELECT pg_advisory_unlock(ADVISORY_LOCK_KEY)
9. proceed to serve
```

`pg_advisory_lock` (blocking, session-scoped) serializes **concurrent container starts**:
the second container blocks at step 2, then finds nothing pending at step 6 and proceeds.
Crash safety: session locks release automatically on disconnect. The lock is taken on a
dedicated connection kept open for the runner's duration only.

### 3.3 Adding migrations later

Next release ships `0002_*.sql`; the runner applies it on first start of the new image.
Rules: additive changes preferred (new columns `NULL`-able or defaulted, new tables/
indexes); destructive changes require a two-release deprecation (release N stops writing,
N+1 drops). `no-transaction` files must be **statement-level idempotent**
(`IF NOT EXISTS` everywhere) because a crash mid-file re-runs the whole file.

---

## 4. Repository contracts (Python)

Location: `analyzer/store/`. Async, psycopg3 (`AsyncConnectionPool`), pydantic v2 models in
`analyzer/store/models.py`. Protocols are structural (`typing.Protocol`) so tests can use
in-memory fakes; the single production implementation is `PgStore` implementing all four.

### 4.1 Models

```python
from datetime import datetime
from typing import Literal, Optional, Sequence
from pydantic import BaseModel

class TestItemIn(BaseModel):
    item_id: int; project_id: int; launch_id: int
    launch_name: str = ""; launch_number: int | None = None
    test_case_hash: int | None = None; unique_id: str | None = None
    item_name: str = ""; start_time: datetime | None = None
    is_auto_analyzed: bool = False; issue_type: str | None = None
    log_count: int = 0; log_time_max: datetime | None = None

class SignatureIn(BaseModel):
    project_id: int; item_id: int
    exception_fp: int; error_hash: int
    top_frames: list[str] = []; template_ids: list[int] = []
    exc_text: str = ""; msg_text: str = ""; frames_text: str = ""; tmpl_text: str = ""
    only_numbers: str = ""; status_codes: list[str] = []
    urls: list[str] = []; paths: list[str] = []
    emb: list[float] | None = None          # 384 floats; None => embed asynchronously
    emb_model_ver: int = 0

class QuerySignature(BaseModel):
    """Search-side view of a signature (built from the item under analysis)."""
    exception_fp: int; error_hash: int
    top_frames: list[str]; template_ids: list[int]
    salient_terms: list[str]                 # for websearch_to_tsquery construction
    exception_names: list[str]               # for trgm fallback
    emb: list[float] | None; emb_model_ver: int
    test_case_hash: int | None = None; launch_id: int | None = None
    launch_number: int | None = None

class CandidateFilters(BaseModel):
    exclude_item_ids: list[int] = []         # e.g. the query item itself / same launch
    exclude_launch_ids: list[int] = []
    min_label_ts: datetime | None = None
    issue_type_groups: list[str] | None = None   # restrict evidence, rarely used

MatchedBy = Literal["hash", "vector", "lexical", "human"]

class Candidate(BaseModel):
    # identity
    item_id: int | None                # None for mode-only candidates
    mode_id: int | None                # set for stage-A KB matches (and stage-B via membership)
    # retrieval scores (CONTEXT §4-consilium field list)
    dense_rank: int | None; sparse_rank: int | None
    rrf_score: float
    cosine: float | None; lex_score: float | None
    jaccard_templates: float
    # label evidence
    issue_type: str | None
    label_source: Literal["seed", "human", "ai_suggested", "rp"] | None
    label_ts: datetime | None
    # cheap boolean/context features
    same_test_case: bool = False
    same_error_hash: bool = False
    same_exception_fp: bool = False
    launch_distance: int | None = None     # |query.launch_number - cand.launch_number|
    # KB-mode extras (stage A)
    mode_status: str | None = None
    mode_purity: float | None = None
    mode_support: int | None = None
    matched_by: MatchedBy | None = None
    # provenance for RP reply compatibility
    relevant_log_id: int | None = None

class ModeIn(BaseModel):
    project_id: int; status: str = "candidate"
    label: str | None = None; label_source: str | None = None
    centroid: list[float] | None = None; emb_model_ver: int | None = None
    representative_template_ids: list[int] = []; exception_fps: list[int] = []
    title: str | None = None; summary: str | None = None

class LabelEventIn(BaseModel):
    project_id: int; item_id: int
    old_label: str | None; new_label: str
    source: Literal["rp_defect_update", "analyzer_suggestion_accepted", "human_ui"]
    suggestion_id: int | None = None
```

### 4.2 `RetrievalStore`

```python
class RetrievalStore(Protocol):
    async def upsert_items(self, items: Sequence[TestItemIn]) -> int: ...
        # INSERT ... ON CONFLICT (project_id, item_id) DO UPDATE (all mutable cols).
        # Returns rows written. Batched with executemany/COPY for >100 rows.

    async def upsert_signatures(self, sigs: Sequence[SignatureIn]) -> int: ...

    async def update_issue_type(self, project_id: int, item_id: int,
                                issue_type: str | None, is_auto: bool) -> bool: ...

    async def delete_items(self, project_id: int, item_ids: Sequence[int]) -> int: ...
        # 'clean' / 'item_remove'. Deletes test_item + failure_signature + mode_membership
        # + suggestion rows for those items, one transaction. label_event is retained.

    async def delete_launches(self, project_id: int, launch_ids: Sequence[int]) -> int: ...
    async def delete_project(self, project_id: int) -> None: ...
        # 'delete' message: removes ALL rows for the project incl. drain3 state, KB,
        # label events, caches. project row last.
    async def delete_by_time_range(self, project_id: int,
                                   field: Literal["start_time", "log_time"],
                                   before: datetime) -> int: ...
        # remove_by_launch_start_time / remove_by_log_time (uses log_time_max).

    async def find_by_error_hash(self, project_id: int, error_hash: int,
                                 limit: int = 5) -> list[Candidate]: ...
        # The exact fast path (paradigm step 2/5): labeled items only, newest first.

    async def find_candidates(self, project_id: int, q: QuerySignature,
                              k: int = 20,
                              filters: CandidateFilters | None = None) -> list[Candidate]: ...
```

**`find_candidates` internals (normative):**

1. **Stage A — KB modes** (`KBStore.match_modes`, §4.3): exact `exception_fp` overlap and
   centroid cosine over `failure_mode` (status != 'retired', matching `emb_model_ver`).
   Returned as `Candidate(mode_id=..., item_id=None, matched_by='hash'|'vector')`.
2. **Stage B — labeled item history**: the hybrid SQL of §5.1 (one round-trip): lexical
   top-50 + dense top-50, RRF k=60 fused in SQL, top-`k` returned with both ranks,
   `jaccard_templates` computed via `array_jaccard`, join to `test_item` for
   `issue_type/test_case_hash/launch_number`, join to latest `label_event` for
   `label_source/label_ts`, join to `mode_membership` for `mode_id`.
3. Python fills derived booleans (`same_test_case`, `same_error_hash`,
   `same_exception_fp`, `launch_distance`) and applies `filters.exclude_*`. Time-decay and
   label-confidence **weighting happens in the decision layer, not here** (CONTEXT §2.5).
4. Result: stage-A candidates first, then stage-B, combined list ≤ `k + 10` entries.

### 4.3 `KBStore`

```python
class KBStore(Protocol):
    async def match_modes(self, project_id: int, q: QuerySignature,
                          k: int = 10) -> list[Candidate]: ...      # §5.2 query
    async def spawn_candidate_mode(self, mode: ModeIn,
                                   seed_item_ids: Sequence[int]) -> int: ...
        # INSERT failure_mode (status='candidate') + mode_membership rows; returns mode_id.
    async def add_members(self, project_id: int, mode_id: int,
                          members: Sequence[tuple[int, float, MatchedBy]]) -> int: ...
    async def update_purity(self, project_id: int, mode_id: int) -> float: ...
        # Recompute from members' current test_item.issue_type:
        #   purity = max(label_count) / labeled_members ; support = labeled_members.
        # Also EWMA-updates centroid toward new members' mean embedding (ewma_alpha).
    async def merge_modes(self, project_id: int, src_mode_id: int,
                          dst_mode_id: int) -> None: ...
        # Move memberships, union fps/templates, support-weighted centroid avg, retire src.
    async def split_mode(self, project_id: int, mode_id: int,
                         partition: dict[int, list[int]]) -> list[int]: ...
        # partition: {new_mode_ordinal: [item_ids]}; spawns candidates, retires original.
    async def set_status(self, project_id: int, mode_id: int, status: str,
                         label: str | None = None, label_source: str | None = None) -> None: ...
    async def list_modes(self, project_id: int,
                         statuses: Sequence[str] | None = None) -> list[dict]: ...
```

### 4.4 `LabelStore` and `StatsStore`

```python
class LabelStore(Protocol):
    async def append_event(self, ev: LabelEventIn) -> int: ...   # returns event_id
    async def fetch_training_frame(self, project_id: int | None = None,
                                   since: datetime | None = None,
                                   limit: int = 500_000) -> list[dict]: ...
        # Joined rows for GBM training: label_event ⋈ suggestion.features ⋈
        # test_history_stats ⋈ mode purity/support at event time. project_id=None ⇒
        # install-wide (CONTEXT: install-wide model, per-project calibration).

class StatsStore(Protocol):
    async def bump_test_history(self, project_id: int, test_case_hash: int,
                                failed: bool, ts: datetime) -> None: ...   # §2.10 upsert
    async def get_test_history(self, project_id: int,
                               test_case_hashes: Sequence[int]) -> dict[int, dict]: ...
    async def bump_metrics(self, project_id: int, day: date, *,
                           suggestions: int = 0, accepted: int = 0, corrected: int = 0,
                           ignored: int = 0, abstained: int = 0,
                           label: str | None = None) -> None: ...
    async def get_metrics(self, project_id: int, frm: date, to: date) -> list[dict]: ...

class Drain3StateStore(Protocol):
    async def load(self, project_id: int) -> tuple[bytes, int] | None: ...   # (state, version)
    async def save(self, project_id: int, state: bytes, expected_version: int,
                   config: dict) -> bool: ...                                # False = CAS conflict
    async def upsert_templates(self, project_id: int, templates: Sequence[dict]) -> int: ...

class LlmCacheStore(Protocol):
    async def get(self, project_id: int, cache_key: str) -> dict | None: ...  # bumps hits
    async def put(self, project_id: int, cache_key: str, role: str, model: str,
                  output: dict, template_hash: int | None = None) -> None: ...
```

---

## 5. Reference SQL — hybrid retrieval

### 5.1 Stage B: item-history hybrid (lexical + dense, RRF k=60 in SQL)

Complete, runnable as-is (parameters: `$1 project_id`, `$2 emb_model_ver`,
`$3 salient-terms string`, `$4 query halfvec literal`, `$5 exception-name string for trgm
fallback`, `$6 query template_ids bigint[]`, `$7 k`).

```sql
SET LOCAL hnsw.ef_search = 80;
SET LOCAL hnsw.iterative_scan = relaxed_order;   -- pgvector >= 0.8

WITH q AS (
    SELECT websearch_to_tsquery('simple', $3) AS tsq
),
lex AS (                                          -- lexical top-50, field-boosted
    SELECT fs.item_id,
           ts_rank_cd('{0.1, 0.2, 0.4, 1.0}',    -- weights {D,C,B,A}: tmpl,frames,msg,exc
                      fs.signature_tsv, q.tsq) AS lex_score,
           row_number() OVER (
               ORDER BY ts_rank_cd('{0.1,0.2,0.4,1.0}', fs.signature_tsv, q.tsq) DESC,
                        fs.item_id DESC) AS l_rank
    FROM analyzer.failure_signature fs
    JOIN analyzer.test_item ti USING (project_id, item_id)
    CROSS JOIN q
    WHERE fs.project_id = $1
      AND ti.issue_type IS NOT NULL
      AND ti.issue_type_group <> 'ti'
      AND fs.signature_tsv @@ q.tsq
    ORDER BY lex_score DESC, fs.item_id DESC
    LIMIT 50
),
lex_trgm AS (                                     -- fallback when FTS found nothing
    SELECT fs.item_id,
           similarity(fs.exc_text, $5) AS lex_score,
           row_number() OVER (ORDER BY similarity(fs.exc_text, $5) DESC,
                              fs.item_id DESC) AS l_rank
    FROM analyzer.failure_signature fs
    JOIN analyzer.test_item ti USING (project_id, item_id)
    WHERE fs.project_id = $1
      AND ti.issue_type IS NOT NULL AND ti.issue_type_group <> 'ti'
      AND $5 <> '' AND fs.exc_text % $5
      AND NOT EXISTS (SELECT 1 FROM lex)
    ORDER BY lex_score DESC, fs.item_id DESC
    LIMIT 50
),
sparse AS (
    SELECT * FROM lex UNION ALL SELECT * FROM lex_trgm
),
dense AS (                                        -- dense top-50 (HNSW or exact per §2.5)
    SELECT fs.item_id,
           1 - (fs.emb <=> $4::halfvec(384)) AS cosine,
           row_number() OVER (ORDER BY fs.emb <=> $4::halfvec(384),
                              fs.item_id DESC) AS d_rank
    FROM analyzer.failure_signature fs
    JOIN analyzer.test_item ti USING (project_id, item_id)
    WHERE fs.project_id = $1
      AND fs.emb_model_ver = $2
      AND fs.emb IS NOT NULL
      AND ti.issue_type IS NOT NULL
      AND ti.issue_type_group <> 'ti'
    ORDER BY fs.emb <=> $4::halfvec(384)
    LIMIT 50
),
fused AS (                                        -- RRF, k = 60
    SELECT COALESCE(s.item_id, d.item_id)                    AS item_id,
           s.l_rank                                          AS sparse_rank,
           d.d_rank                                          AS dense_rank,
           s.lex_score, d.cosine,
           COALESCE(1.0 / (60 + s.l_rank), 0)
         + COALESCE(1.0 / (60 + d.d_rank), 0)                AS rrf_score
    FROM sparse s FULL OUTER JOIN dense d USING (item_id)
)
SELECT f.item_id, f.sparse_rank, f.dense_rank, f.lex_score, f.cosine, f.rrf_score,
       analyzer.array_jaccard(fs.template_ids, $6) AS jaccard_templates,
       fs.error_hash, fs.exception_fp, fs.template_ids,
       ti.issue_type, ti.test_case_hash, ti.launch_id, ti.launch_number,
       ti.is_auto_analyzed,
       le.source AS label_source, le.ts AS label_ts,
       mm.mode_id
FROM fused f
JOIN analyzer.failure_signature fs ON fs.project_id = $1 AND fs.item_id = f.item_id
JOIN analyzer.test_item          ti ON ti.project_id = $1 AND ti.item_id = f.item_id
LEFT JOIN LATERAL (
    SELECT source, ts FROM analyzer.label_event le
    WHERE le.project_id = $1 AND le.item_id = f.item_id
    ORDER BY le.ts DESC LIMIT 1
) le ON true
LEFT JOIN LATERAL (
    SELECT mode_id FROM analyzer.mode_membership mm
    WHERE mm.project_id = $1 AND mm.item_id = f.item_id
    ORDER BY mm.match_score DESC LIMIT 1
) mm ON true
ORDER BY f.rrf_score DESC, f.item_id DESC
LIMIT $7;   -- 20 default
```

Notes:
- `$3` (salient terms) is built in Python from the query signature: exception class names +
  top message tokens, joined with spaces (websearch semantics = implicit AND with OR
  groups; builder emits `"TimeoutException" OR "SocketTimeout" connection pool` style
  strings, quoting exact classnames).
- Deterministic tie-breaks (`item_id DESC`) keep golden-file tests stable.
- **BM25 swap-in point**: the `sparse` CTE is produced by
  `PgStore._lexical_top(project_id, q, 50) -> SQL fragment`. With an operator-installed
  BM25 extension (VectorChord-bm25 / ParadeDB pg_search) a feature flag
  `ANALYZER_LEXICAL_BACKEND=bm25` swaps the fragment for the `@@@`/`bm25` operator query on
  the same columns; the fused shape and all downstream code are unchanged.

### 5.2 Stage A: mode-centroid match

```sql
-- $1 project_id, $2 emb_model_ver, $3 query halfvec, $4 exception_fp bigint[]
SELECT fm.mode_id, fm.status, fm.label, fm.label_source, fm.purity, fm.support,
       fm.representative_template_ids, fm.title,
       1 - (fm.centroid <=> $3::halfvec(384))          AS cosine,
       (fm.exception_fps && $4)                        AS fp_hit
FROM analyzer.failure_mode fm
WHERE fm.project_id = $1
  AND fm.status IN ('seed', 'candidate', 'confirmed')
  AND ((fm.centroid IS NOT NULL AND fm.emb_model_ver = $2) OR fm.exception_fps && $4)
ORDER BY fp_hit DESC,                                   -- exact fingerprint hits first
         fm.centroid <=> $3::halfvec(384) NULLS LAST
LIMIT 10;
```

Exact scan by design (hundreds of rows); `fp_hit=true` rows map to
`matched_by='hash'`, others to `matched_by='vector'`.

---

## 6. Data lifecycle

| Table | Retention | Mechanism |
|---|---|---|
| `test_item`, `failure_signature` | `project.settings.retention_days` (default 365) + RP-driven deletes (`clean`, `item_remove`, `launch_remove`, `remove_by_*`) | nightly job calls `delete_by_time_range`; batched deletes of 10k rows/txn |
| `label_event` | forever (training log; tiny rows) | none |
| `suggestion` | 180 days after `outcome_ts`/`created_at`; rows referenced by `label_event.suggestion_id` have features copied into the training frame first | nightly job |
| `launch_group` | 90 days | nightly job |
| `llm_cache` | 90 days since `last_hit_at`, plus cap 100k rows/project (evict oldest `last_hit_at`) | nightly job |
| `log_template` | retained; templates with `last_seen` > 365 d pruned together with a Drain3 state rebuild | monthly job |
| `failure_mode`/`mode_membership` | modes auto-`retired` when `ewma_hit_rate < 0.01` and `last_seen_at` > 180 d; retired modes deleted after another 180 d | weekly job |
| `metrics_daily`, `schema_migrations`, `project`, `drain3_state` | forever | none |

**Autovacuum for hot partitions** (`failure_signature_p*`, `test_item_p*`) — set in
`0001_init.sql` per partition:

```sql
ALTER TABLE failure_signature_p00 SET (
    autovacuum_vacuum_scale_factor = 0.05,
    autovacuum_analyze_scale_factor = 0.02
);
```

After bulk deletes (project delete, retention sweep) the job issues explicit
`VACUUM (ANALYZE) analyzer.failure_signature_pNN` on touched partitions. HNSW indexes bloat
under heavy delete/insert; when a partition's index size exceeds 2× its expected size
(§7), run `REINDEX INDEX CONCURRENTLY <part>_emb_hnsw` (maintenance job, off-peak, one
partition at a time).

**Partition count guidance**: default **16** hash partitions balances partition pruning,
autovacuum parallelism, and per-partition HNSW build times for installs up to ~50 projects
/ ~20M signatures. Bigger installs: bump to 32/64 **before first start** via
`ANALYZER_PG_PARTITIONS` (read only by `0001_init.sql` generation — changing it later
requires a manual repartition, documented as unsupported in v1).

**Re-embed procedure (emb_model_ver bump)**: on startup, if the bundled encoder's version
constant `EMB_MODEL_VER` > max stored version: (1) new writes use the new version
immediately; (2) a background job scans `failure_signature WHERE emb_model_ver <
current LIMIT 512` per batch, re-embeds from `signature_text` fields, updates `emb` +
`emb_model_ver`; (3) `failure_mode.centroid` recomputed per mode from re-embedded members;
(4) until a row is migrated it is invisible to the dense CTE (`emb_model_ver = $2` filter)
but still retrieved lexically (CONTEXT §3: FTS fallback for not-yet-re-embedded rows);
(5) after completion, REINDEX HNSW partitions.

---

## 7. Sizing estimates (per 1M analyzed failures, defaults)

| Object | Per-row estimate | Total @ 1M |
|---|---|---|
| `test_item` heap | ~220 B | 0.22 GB |
| `failure_signature` heap | ~2.4 KB (texts ~1.2 KB, tsv ~0.5 KB, emb 770 B, arrays) | 2.4 GB |
| `emb` HNSW (halfvec, m=16) | ~900 B/vector (768 B data + graph links) | 0.9 GB |
| `signature_tsv` GIN | ~600 B/row | 0.6 GB |
| trgm GIN on `exc_text` | ~150 B/row | 0.15 GB |
| btree indexes (hashes, launch, tch) | ~200 B/row combined | 0.2 GB |
| `suggestion` (1 per item, features jsonb ~1 KB) | ~1.2 KB | 1.2 GB (rolls off at 180 d) |
| `label_event` | ~90 B | 0.09 GB |
| everything else (KB, templates, stats, metrics) | — | < 0.2 GB |
| **Total** | | **~5.9 GB per 1M items** |

RAM guidance: for good latency the HNSW indexes + hot GIN pages must be cached. For a 1M-
item install: `shared_buffers = 2 GB`, `effective_cache_size = 4 GB`, PG container 4 GB.
Tiny installs (< 100k items, no HNSW yet) run comfortably at PG defaults inside the
standard RP compose Postgres. `maintenance_work_mem = 512 MB` recommended for HNSW builds
(build spills to disk otherwise; still correct, just slower). Analyzer app container
unaffected (~1–2 GB, CONTEXT §6).

---

## 8. Acceptance criteria

Verifiable checklist; integration tests live in `tests/integration/test_db_*.py` against
dockerized `pgvector/pgvector:pg16` (CONTEXT §6).

1. **Clean bootstrap**: empty PG16+pgvector container + `ANALYZER_PG_DSN` pointing at a
   nonexistent DB → service creates DB, schema, extensions, applies `0001_init.sql`,
   healthcheck `GET /` reports DB connected. `schema_migrations` has exactly row
   version=1 with correct sha256.
2. **Idempotent restart**: second start applies nothing, mutates nothing
   (`pg_stat_user_tables` write counters unchanged), starts serving.
3. **Missing pgvector fail-fast**: against vanilla `postgres:16`, process exits with code 3
   and the exact FATAL message of §1.1 (no stack trace as the last line).
4. **Concurrent starts**: two containers started simultaneously against one empty DB →
   both healthy, migrations applied exactly once (assert single `applied_at`, no errors in
   either log). Test harness: two `asyncio` runners with a barrier.
5. **Checksum guard**: mutate one byte of an applied migration file → start fails with
   exit 5 naming the file.
6. **Roundtrip**: `upsert_items` + `upsert_signatures` for a fixture of 200 items → all
   rows land in the correct hash partition (`SELECT tableoid::regclass` distribution
   check), generated `signature_tsv`/`issue_type_group` populated.
7. **Hybrid query**: seeded fixture (50 labeled signatures incl. 5 near-duplicates of the
   query, embeddings from the real ONNX encoder) → §5.1 query returns fused top-20;
   the 5 near-duplicates occupy the top ranks; every returned row has
   `rrf_score = 1/(60+sparse_rank) + 1/(60+dense_rank)` (nulls → 0) verified in-test;
   `ti`-labeled and unlabeled rows never appear.
8. **trgm fallback**: query whose salient terms yield no tsquery hits but whose exception
   name is a 1-char-typo of a stored `exc_text` → candidates still returned via `lex_trgm`.
9. **Version isolation**: signatures with mixed `emb_model_ver` → dense CTE returns only
   the current version; older rows still reachable lexically.
10. **Mode matching**: seed KB inserted; §5.2 returns `fp_hit=true` rows first; a query
    vector near a confirmed centroid returns it with cosine > 0.9 on fixture data.
11. **Tenancy**: identical fixture in projects A and B → `find_candidates(A, ...)` never
    returns B's items (property-based test over random filters); `llm_cache.get` with A's
    key never returns B's row.
12. **Deletes**: `delete_launches` removes test_item/failure_signature/suggestion rows and
    leaves `label_event` intact; `delete_project` leaves zero rows in any table for that
    project; `delete_by_time_range('log_time', ...)` respects `log_time_max`.
13. **Drain3 CAS**: two concurrent `save()` with the same expected_version → exactly one
    succeeds, the other returns False; templates mirror matches miner state after retry.
14. **HNSW threshold job**: partition seeded with 60k rows → maintenance pass creates the
    HNSW index CONCURRENTLY; query plan (EXPLAIN) shows index scan afterwards, exact scan
    before.
15. **Golden stability**: §5.1 with fixed fixture + fixed query returns byte-identical
    ordered item_id list across runs (tie-breaks deterministic).
