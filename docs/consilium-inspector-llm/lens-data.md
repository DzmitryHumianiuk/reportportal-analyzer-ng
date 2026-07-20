# LENS 1 — Data truth: everything the Inspector can (and cannot) render about LLM involvement

Verified against the actual schema and code on this checkout, 2026-07-20:

- `src/analyzer_ng/db/migrations/0001_init.sql` (§2.11 `llm_cache`, §2.8 `suggestion`)
- `src/analyzer_ng/db/migrations/0004_llm.sql` (`llm_event`, `llm_role_state`)
- `src/analyzer_ng/llm/` (engine.py, manager.py, breaker.py, apply.py, wiring.py, eval.py, roles/*)
- `src/analyzer_ng/db/repositories/` (llm_events.py, llm_cache.py, suggestion_ops.py, llm_eval.py, retrieval.py)
- `src/analyzer_ng/api/http.py` (`/`, `/health`, `/metrics`), `inspector/backend/app.py` (`/api/analyzer-health`)
- Live examples: `demo-data/LLM-DEMO.md` (coldstart launch 181 / project `llm-demo`=6; explainer items 2942/2943, validation_fail 2941; honest-abstain 2959; judge = **0 events**, known suggest-path divergence)

Hard rule carried over from the UX rounds: **no dummy data**. Every field below is marked
either RENDERABLE (with the exact SQL) or NOT RENDERABLE (with the honest fallback wording).

Conventions: all SQL runs with `search_path = analyzer, public` (or prefix `analyzer.`).
`:project`, `:item`, `:launch` are bind parameters. Bands: `TAU_SUGGEST = 0.45`,
`TAU_AUTO = 0.75` (`core/decision.py:35-36`); judge window `[0.45, judge_tau=0.75)` with
≥2 candidates, suggest route only (`core/analysis.py:803-812`, config
`analyzer_llm_judge_tau`, default 0.75).

---

## 0. The source tables, column by column

### 0.1 `llm_event` (0004_llm.sql) — the per-call audit log. One row per role invocation.

| column | type | truth notes |
|---|---|---|
| `event_id` | bigint identity | stable sort tiebreaker |
| `project_id`, `item_id` | bigint | `item_id` is the RP test item id; **no `launch_id` column** — per-launch views must join `test_item` (or `suggestion`) on `(project_id, item_id)` |
| `role` | text | CHECK: `explainer|extractor|judge|coldstart` |
| `model` | text | exact tag, e.g. `qwen3:4b-q4_K_M` |
| `prompt_hash` | char(64) | sha256 of the role's *content key* (engine.py:83), NOT of the rendered prompt |
| `cache_hit` | boolean | cache hits DO get an event row: `outcome='ok'`, `cache_hit=true`, `latency_ms=0` (engine.py:87-89). Exclude them from latency stats. |
| `outcome` | text | CHECK allows `ok|schema_fail|validation_fail|timeout|breaker_open|dropped` — but the engine only ever writes the first five (engine.py:97-148). **`dropped` is schema-reserved and never written**: queue overflow is metrics-only (`queue.py:87` → `analyzer_llm_dropped_total{reason="queue_full"}`), no DB row. Never render a `dropped` count from `llm_event` as if it were real. |
| `output` | jsonb | **NULL unless `outcome='ok'`** (engine.py:169). Per-role shapes in §A below. This means guardrail firings (`schema_fail`/`validation_fail`) are visible as events but the *rejected text is not stored* — by design. |
| `latency_ms` | integer | NULL for `breaker_open` (never called) and transport `timeout`; `0` for cache hits |
| `created_at` | timestamptz | index `llme_proj_role_idx (project_id, role, created_at)` — always filter by project+role first |

Semantics of each outcome (engine.py):
- `ok` — validated output, cached, applied.
- `schema_fail` — JSON parse / JSON-schema / tool-calls violation, after one retry (seed 7 then 8). Guardrail.
- `validation_fail` — passed schema, failed the role's `post_validate` (e.g. explainer quote not verbatim in corpus, coldstart rule/label disagreement, judge choice out of range). Guardrail. Live example: item 2941.
- `timeout` — transport failure; also increments the breaker.
- `breaker_open` — job completed instantly without calling Ollama because the breaker was open.

Two invocations that produce **no llm_event at all** (honest gaps, not failures):
1. Role/kill-switch/master-switch off, or sidecar unavailable → the job silently returns in `manager._process` before the engine runs (manager.py:178-192).
2. Facts gone (`fact_loader` returns None: item deleted, project no longer cold, <2 judge candidates, nothing to explain) → skip, no row (wiring.py:74-151).
So "no events" ≠ "no failures"; it can also mean "never attempted". Word empty states accordingly.

### 0.2 `llm_cache` (0001_init.sql §2.11) — per-project content cache

`project_id, cache_key char(64), role, template_hash bigint (extractor only), output jsonb,
model, hits int, created_at, last_hit_at`. PK `(project_id, cache_key)`; index
`llmc_tmpl_idx (project_id, role, template_hash)`.

- `cache_key = sha256(model || '|' || role || '|' || prompt_hash)` (engine.py:85). Joinable to
  `llm_event` **only if pgcrypto is available**:
  `encode(digest(e.model || '|' || e.role || '|' || e.prompt_hash, 'sha256'), 'hex') = c.cache_key`.
  Verify `SELECT 1 FROM pg_extension WHERE extname='pgcrypto'` before shipping that join; otherwise
  match extractor rows via `template_hash` and other roles via time/role proximity — or don't join at all.
- `hits` **is stored** — bumped by `get`/`get_fresh` (llm_cache.py:22-24, 40-44), i.e. role-call-time
  hits. **Not bumped** by the feature-time serving read `get_extractor_by_template`
  (llm_cache.py:50-71, explicitly "no hits bump"). So `hits` *undercounts* real reuse for the
  extractor: every GBM feature computation that consumed a cached extraction is invisible in `hits`.
  Caveat any "cache economics" tile with exactly this.
- Freshness is read-time: a stale row (older than the role TTL — explainer 90d, extractor 90d,
  judge 14d, coldstart 90d) stays in the table but misses. "Entries" therefore ≥ "servable entries";
  show both or say "incl. expired".

### 0.3 `llm_role_state` (0004_llm.sql) — per-project runtime kill-switch

`project_id, role, enabled bool DEFAULT true, reason text, stats jsonb, decided_at`. PK `(project_id, role)`.

- **Absence of a row means enabled** (`PgLlmRoleStateStore.is_enabled`, llm_events.py:72-79). A
  per-project role panel must render the 4 roles with "enabled (default — no state row)" when
  no row exists, never "unknown".
- The nightly eval (`llm/eval.py`) only ever writes `enabled=false` with
  `reason='auto_disabled_precision'`; it never auto-re-enables. Any `enabled=true` row or other
  `reason` (e.g. `'admin'`) is a human action.
- `stats` written by the eval = `{"n", "precision_llm", "precision_classical", "diff_lower", "gap"}`
  (eval.py:112-119) — render as the Wilson evidence for the disable ("LLM 0.61 vs classical 0.68
  over N=57; 95% lower bound of the difference −0.04 < 0").
- **Caveat:** the comparison source is implemented **only for coldstart**
  (`PgLlmComparisonSource.role_comparisons` → `_coldstart_comparisons`, llm_eval.py:29-66,
  comparing `model_ver LIKE 'rubric+%'` vs `'rule_cold%'` acceptance). Auto-disable rows for
  explainer/extractor/judge cannot exist yet from the eval path; if one appears it was manual.

### 0.4 `suggestion` (0001_init.sql §2.8) — where LLM output lands on the record

LLM-relevant columns: `llm_used bool DEFAULT false`, `explanation text` (explainer, async-filled),
`model_ver text` (`'rubric+qwen3:4b-q4_K_M'` marks coldstart provisional rows; classical rows carry
the GBM tag), `features jsonb` (carries `features['judge']`, `features['coldstart']`, and the GBM
feature vector incl. extractor-derived features 39/40 `llm_failing_layer`/`llm_error_class` —
`inspector/backend/features_meta.py:59-60`, code 0 = unknown/LLM off).

---

## A. PER-ITEM: which roles touched this item, and what each left behind

### A1. Role touch summary (the item's "LLM involvement" strip)

```sql
SELECT role, outcome, cache_hit, model, latency_ms, created_at, event_id,
       output                         -- NULL unless outcome='ok'
FROM   analyzer.llm_event
WHERE  project_id = :project AND item_id = :item
ORDER  BY created_at, event_id;
```

RENDERABLE: every attempt, its outcome, whether it was served from cache, and the validated
output when it succeeded. This is the ground truth for "the LLM judged / explained / extracted /
cold-started here" AND for "a guardrail fired here" (`schema_fail`/`validation_fail`/`timeout`/
`breaker_open` rows).

Availability caveats:
- Absence of a role's row = "not attempted or skipped" (see §0.1) — say "no <role> activity
  recorded", not "the <role> declined".
- No `launch_id` on the event — for a launch-scoped strip join
  `JOIN analyzer.test_item ti USING (project_id, item_id) WHERE ti.launch_id = :launch`.
- Rejected outputs are not stored; for 2941-style rows the honest L1 is:
  *"Explainer output rejected by validation — nothing persisted (that's the guard working)."*

### A2. Explainer — the persisted rationale

```sql
-- what the user actually sees on the suggestion
SELECT suggestion_id, explanation, llm_used, model_ver, created_at
FROM   analyzer.suggestion
WHERE  project_id = :project AND item_id = :item
       AND explanation IS NOT NULL AND explanation <> ''
ORDER  BY created_at DESC, suggestion_id DESC LIMIT 1;

-- the audited call behind it (quotes included)
SELECT output->>'explanation' AS explanation,
       output->'quoted_lines' AS quoted_lines,
       cache_hit, latency_ms, created_at
FROM   analyzer.llm_event
WHERE  project_id = :project AND item_id = :item
       AND role = 'explainer' AND outcome = 'ok'
ORDER  BY created_at DESC LIMIT 1;
```

RENDERABLE: `suggestion.explanation` (≤700 chars, schema-bounded), `llm_used=true`
(set by `set_explanation`, suggestion_ops.py:29-36), plus `quoted_lines` (≤2, each verified
verbatim-in-corpus by `post_validate` — a checkable trust claim worth surfacing: "quotes are
machine-verified verbatim log lines").

Caveats:
- `llm_used=true` alone does NOT identify the role — explainer, judge, and coldstart all set it.
  Disambiguate: explanation present → explainer; `features ? 'judge'` → judge;
  `model_ver LIKE 'rubric+%'` → coldstart. An item can have several.
- The explainer skips abstains and label `ti` (wiring.py:107-108) — absence on an abstained item
  is correct behavior, not a miss: *"No explanation — nothing was suggested to explain."*
- Live: items 2942/2943 (present), 2941 (`validation_fail`, nothing persisted).

### A3. Judge — verdict and the reorder it caused

Where the verdict is stored — **two places, verified**:
1. `llm_event.output` on the `ok` event: `{"choice": "candidate_2"|"none"|"abstain", "reason": "..."}`
   (judge.py schema). This is the **only** place an `abstain` verdict exists — `apply_judge`
   returns early on abstain (apply.py:72-73) and writes nothing to the suggestion.
2. `suggestion.features->'judge'` = `{"choice", "chosen_item_id", "model", "prompt_hash"}` +
   `llm_used=true`, merged onto the item's **latest** suggestion row (apply.py:78-88,
   suggestion_ops.py:38-68). Written only for `candidate_k` / `none` (`chosen_item_id` NULL for
   `none` = demote all). `predicted_label`/`confidence` are never changed — say so in the UI.

```sql
-- the persisted verdict on the record
SELECT suggestion_id, features->'judge' AS judge, matched_item_id, confidence, created_at
FROM   analyzer.suggestion
WHERE  project_id = :project AND item_id = :item AND features ? 'judge'
ORDER  BY created_at DESC, suggestion_id DESC LIMIT 1;

-- the full verdict incl. reason and abstains
SELECT output->>'choice' AS choice, output->>'reason' AS reason, latency_ms, created_at
FROM   analyzer.llm_event
WHERE  project_id = :project AND item_id = :item AND role='judge' AND outcome='ok'
ORDER  BY created_at DESC LIMIT 1;
```

What reorder it caused — **computed at serve time, not stored**: the suggest read path calls
`latest_judge` (retrieval.py:563-583, same `features->'judge'` within the 14-day judge TTL) and
promotes `chosen_item_id` to `resultPosition` 0. There is no "reorder log". The honest render is a
*derivation*: "judge chose item `{chosen_item_id}`; classical top match was
`{suggestion.matched_item_id}` → promoted / already top / all demoted (`none`)". Apply the same
14-day TTL when phrasing it as *active* ("this verdict currently reorders suggestions") vs
*historical* ("verdict expired, no longer applied").

Availability caveat (live stand): **`role='judge'` has zero events** — a real suggest-path
divergence (LLM-DEMO §3: analyze scores mid-band, suggest re-decides the same item at 0.167 with
`matched_item_id=NULL`). The UI must render this honestly, e.g.:
*"No judge events. The judge only fires on suggest-route decisions in [0.45, 0.75) with ≥2
candidates — no item has landed in that window on this install."* Do not hide the judge panel;
an always-empty panel here is the finding.

### A4. Extractor — structured facts and cache hits for the item's template set

Three renderable layers:

```sql
-- (a) the item's own extraction attempts + validated facts
SELECT outcome, cache_hit, latency_ms, created_at,
       output->>'root_exception' AS root_exception,
       output->'wrapper_chain'   AS wrapper_chain,
       output->>'failing_layer'  AS failing_layer,   -- test_code|app_code|infrastructure|environment
       output->>'error_class'    AS error_class,     -- assertion|timeout|connection|http_4xx|http_5xx|...
       output->'components'      AS components
FROM   analyzer.llm_event
WHERE  project_id = :project AND item_id = :item AND role = 'extractor'
ORDER  BY created_at DESC;

-- (b) what the GBM actually consumed (codes; 0 = unknown/LLM off)
--     features 39/40 of the stored vector on the item's suggestion row
--     (already surfaced by the Inspector's feature table, features_meta.py:59-60)

-- (c) the cache row serving this template set (needs template_hash, see caveat)
SELECT output, model, hits, created_at, last_hit_at,
       (created_at >= now() - interval '90 days') AS fresh
FROM   analyzer.llm_cache
WHERE  project_id = :project AND role = 'extractor' AND template_hash = :thash
ORDER  BY created_at DESC LIMIT 1;
```

Caveats:
- `:thash` is `xxhash64(exception_fp || '#' || sorted(template_ids) joined by '|')`
  (`extractor_template_hash`, roles/extractor.py:20-28) — **not computable in SQL**; the Inspector
  backend must compute it in Python from `failure_signature.exception_fp` + `template_ids`
  (both already read for the journey payload). Same function, same signed-64 hash as the writer.
- (a) vs (c) can disagree honestly: the event shows *this item's* call; the cache row is what
  *feature extraction* served (could be from a sibling item with the same template set). Layer (b)
  is the only ground truth for "what the model saw".
- `hits` on (c) undercounts (see §0.2) — caption: "role-call hits; feature-time reads not counted".
- Extractor writes nothing to `suggestion` and does not set `llm_used` (apply.py:198 comment) —
  an extractor-only item (like 2959) rightly shows `llm_used=false`. That IS the honest-abstain
  story: *"LLM extracted features only; the label came from the classical path."*

### A5. Cold-start — provisional AI suggestion rows

```sql
SELECT s.suggestion_id, s.predicted_label, s.confidence, s.model_ver, s.llm_used,
       s.features->'coldstart'->>'rule'       AS rubric_rule,       -- 'R1'..'R14'|'none'
       s.features->'coldstart'->>'confidence' AS rubric_confidence, -- 'low'|'med'|'high'
       s.created_at
FROM   analyzer.suggestion s
WHERE  s.project_id = :project AND s.item_id = :item
       AND s.model_ver LIKE 'rubric+%'
ORDER  BY s.created_at DESC;

-- the audited call: label + rubric rule + model's reason sentence
SELECT output->>'label' AS label, output->>'confidence' AS conf,
       output->>'rubric_rule_matched' AS rule, output->>'reason' AS reason
FROM   analyzer.llm_event
WHERE  project_id = :project AND item_id = :item AND role='coldstart' AND outcome='ok'
ORDER  BY created_at DESC LIMIT 1;
```

RENDERABLE provenance markers, all verified in `apply_coldstart`/`insert_coldstart`
(apply.py:91-117, suggestion_ops.py:70-101):
- `model_ver = 'rubric+' || model_tag` → `'rubric+qwen3:4b-q4_K_M'` (the marker the brief asks for);
- `llm_used = true`;
- `confidence ∈ {0.46, 0.55, 0.65}` (low/med/high map, coldstart.py:35) — always suggest-band,
  never ≥ τ_auto: render as "provisional — suggests, never auto-confirms";
- `features->'coldstart'` = `{rule, confidence}` — link the rule to its rubric text (R1–R14 table,
  coldstart.py:37-78) for the L2 drawer;
- `predicted_label` is the default locator of the rubric's base label (e.g. `si` → `si001`).
- The `reason` sentence lives **only** in `llm_event.output` (and the cache), not on the
  suggestion row — join (a)↔(b) via `(project_id, item_id, role='coldstart')`, latest ok.

`ai_suggested` / `methodName='llm_coldstart'` caveat: that flag is applied on the RP reply surface
(apply.py:21 `COLDSTART_METHOD_NAME`); the base schema has **no `method` column** —
`insert_coldstart` doesn't write one. The Inspector already probes optional columns
(`_suggestion_opt_cols`, payloads.py); when `method` is absent, derive "AI-suggested" from
`model_ver LIKE 'rubric+%'` — that is the durable provenance, and the derivation is honest.

Live: items 2932-2940 (project 6, launch 181), 12 ok coldstart events.

---

## B. PER-PROJECT

### B1. Kill-switch panel (`llm_role_state`)

```sql
SELECT r.role,
       COALESCE(s.enabled, true)                    AS enabled,
       s.reason, s.stats, s.decided_at,
       (s.project_id IS NULL)                       AS is_default   -- no row = default-on
FROM   (VALUES ('explainer'),('extractor'),('judge'),('coldstart')) AS r(role)
LEFT JOIN analyzer.llm_role_state s
       ON s.project_id = :project AND s.role = r.role;
```

RENDERABLE: enabled/disabled per role, who/why (`reason`), when (`decided_at`), and for
`auto_disabled_precision` the full Wilson evidence from `stats` (§0.3). Takeaway templates:
- default row: "coldstart — on (default; no state row)"
- auto: "judge — auto-disabled 2026-07-11: precision 0.61 vs 0.68 classical, N=57, 95% diff LB −0.04"
- manual: "explainer — disabled by admin 2026-07-12"

NOT RENDERABLE from the DB: the **install-level role flags** (`ANALYZER_LLM_EXPLAINER` etc.,
config.py:141-144) and the master switch — env-only, not persisted, not on `/health` (see §D).
A role can show "enabled (default)" here while being off install-wide. Honest wording:
*"Per-project switch state; install-level role flags are configuration and not visible to the
Inspector."* Corroborate indirectly: recent `llm_event` rows for a role prove it is actually on.

### B2. Role activity: counts, outcome mix, latency percentiles

```sql
-- outcome mix (uses llme_proj_role_idx)
SELECT role, outcome, count(*) AS n,
       count(*) FILTER (WHERE cache_hit) AS from_cache
FROM   analyzer.llm_event
WHERE  project_id = :project AND created_at >= now() - :window
GROUP  BY role, outcome
ORDER  BY role, outcome;

-- latency percentiles — real calls only (exclude cache hits' 0 ms and NULLs)
SELECT role,
       percentile_cont(0.5)  WITHIN GROUP (ORDER BY latency_ms) AS p50,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
       max(latency_ms) AS max, count(*) AS n
FROM   analyzer.llm_event
WHERE  project_id = :project AND NOT cache_hit AND latency_ms IS NOT NULL
       AND created_at >= now() - :window
GROUP  BY role;

-- guardrail tile: how often the guards fired
SELECT role,
       count(*) FILTER (WHERE outcome IN ('schema_fail','validation_fail')) AS guarded,
       count(*) FILTER (WHERE outcome = 'timeout')       AS timeouts,
       count(*) FILTER (WHERE outcome = 'breaker_open')  AS breaker_skips,
       count(*) FILTER (WHERE outcome = 'ok')            AS ok
FROM   analyzer.llm_event
WHERE  project_id = :project AND created_at >= now() - :window
GROUP  BY role;
```

Caveats: p50/p95 need a floor N (LLM-DEMO scale: ~10-100 events/role) — below ~20, show min/median/max
with the count, not "percentiles". `latency_ms` is server-side call latency (~8-13 s/item live);
don't imply user-facing latency — the roles are async and never block a decision.

### B3. LLM footprint on the record (project rollup, already partially in `/api/summary`)

```sql
SELECT count(*) FILTER (WHERE llm_used)                                       AS llm_touched,
       count(*) FILTER (WHERE explanation IS NOT NULL AND explanation <> '')  AS explained,
       count(*) FILTER (WHERE features ? 'judge')                             AS judge_annotated,
       count(*) FILTER (WHERE model_ver LIKE 'rubric+%')                      AS coldstart_provisional
FROM   analyzer.suggestion
WHERE  project_id = :project;
```

`summary()` (payloads.py:919) already counts `llm_used`; the three role-specific splits above are
the honest decomposition (they don't sum to `llm_touched` — roles overlap on one row).

---

## C. INSTALL-WIDE

### C1. Cache size & hit economics (`llm_cache`)

```sql
SELECT role, count(*) AS entries,
       count(*) FILTER (WHERE created_at >= now() - make_interval(days =>
           CASE role WHEN 'judge' THEN 14 ELSE 90 END))        AS servable,
       sum(hits) AS recorded_hits, max(last_hit_at) AS last_hit
FROM   analyzer.llm_cache
GROUP  BY role ORDER BY role;

-- physical size
SELECT pg_size_pretty(pg_total_relation_size('analyzer.llm_cache')) AS cache_size;

-- economics cross-check from the audit log (authoritative for role-call reuse)
SELECT role,
       count(*) FILTER (WHERE cache_hit)                          AS hits,
       count(*) FILTER (WHERE NOT cache_hit AND outcome='ok')     AS misses_computed,
       round(avg(latency_ms) FILTER (WHERE NOT cache_hit AND outcome='ok')) AS avg_cost_ms
FROM   analyzer.llm_event
GROUP  BY role;
```

Is a hit count stored? **Yes** — `llm_cache.hits`, bumped on `get`/`get_fresh` (§0.2). But prefer
the `llm_event.cache_hit` aggregation for the headline ("N calls avoided, ≈ N × avg_cost_ms of
inference saved"): it is per-event, windowable, and consistent with the rest of the panel.
Mandatory caveat on either number: **extractor feature-time serving reads bump nothing** — real
reuse is strictly higher than recorded. TTL constants (14/90) are code constants
(roles/*.py `ttl_days`), not DB data — keep them in one backend constant, cite spec 04 §3.0.

### C2. Circuit breaker — observability limits (the big honesty item)

The breaker (breaker.py) is **in-process, in-memory state**: closed → open on 3 consecutive
transport failures; open → half-open after 60 s cooldown (doubling to 15 min cap); never persisted,
reset on restart. `sidecar.health()` returns `{enabled, available, model, reason}` — and even that
is **not wired into any HTTP surface** (see §D).

What the DB honestly shows:
```sql
-- breaker footprints: jobs skipped while open, and the failures that opened it
SELECT date_trunc('hour', created_at) AS hour,
       count(*) FILTER (WHERE outcome = 'breaker_open') AS skipped_while_open,
       count(*) FILTER (WHERE outcome = 'timeout')      AS transport_failures
FROM   analyzer.llm_event
WHERE  created_at >= now() - interval '7 days'
GROUP  BY 1 HAVING count(*) FILTER (WHERE outcome IN ('breaker_open','timeout')) > 0
ORDER  BY 1;

-- freshness: last event per role (the only "is it alive" signal in the DB)
SELECT role, max(created_at) AS last_event
FROM   analyzer.llm_event GROUP BY role;
```

NOT RENDERABLE: current breaker state, current cooldown, open/close timestamps. Honest fallback
wording: *"Breaker state is in-process and not persisted. Shown instead: recorded `breaker_open`
skips, the transport failures around them, and last-activity times per role. A quiet log means
either healthy-and-idle or disabled — the Inspector cannot distinguish."*
Live truth does exist elsewhere: the analyzer's Prometheus `/metrics` exports
`analyzer_llm_breaker_state` (gauge) plus `analyzer_llm_breaker_open_total`,
`analyzer_llm_calls_total{role,outcome}`, `analyzer_llm_latency_ms{role}`,
`analyzer_llm_dropped_total{reason}`, `analyzer_llm_role_disabled{role}` (metrics.py:75-134) —
but the Inspector only proxies `/health` (`/api/analyzer-health`), not `/metrics`. If a live
breaker light is wanted, that's a small backend addition (proxy + parse the gauge), not a DB query
— until then, don't fake it.

### C3. Queue drops

NOT RENDERABLE from the DB at all: queue overflow increments
`analyzer_llm_dropped_total{reason="queue_full"}` only (queue.py:87); no `llm_event` row is written
(the reserved `dropped` outcome is unused). Fallback wording if a drops tile is wanted:
*"Queue drops are counted in Prometheus only; not visible here."* Better: omit the tile.

---

## D. What `/health` exposes about the LLM — verified: effectively nothing

- Analyzer `GET /health` (api/http.py:56-71) returns `live, ready, pg, amqp, emb_model_ver,
  gbm_model_ver, version, metrics` — the `metrics` summary is `metrics_daily` counters
  (suggestions/accepted/…, stats.py:145-172). **No `llm` key.** `LlmSidecar.health()`
  (`{enabled, available, model, reason∈{disabled,starting,unreachable,model_missing,None}}`,
  manager.py:195-201) exists precisely for this but is never surfaced by the `HealthProvider`
  wiring — the manager docstring even promises `GET /health`, so this is a genuine analyzer-side
  gap worth filing, not something the Inspector can paper over.
- Analyzer `GET /` — legacy shape, threads list; no LLM.
- Analyzer `GET /metrics` — the `analyzer_llm_*` series (§C2); not proxied by the Inspector.
- Inspector `GET /api/analyzer-health` (app.py:147-160) → `{configured, reachable, health:<body>}`;
  since the body has no `llm` key, today the Inspector **cannot show "LLM enabled/available/model"
  from health**. Honest fallback for the health strip:
  *"LLM sidecar status is not exposed on /health. Inferred from the audit log: last LLM event
  <time> (<role>, model <tag>)."* — using the `max(created_at)` query from §C2, with the model tag
  from the freshest event. If `llm_event` is empty install-wide: *"No LLM activity recorded —
  sidecar disabled, or enabled but never invoked; health does not say which."*

---

## E. Cannot-show summary (one table, with the approved-tone fallbacks)

| Fact | Why not | Honest fallback |
|---|---|---|
| Live breaker state (open/half-open/closed) | in-process only; not persisted, not proxied | `breaker_open`/`timeout` event history + last-event times; note restart resets |
| LLM enabled/available/model on health | `sidecar.health()` not wired into `/health` | infer from freshest `llm_event`; name the gap |
| Install-level role flags / master switch | env config, not in DB | show per-project switch table; caption the limitation |
| Judge verdicts on this stand | 0 events — suggest-path divergence (LLM-DEMO §3) | render the empty judge panel with the firing contract and "never fired on this install" |
| Judge `abstain` on the suggestion row | `apply_judge` no-ops on abstain | show it from `llm_event.output` only, labeled "verdict logged, record untouched" |
| Rejected (guard-failed) outputs | `output` NULL unless `ok` — by design | "output rejected by validation; nothing persisted" |
| Queue drops | metrics-only; `dropped` outcome never written | omit, or "Prometheus-only" |
| True extractor cache reuse | feature-time reads don't bump `hits` | show recorded hits with "undercounts feature-time reads" |
| Reorder as a stored fact | judge promotion is computed at read time | derive: chosen vs `matched_item_id`, within 14-day TTL |
| "Not attempted" vs "skipped" for a missing role event | silent skips write no row | "no <role> activity recorded" phrasing everywhere |
