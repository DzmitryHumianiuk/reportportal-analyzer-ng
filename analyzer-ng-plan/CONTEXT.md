# Analyzer-NG — Shared Design Context (Source of Truth)

This file is the authoritative design brief for all specification and implementation agents.
It condenses two research rounds: (A) deep code analysis of the legacy
`reportportal/service-auto-analyzer`, and (B) a greenfield design consilium
(vector-retrieval, local SLM, AIOps SOTA, chief architect).

## 1. Product goal

Build **analyzer-ng**: a new, standalone Python microservice for ReportPortal that
auto-triages failed test items. It replaces `service-auto-analyzer` +
OpenSearch in a local docker-compose ReportPortal installation. All state lives in
**PostgreSQL** (its own schema/database), created automatically at first container start.
An optional **Ollama sidecar** provides LLM roles. No OpenSearch/Elasticsearch anywhere.

Labels (issue types), ReportPortal conventions: `pb` (product bug), `ab` (automation bug),
`si` (system issue), `nd` (no defect), `ti` (to investigate). Custom subtypes exist per
project (e.g. `pb001`); the locator string is what flows through the API.

## 2. Paradigm (decided — do not relitigate)

**Knowledge-base-first, group-then-classify:**

1. **Ingest**: all failed test item error logs → preprocess → Drain3 template mining →
   build a compact *failure signature document* per test item.
2. **Fingerprint**: deterministic hash of (exception fingerprint + normalized top frames +
   ordered error template IDs). Exact fingerprint match = fast path (no ML).
3. **Launch-level grouping**: cluster failures *within a launch* by signature similarity.
   One group = one diagnosis, fanned out to members. Burst heuristic: if a dominant new
   fingerprint covers > X% of a launch's failures → strong `si` (system issue) prior.
4. **Failure-mode KB**: per-project catalog of curated failure modes
   (centroid embedding + representative templates + exception fingerprints + human label +
   purity + stats + optional SLM-written title/summary; lifecycle candidate → confirmed →
   retired). Matching goes against KB modes first, then against raw labeled item history.
   A **seed KB of ~40 generic modes** with prior labels ships with the product (cold start).
5. **Hybrid retrieval**: exact `error_hash` → lexical (Postgres FTS on signature text,
   field-boosted) + dense (pgvector cosine over signature embeddings) fused with RRF (k=60)
   + template-set Jaccard. Retrieve only labeled/analyzable items. Time-decay and
   label-confidence weighting applied post-retrieval in Python.
6. **Decision layer**: LightGBM (install-wide model, per-project isotonic calibration)
   over features: similarity/retrieval features + **non-text features** (retry outcome,
   per-test historical failure rate / flakiness score, co-failure cluster size in launch,
   test age, environment/agent match, same test_case_hash, recency decay, KB-mode purity &
   support). Calibrated probability; **abstain below threshold → `ti`** with a reason.
7. **Feedback loop**: every human confirm/correct is an append-only `label_event`;
   updates mode purity; GBM retrains from label_events (seconds, CPU); metrics computed
   against this stream (acceptance rate, correction rate, per-label precision).
8. **Optional local SLM (Ollama sidecar, feature-flagged)** — adopted roles only:
   - **Explainer**: verbalize why an item matched a mode (facts injected from DB; verbatim
     quotes validated by substring check). Async; UX only.
   - **Extractor**: structured JSON extraction from noisy logs (root exception vs wrappers,
     failing layer test/app/infra, error class) → stored columns → GBM features. Cached per
     template-hash.
   - **Judge**: gated to low-confidence band (GBM max-prob < τ_judge); picks best of top-K
     candidates or "none"; grammar/JSON-schema-constrained enum output.
   - **Cold-start rubric triage**: zero-label projects; provisional labels flagged
     `ai_suggested`.
   - REJECTED roles: primary classifier, per-install LoRA, agentic loops, reasoning modes
     (default `/no_think`).
   - Default models: Qwen3-4B (Apache-2.0) primary; IBM Granite 4 Micro as alternative.
   - Security: logs are untrusted input (prompt injection). Constrained decoding always;
     LLM output can only suggest, never auto-confirm; strip prompt-shaped sequences.
