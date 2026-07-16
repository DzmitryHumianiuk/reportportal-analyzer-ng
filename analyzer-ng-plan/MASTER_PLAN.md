# Analyzer-NG — Master Implementation Plan (multi-agent execution)

Status: READY FOR IMPLEMENTATION.
Read order for any implementing agent: `CONTEXT.md` → the spec section referenced by your
task → this plan's task card. Specs are authoritative; if a spec contradicts CONTEXT.md,
the spec wins (it is more recent and more detailed). If two specs contradict each other,
stop and escalate to the orchestrator.

Spec files:
- `specs/01-architecture.md` — service skeleton, AMQP contract (23 routing keys), config,
  docker, startup/shutdown, observability, acceptance checklist.
- `specs/02-database.md` — full DDL, migration runner, store contracts
  (RetrievalStore/KBStore/LabelStore/StatsStore/Drain3StateStore/LlmCacheStore),
  reference hybrid-retrieval SQL, lifecycle/sizing, acceptance checklist.
- `specs/03-pipeline.md` — preprocessing (ported legacy functions), Drain3 config,
  signatures/fingerprints, launch grouping, matching stages A/B/C, 39-feature GBM,
  decision policy, route semantics, 50-mode seed catalog, eval harness, acceptance.
- `specs/04-llm-sidecar.md` — optional Ollama roles (Explainer/Extractor/Judge/Cold-start),
  prompts, schemas, injection defenses, kill-switch, acceptance checklist.

Target deliverable: a new repository `analyzer-ng` producing a single Docker image that
drop-in replaces `analyzer`/`analyzer-train`/`opensearch` in a local ReportPortal
docker-compose installation, storing all state in PostgreSQL (pgvector), with optional
Ollama sidecar under compose profile `llm`.

---

## Global rules for implementing agents

1. **TDD where the spec gives acceptance criteria**: write the test from the checklist
   item first, then implement. Golden-file tests (fingerprints, signatures, RRF ordering)
   are mandatory before the implementation is considered done.
2. **Never invent wire-format fields.** AMQP request/response models are copied verbatim
   from spec 01 §AMQP contract (which was verified against legacy code). UI compatibility
   depends on exact field names.
3. **Determinism**: same input → same fingerprint, same group assignment, same candidate
   ordering (tie-breaks are specified in 02/03). Any nondeterminism is a bug.
4. **Every task ends with**: tests green locally (`pytest`), lint clean (`ruff`), and the
   task's acceptance items demonstrably checked (paste evidence into the PR/commit body).
5. **Python 3.12, pydantic v2, psycopg3, pika, drain3, lightgbm, onnxruntime** — versions
   pinned in spec 01 §dependencies. Apache-2.0-compatible only.
6. Commit granularity: one task = one branch/PR (or one commit series) named `phase{N}/task-{ID}`.

## Verification gates (orchestrator runs these between phases)

- **G0** (after Phase 0): `docker compose up` of the dev harness (PG16+pgvector, RabbitMQ)
  works; CI runs pytest+ruff; empty service starts, migrates, reports healthy.
- **G1** (after Phase 1): RP-compat integration test passes — sample `index`, `analyze`,
  `suggest`, `delete`, `defect_update` payloads (fixtures from legacy test_res) round-trip
  with schema-valid responses; RabbitMQ mgmt shows correct exchange args.
- **G2** (after Phase 2): pipeline e2e test — synthetic launch with 3 failure kinds is
  indexed, grouped, matched against seeded KB, suggestions written with calibrated
  confidences and abstains; golden fingerprints stable across two runs.
- **G3** (after Phase 3): learning-loop test — replayed label_events retrain GBM, eval
  harness reports metrics, ship-gate logic keeps/replaces model correctly.
- **G4** (after Phase 4): LLM-off byte-identical behavior test + (with Ollama mock) all
  four roles produce schema-valid outputs; injection fixture has no effect.
- **G5** (after Phase 5): full docker-compose with a real local ReportPortal instance:
  launch import → auto-analysis visible in UI, suggestions in "Make Decision" modal,
  defect update flows back as label_event.

---

## Phase 0 — Repo scaffold & dev harness (parallel: T0.1–T0.3)

### T0.1 Repo skeleton
- Input: spec 01 §repository layout, §dependencies.
- Create `analyzer-ng` repo: src layout (`app/amqp`, `app/api`, `app/core`, `app/db`,
  `app/ml`, `app/llm`, `app/config.py`, `app/main.py`), pyproject with pinned deps,
  ruff+pytest config, Makefile, README stub, Apache-2.0 LICENSE.
- Accept: `pip install -e .` works; `pytest` runs (empty suite ok); `ruff check` clean.

