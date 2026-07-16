-- Optional LLM sidecar bookkeeping tables (spec 04 §6.1).
--
-- Applied in one transaction by the migration runner (spec 02 §3) with
-- search_path = analyzer, public. `llm_event` is the per-call audit log (every
-- LLM suggestion is logged with its prompt_hash + model tag); `llm_role_state`
-- carries the per-project runtime kill-switch the nightly eval job (T4.2) flips.
--
-- The content cache (`llm_cache`) already exists from 0001 (spec 02 §2.11); this
-- migration only adds the two event/state tables.

-- §6.1 llm_event -------------------------------------------------------------
CREATE TABLE llm_event (
    event_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    project_id   bigint      NOT NULL,
    item_id      bigint      NOT NULL,
    role         text        NOT NULL CHECK (role IN ('explainer','extractor','judge','coldstart')),
    model        text        NOT NULL,                -- exact tag
    prompt_hash  char(64)    NOT NULL,
    cache_hit    boolean     NOT NULL DEFAULT false,
    outcome      text        NOT NULL CHECK (outcome IN
                   ('ok','schema_fail','validation_fail','timeout','breaker_open','dropped')),
    output       jsonb,                               -- NULL unless outcome='ok'
    latency_ms   integer,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX llme_proj_role_idx ON llm_event (project_id, role, created_at);

-- §6.1 llm_role_state --------------------------------------------------------
CREATE TABLE llm_role_state (
    project_id  bigint      NOT NULL,
    role        text        NOT NULL,
    enabled     boolean     NOT NULL DEFAULT true,
    reason      text,                                 -- 'auto_disabled_precision', 'admin'
    stats       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    decided_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, role)
);