9. **Interactive suggest path never runs model inference synchronously**: analysis is
   async (AMQP queue-driven); suggestions are precomputed rows read by the UI. Embedding
   of a query signature (~50–200 ms CPU) + SQL retrieval (tens of ms) is the only
   synchronous work allowed on the suggest path.

## 3. Storage decisions (decided)

- **PostgreSQL 16+ with `pgvector` (>= 0.8, iterative index scans)** is the only required
  datastore. The analyzer uses **its own logical database or schema** (`analyzer` schema),
  NOT ReportPortal's main tables — data arrives over AMQP exactly as it does for the
  legacy analyzer. Rationale: derived fields don't exist in the main DB; avoid OLTP
  contention; keep service decoupling.
- Schema is created/migrated **automatically at container start** (app-level migration
  runner, e.g. ordered SQL files + a `schema_migrations` table; idempotent; safe for
  concurrent starts via advisory lock).
- Multi-tenancy: single tables **hash-partitioned by `project_id`** (or LIST for small
  installs), never table-per-project. All queries carry `project_id`.
- Embeddings: one primary vector per test item signature (`halfvec`/`vector`, 384–512 dims,
  Matryoshka-truncated), `emb_model_ver` column; never mix versions in one similarity
  computation; BM25/FTS fallback for not-yet-re-embedded rows.
- Vector search: exact scan under tenant filter for small partitions; HNSW for large.
  KB-mode centroids are the hot search space (hundreds per project) — item history search
  is second stage.
- FTS: Postgres native `tsvector` + `pg_trgm` by default (zero extra extensions beyond
  pgvector); design the SQL so an operator-installed BM25 extension (VectorChord-bm25 or
  ParadeDB pg_search, both optional) can be swapped in behind the same repository method.
- Embedding model: small multilingual encoder served in-process via ONNX Runtime int8
  (e.g. `intfloat/multilingual-e5-small` or `nomic-embed-text-v2`-class, 384 dims),
  downloaded/bundled at build; deterministic version pinning.
- Cold/analytics tier (Parquet + DuckDB) is **out of scope for v1** (phase 4+); design
  should not preclude nightly export.

## 4. Integration contract (must match ReportPortal expectations)

The legacy analyzer integrates via RabbitMQ; analyzer-ng must be a **drop-in replacement**:

- Exchange: name from `ANALYZER_EXCHANGE_NAME` (default `analyzer`), declared with
  capability arguments: `analyzer` (service name), `analyzer_index`, `analyzer_priority`,
  `analyzer_log_search`, `analyzer_suggest`, `analyzer_cluster` (booleans/ints) — this is
  how the RP backend discovers analyzers. `version` arg included.
- Queues & routing keys handled (RPC-style: reply published to `props.reply_to` with the
  original `correlation_id`, JSON bodies):
  - `index` — index launches (list of Launch objects with testItems[].logs[]). Response:
    IndexResult-like counts.
  - `analyze` — auto-analysis: list[Launch] → list[AnalysisResult]
    `{testItem: int, issueType: str, relevantItem: int}` (+ we extend with
    `matchScore`/`explanation` if the contract tolerates extra fields — verify; otherwise
    keep extended data internal).
  - `suggest` — TestItemInfo → list[SuggestAnalysisResult] (`testItem, relevantItem,
    relevantLogId, issueType, matchScore, esScore, esPosition, resultPosition, modelInfo,
    usedLogLines, minShouldMatch, clusterId, methodName...`) — keep field names for UI
    compatibility; fill legacy-only fields with sensible values.
  - `cluster` — LaunchInfoForClustering → ClusterResult (clusters of similar logs with
    cluster ids/messages).
  - `search` — SearchLogInfo → list of matching log ids (used by RP "similar TI" search).
  - `delete` (delete project index), `clean` (delete specific logs), `item_remove`,
    `launch_remove`, `defect_update` (labels changed in RP UI — THIS IS THE FEEDBACK
    SIGNAL: treat as label_event source), `remove_by_launch_start_time`,
    `remove_by_log_time`, `namespace_finder`, `suggest_patterns`, `train_models`,
    `stats_info`/`index_suggest_info` variants — implement or explicitly no-op with logged
    warning, matching legacy behavior.