### T0.2 Dev/CI harness
- Input: spec 01 §docker, spec 02 §extensions.
- `docker-compose.dev.yml`: postgres:16 + pgvector image, rabbitmq:3-management;
  testcontainers-based pytest fixtures for both; GitHub Actions (or equivalent) CI running
  lint + unit + integration markers.
- Accept: `make test-integration` spins containers and passes a trivial connectivity test.

### T0.3 Config module
- Input: spec 01 §configuration (full env table).
- Pydantic-settings config with legacy-compatible env names, validation at import,
  WARN-and-ignore for legacy ES vars.
- Accept: unit tests for defaults, overrides, invalid values fail fast with clear message.

## Phase 1 — Foundation: DB + AMQP + service shell (T1.1 ∥ T1.2, then T1.3)

### T1.1 Migration runner + full schema (spec 02)
- Migration runner (ordered SQL files, sha256 checksums, advisory lock 0x616E7A6D69677230,
  `-- analyzer:no-transaction` support), `0001_init.sql` with the complete DDL from spec 02
  (16 hash partitions, halfvec(384), generated tsvector, all tables), optional
  auto-CREATE-DATABASE path (`ANALYZER_PG_CREATE_DB`), fail-fast pgvector/version checks.
- Accept (spec 02 checklist): clean apply on empty PG; two concurrent starts → single
  application; re-run idempotent; checksum tamper detected.

### T1.2 AMQP layer + service shell (spec 01)
- pika consumers (`all`/`train` queues with routing-key filter), exchange declaration with
  exact capability args + 406 redeclare fallback, RPC reply publisher thread, worker pool
  with bounded priority queue, retries + DLQ, graceful shutdown, FastAPI health/metrics.
- ALL pydantic wire models (copied per spec 01 tables) + serialization rules.
- Route handlers as stubs returning spec-correct empty/no-op responses; deprecated keys
  no-op with WARN exactly as specced.
- Accept (spec 01 checklist subset): exchange visible with args in mgmt API; `noop_echo`
  round-trips; malformed JSON → nack-to-DLQ; health shows amqp+pg status.

### T1.3 Store layer (spec 02 contracts)
- Implement RetrievalStore/KBStore/LabelStore/StatsStore/Drain3StateStore/LlmCacheStore
  against psycopg3 pool; the reference hybrid SQL as a versioned query module; unit tests
  on seeded fixtures incl. RRF ordering golden test and tenancy (`project_id`) isolation
  tests.
- Accept: hybrid query returns fused, deterministically-ordered results on fixtures;
  cross-project leakage test proves isolation; exact-scan vs HNSW paths both covered.

## Phase 2 — Pipeline: ingest → suggestion (T2.1 → T2.2 → T2.3; T2.4 ∥ after T2.1)

### T2.1 Preprocessing + Drain3 + signatures (spec 03 §1–4)
- Port the listed legacy text_processing functions verbatim (with their unit tests
  adapted from legacy test suite where available); Drain3 with masking config + PG
  persistence (CAS state blob + template mirror rows); signature doc builder; exception
  fingerprint + error_hash (xxh3); ONNX e5-small embedder (pinned revision, int8),
  query/passage prefixing, warmup.
- Accept: golden-file tests — fixed fixture logs → exact expected signature docs,
  fingerprints, hashes; drain state loss → rebuild ≥95% template recovery; embed p95
  within budget on CI hardware (soft assert/log).

### T2.2 Index & maintenance routes for real (spec 01 + 03)
- `index`: filter ERROR+/cap/near-dup per spec, build signatures, upsert via stores,
  update test_history_stats incrementally; `delete/clean/item_remove/launch_remove/
  remove_by_*`: real deletions; `defect_update`: label_event append + purity update +
  reply with not-found ids.
- Accept: G1 fixtures pass against real pipeline; idempotent re-index of same launch.

### T2.3 Grouping, matching, decision (spec 03 §5–6)
- Launch grouping (exact-fp buckets + greedy cosine θ=0.83 + merge pass, burst si_prior);
  Stage A/B/C matching; 39-feature extractor (features stored in suggestion.features);
  rule-based cold fallback; decision policy bands (τ_auto 0.75 / τ_suggest 0.45 →
  abstain=ti); analyzerMode scope filters replicated; `analyze` and `suggest` responses
  in legacy wire format; `cluster` and `search` routes per spec 03 §7.
- Accept: G2 e2e; determinism test (shuffled input order → same groups/labels);
  analyzerMode filter unit tests.

### T2.4 Seed KB (spec 03 §8)
- Ship the 50-mode YAML catalog as package data; idempotent loader at startup; lazy
  per-project copies on first match; matching rules engine (regex/keyword sets).
