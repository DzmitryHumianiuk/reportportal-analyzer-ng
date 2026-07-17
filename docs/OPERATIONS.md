# OPERATIONS — running analyzer-ng

Operator reference for a live analyzer-ng: configuration, tuning thresholds, the
LLM sidecar and its kill switch, retraining, and troubleshooting. Companion to
`docs/INSTALL.md` (deployment) and `docs/G5-WALKTHROUGH.md` (end-to-end proof).

---

## 1. Configuration (environment variables)

All configuration is environment-driven, read **once at startup** into a frozen
`AppConfig` (`src/analyzer_ng/config.py`). Precedence: process env > `.env` (dev
only) > coded default. The authoritative list with defaults is:

- **`src/analyzer_ng/config.py`** — every field is the lowercase of its env var.
- **spec `01-architecture.md` §5** — the annotated table (§5.1 legacy-compatible
  names that must stay RP-compatible; §5.2 new analyzer-ng vars; §5.3 validation).

Legacy Elasticsearch/datastore vars (`ES_*`, `DATASTORE_*`, `MINIO_*`) are
**accepted-and-ignored** with a one-line WARN each, so an unmodified RP analyzer
env block starts this image without crashing.

### The vars you actually touch

| Var | Default | Purpose |
|---|---|---|
| `AMQP_URL` | — | broker URL (credentials stripped from logs) |
| `AMQP_VIRTUAL_HOST` | `analyzer` | RP analyzer vhost |
| `AMQP_EXCHANGE_NAME` | `analyzer` | advertised exchange (fanout) |
| `ANALYZER_PG_DSN` | — | Postgres DSN for the `analyzer` DB (or set discrete `ANALYZER_PG_*`) |
| `ANALYZER_PG_CREATE_DB` | `true` | create the DB at startup if missing (needs CREATEDB; pgvector still needs a superuser — see INSTALL §1c) |
| `ANALYZER_PRIORITY` | `1` | advertised analyzer priority (lower wins across multiple analyzers) |
| `ANALYZER_NG_WORKERS` | `2` | worker-pool size (target ≤ 2 GiB / 2 vCPU) |
| `LOGGING_LEVEL` | `INFO` | log level; `DEBUG` also logs message bodies |
| `ANALYZER_LLM_ENABLED` | `false` | master switch for the LLM sidecar (§3) |

Startup fails fast (exit 2) on invalid config, and on a missing embedding-model
directory (`ANALYZER_EMB_MODEL_PATH`, baked into the image). DB bootstrap exits
3 (operator error: bad server / missing pgvector / privilege) or 4 (connect retries
exhausted).

## 2. Decision thresholds & pipeline tunables

Confidence gates (spec 03). Raise a threshold to be more conservative (fewer
auto-labels / suggestions, more abstains); lower it to be more eager.

| Var | Default | Effect |
|---|---|---|
| `ANALYZER_AUTO_MIN_PROB` | `0.6` | min calibrated probability to **auto-apply** a defect. Higher → fewer auto-labels |
| `ANALYZER_SUGGEST_MAX` | `3` | max suggestions returned in Make Decision |
| `ANALYZER_MAX_LOGS_PER_ITEM` | `20` | cap on logs embedded per item |
| `ANALYZER_DRAIN_SIM_TH` | `0.4` | Drain3 template similarity threshold (log clustering) |
| `ANALYZER_DRAIN_MAX_LINES` | `40` | max lines kept per log for templating |
| `ANALYZER_BURST_SI_SHARE` | `0.5` | co-failure burst → System Issue prior weight |
| `ANALYZER_TIME_DECAY` | `0.999` | recency decay for historical label priors |

RP-side per-project knobs (project attributes, not analyzer env):
`analyzer.isAutoAnalyzerEnabled`, `analyzer.autoAnalyzerMode`
(`ALL`/`LAUNCH_NAME`/`CURRENT_LAUNCH`), `analyzer.minShouldMatch`,
`analyzer.numberOfLogLines`. See INSTALL §5.

Model-version stamping: every suggestion/analysis row and every
`SuggestAnalysisResult.modelInfo` carries `emb_model_ver`, `gbm_model_ver`,
`kb_snapshot_id`; similarity is never computed across differing `emb_model_ver`.

## 3. LLM sidecar — enable, roles, kill switch

Off by default. With `ANALYZER_LLM_ENABLED=false` the `llm/` package is inert —
construction is a no-op and no pipeline path touches it (spec 04 §0).

**Enable:** set `ANALYZER_LLM_ENABLED=true` and run the `ollama` sidecar
(`--profile llm`, see INSTALL §3). Key vars:

| Var | Default | Purpose |
|---|---|---|
| `OLLAMA_URL` | `http://ollama:11434` | LLM endpoint |
| `ANALYZER_LLM_MODEL` | `qwen3:4b-q4_K_M` | pinned Ollama tag (exact quantization) |
| `ANALYZER_LLM_API` | `ollama` | `ollama` (`/api/chat`) or `openai` (`/v1/chat/completions`) |
| `ANALYZER_LLM_TIMEOUT_S` | `20` | per-call timeout |
| `ANALYZER_LLM_JUDGE_TAU` | `0.75` | judge fires when `τ_suggest ≤ p* < this` |
| `ANALYZER_LLM_EXPLAINER` / `_EXTRACTOR` / `_JUDGE` / `_COLDSTART` | `true` | per-role static enables |

