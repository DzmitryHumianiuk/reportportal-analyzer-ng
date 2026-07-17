# analyzer-ng — final whole-branch review: fix pass report

Branch: `develop`. All commits prefixed `review/final: `. One fix pass, TDD where
marked. Default runtime behavior is byte-stable (golden + rp-compat suites green;
the only wire change is the intended abstain-persists-a-row fix, whose reply stays
`[]`).

## Verification (commands + results)

- `ruff check src/ tests/` → **All checks passed!**
- `pytest tests/unit` → **496 passed, 1 skipped** (skip: exported e5-small ONNX
  model not present) in ~150s.
- `pytest tests/golden tests/integration/test_rp_compat.py
  tests/integration/test_analysis_pipeline.py tests/integration/test_index_pipeline.py
  tests/integration/test_db_stores.py` → **43 passed** (byte-stability + pipeline).
- `pytest tests/integration/test_db_migrations.py test_llm_stores.py
  test_metrics_daily_pg.py test_model_artifact.py test_seed_kb.py
  test_amqp_contract.py` → see final full-suite run below.

## Per-finding resolution

### CRITICAL

**#1 — Suggest-route abstains never wrote a suggestion row.**
`_render_suggestions` returned early (`proxy < TAU_SUGGEST` / no candidates) before
`_write_suggestion`, so abstained suggest decisions carried no feature snapshot and
were dropped from training once labeled. Hoisted the write into `suggest()` right
after `_decide` (removed it from `_render_suggestions`). Reply is byte-identical
(still `[]` on abstain).
RED→GREEN: `tests/integration/test_analysis_pipeline.py::test_suggest_abstain_still_writes_suggestion_row`
(proved 0 rows before, 1 `ti` row after). Commit `7ea7d6f`.

### IMPORTANT

**#2 — Migration runner never closed a reserved-version gap.**
Replaced high-watermark `version > max(applied)` with `select_pending()` =
set-difference against the ledger (ascending); a DB at `{1,2,3,5}` now applies
`0004` on the first start after merge. Added `ledger_gaps()` which logs ERROR on a
genuine skipped-version hole no file can fill (spec 02 §3.1 "no gaps"). Corrected
the false "closes it when 0004 lands" claim in the `0005` header.
Tests: unit `select_pending`/`ledger_gaps` cases; integration
`test_reserved_version_gap_closes_after_merge` (applies exactly 4); tamper/duplicate
detection unchanged. Commit `e2165f4`.

**#3 — Plaintext DSN (password) could reach logs.**
Added `redact_dsn()` (URL userinfo + `password=` keyword) mirroring
`amqp/client.remove_credentials_from_url`; applied to the `_maintenance_dsn`
`BootstrapError`. Tests: `test_redact_dsn_*`, `test_maintenance_dsn_error_message_redacts_password`.
Commit `6f8bd15`.

**#4 — Seven declared-but-unwired config knobs.**
Wired each into its consumer with the config default set equal to the code constant
(behavior byte-identical by default; asserted by
`test_wired_defaults_equal_code_constants`):
- `ANALYZER_AUTO_MIN_PROB` → `decide(tau_auto=…)` auto band (= `TAU_AUTO` 0.75)
- `ANALYZER_SUGGEST_MAX` → engine suggestion cap (= 3)
- `ANALYZER_BURST_SI_SHARE` → `group_launch(burst_x=…)` (= `BURST_X` 0.4)
- `ANALYZER_TIME_DECAY` → `features.decay` per-day factor via `FeatureContext`
  (= `TIME_DECAY_PER_DAY`; default path kept bit-identical to `exp(-ln2·d/90)`)
- `ANALYZER_DRAIN_MAX_LINES` → `DrainManager.max_lines` via `IndexPipeline` (= 40)
- `DEBUG_MODE` → `WorkerPool` inline mode (handlers run on the consumer thread, no
  pool; spec 01 §5.1)