- Pydantic models for these payloads exist in legacy repo `app/commons/model/launch_objects.py`
  — reuse their shape (copy definitions; do not import legacy package).
- Flask (or FastAPI) healthcheck on `ANALYZER_HTTP_PORT` (default 5001), `GET /` returns
  status incl. DB connectivity.
- Env vars: keep `AMQP_URL`, `AMQP_EXCHANGE_NAME`, `ANALYZER_PRIORITY`, `ANALYZER_INDEX`,
  `ANALYZER_LOG_SEARCH`, `ANALYZER_SUGGEST`, `ANALYZER_CLUSTER`, `ANALYZER_HTTP_PORT`;
  add `ANALYZER_PG_DSN` (or discrete PG vars), `ANALYZER_PG_SCHEMA=analyzer`,
  `OLLAMA_URL` (optional), `ANALYZER_LLM_ENABLED=false`, model/threshold config.
- Deployment target: docker-compose bundle of ReportPortal; analyzer-ng is one container
  (plus optional ollama container). Legacy `opensearch` container becomes unnecessary when
  analyzer-ng replaces both `analyzer` and `analyzer-train` services.
- Reference for legacy behavior (cloned repo, read-only):
  `/private/tmp/claude-501/-Users-Dmitriy-Gumeniuk-scripts/73236fa0-35ae-4721-b518-a2ce62e95c78/scratchpad/service-auto-analyzer`

## 5. Reusable legacy assets (port, don't reinvent)

- Text preprocessing pipeline (`app/utils/text_processing.py`, `log_preparation.py`,
  `prepared_log.py`): log-level/datetime/thread stripping, UUID/hex/token masking, URL/path
  extraction, exception extraction, stacktrace detection. Port the *useful subset*;
  Drain3 replaces most ad-hoc regex normalization for templates, but exception/status-code
  extraction and stacktrace splitting remain valuable verbatim features.
- Log filtering semantics: only ERROR+ logs (log_level >= 40000), cap ~20 logs/item,
  drop near-duplicates.
- AMQP handling patterns (exchange declaration args, RPC reply) from `app/amqp/`.

## 6. Non-functional requirements

- CPU-only must work end-to-end (LLM off). Tiny install: analyzer container ~1–2 GB RAM.
- Async analysis budget: seconds per launch group; suggest read path < 300 ms server-side.
- Multi-tenant correctness: every query filtered by project; no cross-project leakage
  (including LLM prompt caches).
- Observability: structured logs, per-project metrics tables (suggestion shown/accepted/
  corrected), model versions stamped on every suggestion.
- Testing: unit tests + integration test with dockerized PG (pgvector) + rabbitmq;
  golden-file tests for signature/fingerprint stability.
- License hygiene: Apache-2.0-compatible deps only in the default image.
- Python 3.12, pydantic v2, psycopg3 (or asyncpg), no heavyweight frameworks.

## 7. Deliverables of the spec phase (this plan)

- `specs/01-architecture.md` — service architecture & RP integration
- `specs/02-database.md` — PG schema, migrations, RetrievalStore contract
- `specs/03-pipeline.md` — analysis pipeline + seed failure-mode catalog
- `specs/04-llm-sidecar.md` — optional LLM roles spec
- `MASTER_PLAN.md` — phased multi-agent implementation plan (written by the orchestrator)