**Kill switch (runtime, per project × role).** Roles honor a runtime disable read
from the `llm_role_state` table (`src/analyzer_ng/llm/manager.py`). A role flips
**off automatically** when the nightly eval decides it is underperforming, and a
circuit breaker trips the sidecar on repeated LLM errors/timeouts. To force a role
off without a restart, set its `llm_role_state` row disabled; with the master
switch off, all roles are off regardless. The pipeline always degrades gracefully:
if the LLM is disabled/tripped, analysis falls back to the ML + KB path (this is
exactly what the LLM-off byte-identical G4 gate guarantees).

## 4. Retraining (the learning loop)

The GBM ranker retrains from stored `label_event` feedback (spec 03 §6.5):

- **Triggers:** every ~100 new label events, a nightly timer (~02:00), or an
  operator/RP `train_models` AMQP message. All routed through a single-flight
  scheduler (`src/analyzer_ng/ml/retrain.py`).
- **Debounce:** at most one retrain per hour (`_min_interval`); extra triggers
  return `debounced`.
- **Cold start:** below the minimum event count a retrain is a logged no-op — the
  system runs on seed-KB + rules until enough labeled history exists (this is why a
  brand-new install abstains; see G5 §3).
- **Ship gate:** a freshly trained model replaces the active one **only** if it
  beats it on the held-out eval (spec 03 §7). Training reads stored feature
  snapshots only; ship is atomic. Model artifacts live in `analyzer.model_artifact`.

Trigger manually (from an operator with RP admin, or any AMQP publisher on the
`analyzer` exchange, routing key `train_models`) — it returns no reply by design.

## 5. Observability

- `GET /` — legacy health JSON (200 healthy / 503 `{"status":"PostgreSQL is not
  healthy"}` when PG is down), with per-consumer thread status + running tasks.
- `GET /health` — `{live, ready, pg, amqp, emb_model_ver, gbm_model_ver, version,
  metrics}`; 503 until consumers are up.
- `GET /metrics` — Prometheus: `analyzer_requests_total{routing_key,outcome}`,
  `analyzer_request_seconds{routing_key}`, `analyzer_queue_depth`,
  `analyzer_suggest_shown_total`/`_accepted_total`/`_corrected_total{project}`,
  `analyzer_abstain_total{project}`, `analyzer_pg_pool_in_use`.
- Logs are JSON lines to stdout with `correlation_id`, `routing_key`, `project`,
  `duration_ms`, `app_version`. Bodies only at `DEBUG`. Credentials never logged.

Exhausted/unparseable messages are copied to the durable `analyzer-ng.dlq` queue
with `x-error` / `x-routing-key` / `x-retries` headers for post-mortem.

## 6. Troubleshooting

| Symptom | Likely cause & fix |
|---|---|
| `permission denied to create extension "vector"` (crash loop, exit 3) | RP postgres app user is not a superuser. Pre-create pgvector as the `postgres` superuser — INSTALL §1c. |
| `role lacks CREATEDB to create database 'analyzer'` | App user lacks CREATEDB. Pre-create the DB (pg-init / manual SQL) and set `ANALYZER_PG_CREATE_DB=false`. |
| `NOT_ALLOWED - vhost analyzer not found` (retrying) | The `analyzer` vhost is created by RP's `service-api`; it appears once api starts. Transient during first boot — analyzer-ng backs off and reconnects. |
| pg-init script "did not run" / `database analyzer does not exist` | Bind mount became an empty dir because your Docker VM (colima/lima/WSL) doesn't share the compose dir, **or** the postgres volume was already initialized (init scripts run once). Run the manual SQL — INSTALL §1c. |
| `Connection reset by peer` on each reply | Benign: RabbitMQ closing the `amq.rabbitmq.reply-to` direct-reply pseudo-queue after RP consumes the reply. The reply is delivered first; the publisher reconnects. No action needed. |
| Suggestions/auto-analysis always empty | Cold system (no labeled history) correctly abstains. Label a few items (change defects) so retrieval + retrain have signal — see G5 §5. Also confirm auto-analysis is enabled on the project (INSTALL §5). |
| `modelInfo` shows `emb=none`, `emb_model_ver:null` | The ONNX embedder did not load, so retrieval is running **lexical/signature-only** (still correct, but no dense cosine — weaker recall). When the model loads, startup logs `ONNX embedder loaded and warmed (e5s-int8-r…)` and `/health` reports that `emb_model_ver`; `null` means it soft-degraded. Look for the degrade line `ONNX embedder failed to load from … continuing LEXICAL-ONLY (emb=none, emb_model_ver=0)` and confirm `ANALYZER_EMB_MODEL_PATH` resolves inside the container (`/opt/analyzer/models/e5-small-int8`) **and** contains `model.onnx` + `tokenizer.json` (a missing *directory* fails startup fast; a present-but-unloadable model soft-degrades). The earlier wiring gap (embedder never constructed / tag never surfaced) is fixed — `null` now means the model genuinely isn't loadable, not a plumbing bug. |
| Ollama/LLM stalls | Circuit breaker trips the sidecar on repeated timeouts; roles auto-fall-back to ML+KB. Check `OLLAMA_URL` reachability and `ANALYZER_LLM_TIMEOUT_S`; the model is pulled on first use (pre-pull to avoid a cold stall). |
| Messages piling in `analyzer-ng.all` | Backpressure: workers saturated. Increase `ANALYZER_NG_WORKERS` / container CPU, or check for a slow/stuck handler in logs (`duration_ms`). |

For image/license/reliability acceptance details see spec `01-architecture.md` §10.