- Accept: synthetic fixture suite — seed catalog classifies ≥90% of fixture cases to the
  expected mode; re-run loader → no duplicates.

## Phase 3 — Learning loop (T3.1 → T3.2)

### T3.1 Training & calibration (spec 03 §6)
- LightGBM multiclass trainer from label_event ⨝ stored feature snapshots; per-project
  isotonic calibration (≥300 events); artifact storage in PG (bytea + version row);
  retrain trigger (100 new events or nightly via `train_models` route + internal timer).
- Accept: training on synthetic event stream produces model beating rule fallback on
  held-out replay; artifacts versioned; serving picks latest shipped model atomically.

### T3.2 Eval harness + metrics (spec 03 §9)
- Chronological 80/20 replay; ship-gate (macro-F1 within 0.005, auto-band precision ≥
  active, abstain +≤0.05); metrics_daily population; `stats_info`-style reporting hooks.
- Accept: G3; deliberately degraded candidate model is rejected by the gate.

## Phase 4 — Optional LLM sidecar (T4.1 → T4.2; independent of Phase 3, needs Phase 2)

### T4.1 LLM client + roles (spec 04)
- Ollama client (OpenAI-compatible), startup probe (no auto-pull), circuit breaker,
  bounded single-worker async queue (judge > extractor/coldstart > explainer), migration
  `0002_llm.sql` (llm_event, llm_role_state), all four roles with exact prompts, JSON
  schemas via `format`, post-validation (substring quotes, candidate existence),
  llm_cache usage, sanitizer (chat-template tokens, role-line prefixes, nonce envelopes).
- Accept (spec 04 checklist): LLM-off byte-identical test; schema-valid-or-dropped
  property test; injection fixture no-effect test; cache hit path covered.

### T4.2 Kill-switch & eval
- Nightly paired comparison vs label_event; per-project auto-disable (N≥50, precision
  gap >0.02, Wilson bound); admin visibility in /metrics.
- Accept: G4; simulated underperforming role auto-disables.

## Phase 5 — Packaging & RP integration (T5.1 → T5.2)

### T5.1 Docker image + compose (spec 01 §docker)
- Production Dockerfile (python:3.12-slim, ONNX model baked at build, non-root,
  HEALTHCHECK); `docker-compose.analyzer-ng.yml` overlay for a standard RP compose:
  replaces analyzer/analyzer-train/opensearch; postgres reuse with separate `analyzer` DB;
  optional `ollama` service under profile `llm` with named volume.
- Accept: image builds reproducibly; compose up from scratch → healthy in <60s on a
  laptop; RAM at idle ≤ target from spec 01.

### T5.2 End-to-end against real ReportPortal
- Bring up full local RP with analyzer-ng; run a scripted scenario: import demo launch
  with failures → verify auto-analysis labels in RP UI/API, suggestions in Make Decision,
  manual defect change → label_event recorded → stats update. Document any RP-side
  settings needed (analyzer priority etc.). Write `docs/INSTALL.md` (how to swap into an
  existing local RP compose) and `docs/OPERATIONS.md` (env vars, thresholds, LLM enable,
  troubleshooting).
- Accept: G5 walkthrough recorded in docs with screenshots/curl transcripts.

---

## Parallelization map

```
Phase 0: T0.1 ∥ T0.2 ∥ T0.3
Phase 1: T1.1 ∥ T1.2  →  T1.3
Phase 2: T2.1 → T2.2 → T2.3 ;  T2.4 ∥ (after T2.1)
Phase 3: T3.1 → T3.2          (needs Phase 2)
Phase 4: T4.1 → T4.2          (needs Phase 2; ∥ with Phase 3)
Phase 5: T5.1 → T5.2          (needs G2+G3; G4 if LLM shipped)
```

Suggested agent allocation: 2–3 implementation agents in parallel per phase respecting
the map; one reviewer agent per gate running the checklist of the corresponding spec.

## Risk register (watch during implementation)

1. **Wire-format drift** — RP backend may validate response fields strictly; G1/G5 catch
   this; keep legacy field names even when semantically legacy-only (e.g. `esScore`).
2. **pgvector image availability** — pin a specific pgvector-enabled postgres image tag in
   compose; document the extension requirement for users bringing their own PG.
3. **Drain3 state races** — CAS/state_version logic per spec 02; single-writer per project
   enforced by worker routing (hash project_id → worker).
4. **Embedding latency on weak CPUs** — batch on index path; suggest path uses cached
   item signatures; soft-degrade to lexical-only if embed queue backs up.
5. **Feature drift between train and serve** — features are snapshotted into
   suggestion.features at serve time and training reads ONLY those snapshots (spec 03).
6. **LLM scope creep** — roles limited to the four specced; any new role requires a spec
   change first.