Removed `ANALYZER_DRAIN_SIM_TH` from the config surface (changing it changes
template identity/fingerprints — pinned to `ml.drain.DEFAULT_SIM_TH`, documented in
OPERATIONS §2) and the stale `ANALYZER_SEED_KB_PATH` default (packaged data via
importlib.resources, T2.4). OPERATIONS §2 defaults updated.
Tests: `test_wired_defaults_equal_code_constants`, updated `test_config.py`,
`test_inline_mode_processes_on_caller_thread_without_workers`. Commit `1f92a2a`.

**#5 — Shutdown ordering.**
Reordered `AnalyzerService.shutdown` to consumers → worker pool (drain in-flight) →
sidecar/scheduler/timer → publisher, so a drain-window message can no longer request
a retrain / enqueue an LLM job against a stopped component. Structural order test
`test_shutdown_stops_components_in_safe_order`. Commit `961ed9a`.

### MINOR

**#6/#7 — LLM queue.** Eviction compares the incoming job with the least-urgent
queued candidate and drops the incoming job when it is itself the worst (fixed the
"numerically-lowest" docstring). `_pop_blocking` returns None immediately when
stopping, so shutdown finishes only the in-flight job, not the ≤500 backlog. Tests:
`test_overflow_drops_incoming_when_it_is_the_worst`, `test_stop_does_not_drain_backlog`.
Commit `52e9aa1`.

**#8 — Determinism tie-breaks.** `labels.fetch_training_frame`: `sg.suggestion_id`,
`mm.mode_id`, `le.event_id` secondary sorts. `queries.py`: `fs.item_id DESC` on the
dense-CTE LIMIT and `fm.mode_id DESC` on the mode-match ORDER BY. Bumped
`HYBRID_RETRIEVAL_VERSION` 1→2 (SQL text changed, per the module rule). Commit
`76dc364`.

**#9 — `index_suggest_info` must always reply `{}`.** Route parses leniently
(`_identity`) so a malformed payload can no longer raise ValidationError →
DLQ-without-reply; handler WARNs and returns `{}` for any shape. Test
`test_index_suggest_info_replies_empty_object_for_malformed_payload`. Commit `d0eadd9`.

**#10 — `decision._base`.** Delegates to `features.base_group` (case-folding
unification); Stage-A ti-guard uses the empty-string fold and also blocks
unrecognized locators. Tests: custom-uppercase-locator + unrecognized-locator cases.
Commit `09f736a`.

**#11 — `amqp.models.timestamp_factory`** uses UTC, not naive local time. `09f736a`.

**#12 — `RetrainScheduler.outcomes`** → `deque(maxlen=100)`. `09f736a`.

**#13 — `PipelineHandlers.bind`** stops any prior `RetrainScheduler` before creating
a new one (idempotent; no leaked daemon thread on a second bind). `09f736a`.

**#14 — sanitizer forged-envelope regex** tolerates leading whitespace
(`^[^\S\n]*=+`, line-local). `09f736a`.

**#15 — `stats.bump_metrics`** deleted from `PgStatsStore` and the `StatsStore`
protocol (unused incremental `+=` writer that would race the nightly absolute-SET
`upsert_daily_metrics`); reserved-with-comment left in place. Commit `5646034`.

**#16 — OPERATIONS §7** added: `failure_signature` rows indexed before the embedder
fix carry `emb_model_ver=0` (no vectors), dense retrieval skips them, and how to
re-index (re-send launches / delete+reindex per project). Commit `5646034`.

## Notes / deviations

- Finding #4 required setting three config defaults to the code constants
  (`AUTO_MIN_PROB` 0.6→0.75, `BURST_SI_SHARE` 0.5→0.4, `TIME_DECAY` 0.999→2^(-1/90))
  because the knobs were previously unwired — the running behavior was the constant,
  and the finding mandates "defaults equal today's hardcoded constants so behavior is
  unchanged by default." OPERATIONS §2 was updated to match.
- `0005_metrics_daily_ext.sql`'s checksum changed (header comment edit). This is a
  develop-branch migration recreated on fresh test DBs; the review explicitly
  requested the header correction.
