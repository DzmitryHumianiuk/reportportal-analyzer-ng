# Lens 3 — Microcopy: LLM involvement (tab + journey strip)

Exact strings for surfacing the LLM sidecar (spec 04) in the Inspector: per-item
journey strip / LLM stage card, the per-project LLM tab, and tooltips for every raw
field. Tone = the approved Round-1/Round-2 voice (bold fact first, dash, evidence;
expert terms inline in mono; thresholds always named; no hand-holding). Every string
is real payload data, derived from it, or an honest static contract — never invented.

Grounding (verified in code, not from memory):
`src/analyzer_ng/db/migrations/0004_llm.sql` (llm_event, llm_role_state),
`0001_*.sql` §2.11 (llm_cache), `src/analyzer_ng/llm/engine.py` (outcome flow,
retry seed 7→8, cache key), `breaker.py` (3-strike open, 60 s→15 min cooldown),
`sanitizer.py` (control-token strip, U+2236 defang, nonce envelope), `eval.py`
(Wilson/MOVER kill-switch: N ≥ 50, gap 0.02, z 1.96), `apply.py` (row-update
invariants), `roles/{explainer,extractor,judge,coldstart}.py` (schemas +
post-validation), `core/analysis.py` l.790–812 (enqueue contract, `judge_tau`),
`core/decision.py` (`TAU_SUGGEST 0.45`, `TAU_AUTO 0.75`), `llm/queue.py`
(maxsize 500, `queue_full` drop), live state per `demo-data/LLM-DEMO.md`
(coldstart 12 ok, explainer 21 ok, extractor 105 ok, judge 0).

---

## 0. Voice + honesty rules for this surface

- Round-2 voice rules apply unchanged (bold verdict first, mono expert keys,
  numbers per `fmt()` conventions: latency integer ms or 1-decimal s ≥ 1000 ms,
  confidence 2 decimals, Wilson stats 3 decimals, counts plain integers).
- **The breaker is in-process state, never persisted.** No string may render a
  live breaker state chip ("breaker: closed") — the DB cannot know it. The UI
  shows only what `llm_event` proves: last-event age, `breaker_open` outcomes,
  and gaps. §5 has the exact wording.
- `llm_event.output` is NULL unless `outcome='ok'` (engine `_finish`). Any
  rejected call renders outcome + latency only — never a reconstructed output.
- A missing `llm_role_state` row means the DB default (`enabled=true`) and that
  the nightly eval has never flipped it — say "on (default)", not "healthy".
- One takeaway per render; variants checked in listed order, first match wins.

---

## 1. Per-item journey strip — "LLM" stage card

### 1.1 Card frame

| Surface | String |
|---|---|
| Title | `LLM sidecar` |
| Subtitle | `async roles, decision-path-neutral (spec 04)` |
| Strip chip (any events) | `LLM · {event_count}` |
| Strip chip (none) | `LLM · —` |

Data source: `llm_event` rows for this `item_id` + `suggestion.llm_used /
explanation / model_ver / features.judge / features.coldstart`.

### 1.2 L1 takeaway templates (first match wins)

**T-L1 — cold-start provisional label** (`suggestion.model_ver` starts `rubric+`)
> `**Cold-start rubric labeled this {label}** (rule {rubric_rule}, {conf_word} → conf {confidence}) — provisional ai_suggested, model_ver {model_ver}; suggest band by construction (< τ_auto 0.75), RP issue stays To Investigate until a human confirms.`

**T-L2 — explainer wrote the rationale** (`suggestion.explanation` non-empty ∧ an
`explainer` event with `outcome=ok`)
> `**Explainer wrote the rationale** ({model}, {latency_fmt}, schema-valid, quotes verified verbatim) — UX only; label and confidence untouched.`

Cache-hit variant (`cache_hit=true` on that event): replace `({model}, …)` with
`({model}, cache hit — no inference call)`.

**T-L3 — explainer rejected, nothing persisted** (`explainer` events exist, newest
non-ok, `explanation` empty)
> `**Explainer output rejected** ({outcome_key}, ×{attempt_note}) — nothing persisted: the match stands without a rationale rather than with an unverified one.`

`{attempt_note}` = `retried once (seed 7 → 8), then dropped` — static engine
contract (§3.0 step 6).

**T-L4 — judge annotated** (`suggestion.features.judge` present)
> `**Judge verdict: {judge_choice}** — {judge_effect}; label/confidence untouched (judge never crosses that line, spec 04 §4.3).`

`{judge_effect}` by `choice`: `candidate_k` → `chose item {chosen_item_id}, promoted
to position 0 on the suggest read path`; `none` → `demoted all candidates`.
(`abstain` writes nothing — an abstain shows only as an `ok` event with no
`features.judge`; then T-L5/T-L6 applies with the judge event visible in the table.)

**T-L5 — features only** (only `extractor` events, all other roles absent)
> `**LLM touched features only** — extractor cached structured facts ({failing_layer} / {error_class}) for the GBM; no role wrote to this suggestion. The label came from the classical path.`

**T-L6 — sidecar paused at call time** (newest event `outcome=breaker_open`)
> `**Sidecar was paused** — breaker open when this job ran ({ts_short}); completed instantly as breaker_open, no call made, nothing generated.`

**T-L7 — no LLM at all** (`event_count == 0`)
> `**No LLM on the label path** — zero llm_event rows for this item; the decision was purely rule/model.`

Suffix when `llm_used=false` and the item abstained (the safety story):
`Abstain came from the classical path — coldstart fires only in zero-history projects.`

### 1.3 Event table — per-row string

Row: `{ts_short} · {role_key} · {outcome_key} · {latency_fmt} · {cache_chip}`
where `{cache_chip}` = `cache` (only when `cache_hit`) and `{latency_fmt}` = `—`
when `latency_ms` is NULL (tooltip §3).

---

## 2. Per-project LLM tab

### 2.1 Tab frame

| Surface | String |
|---|---|
| Title | `LLM` |
| Subtitle | `llm_event · llm_cache · llm_role_state — read-only bookkeeping` |
| Model line | `model {model} — from llm_event rows; the Inspector reads the DB, not live sidecar health.` |
| Tab empty state | `No llm_event rows in this project — either ANALYZER_LLM_ENABLED=false on the analyzer, or no analyzed failure has reached an enqueue condition yet. Absence of events is the only signal this DB carries.` |

### 2.2 Role summary rows — L1 per role

**T-R1 — role active** (`ok_count > 0`)
> `**{role_key}: {ok_count} ok** · {rejected_count} rejected ({outcome_breakdown}) · p50 {p50_latency_fmt} · cache {cache_hit_count}/{event_count}`

**T-R2 — role with zero events, judge** (grounded in the real current state)
> `**judge: 0 events** — fires only on mid-band suggest decisions (τ_suggest 0.45 ≤ conf < τ_auto 0.75, ≥ 2 Stage-C candidates); none occurred. On this stand items land auto (≥ 0.75) or abstain (< 0.45).`

**T-R3 — role with zero events, generic** (any other role, `event_count == 0`)
> `**{role_key}: 0 events** — its trigger condition never fired in this project ({trigger_gloss}).`

`{trigger_gloss}` per role (static contract, `core/analysis.py` l.798–812):
`coldstart` → `classical abstain in a zero-history project`; `explainer` →
`decision conf ≥ τ_suggest 0.45`; `extractor` → `any analyzed failure` (a zero
here with other roles present would be a real anomaly — render the raw counts,
never paper over).

### 2.3 Kill-switch panel (`llm_role_state`) — L1 per role

**T-K1 — no row**
> `**on** (default — no llm_role_state row) — the nightly eval has never flipped this role here; it disables only when demonstrably worse than the classical path (N ≥ 50).`

**T-K2 — auto-disabled** (`enabled=false`, `reason='auto_disabled_precision'`)
> `**auto-disabled** {decided_short} — precision_llm {precision_llm} < classical {precision_classical} − 0.02 over N {n} gated cases; 95 % Wilson-MOVER lower bound of the difference {diff_lower} < 0. Stays off until an admin re-enables — the nightly job never auto-re-enables.`

**T-K3 — admin-disabled** (`enabled=false`, `reason='admin'`)
> `**disabled by admin** {decided_short}.`

**T-K4 — admin re-enabled** (`enabled=true`, row exists)
> `**on** (re-enabled {decided_short}, reason {reason_raw}) — subject to the next nightly eval window.`

Unknown `reason` value: render raw in mono, tooltip `reason code not documented in
the Inspector.`

### 2.4 Cache panel (`llm_cache`) — L1

> `**{entry_count} cached outputs**, {total_hits} hits — per-project dedup of inference calls (never served cross-project); extractor keys by template-set hash, so a novel failure shape costs one call.`

Empty: `No llm_cache rows — every call so far was novel or nothing has run.`

---

## 3. Tooltips — every raw field

### 3.1 `llm_event` columns

| Field | Tooltip |
|---|---|
| `role` | `Which sidecar role made this call: explainer, extractor, judge, coldstart. All four are async and decision-path-neutral — the analyzer never waits on the LLM (spec 04 §0/§1.5).` |
| `model` | `Exact model tag the call ran with (e.g. qwen3:4b-q4_K_M). Output was produced under constrained decoding (format schema) and re-validated client-side — the server-side constraint is never trusted alone (§3.0).` |
| `prompt_hash` | `sha256 of the role's content key (cache identity), NOT the literal prompt — the per-request nonce and envelope never enter the hash, so identical content always dedupes.` |
| `cache_hit` | `Served from llm_cache — no inference call was made; outcome is ok by definition and latency_ms is 0.` |
| `outcome` | `Final disposition of the call — see the per-value tooltips. Anything except ok persisted nothing.` |
| `output` | `The validated JSON the role produced. NULL unless outcome=ok — partial or rejected output is never stored (§3.0).` |
| `latency_ms` | `Wall time of the inference call. 0 = cache hit; — (NULL) = no call happened (breaker_open) or the transport failed before a response (timeout).` |
| `created_at` | `When the async worker finished the job — not when the item was analyzed; roles run behind a bounded queue.` |

### 3.2 `outcome` values

| Value | Chip | Tooltip |
|---|---|---|
| `ok` | `ok` | `Schema-valid AND role-guard-valid output — the only outcome that persists anything (cache row + suggestion write where the role has one).` |
| `schema_fail` | `schema_fail · rejected` | `Response was not the required JSON — parse failure, JSON-schema violation, or the model emitted tool_calls (any tool_call ⇒ reject, §5.3). Retried once (same prompt, seed 7 → 8), then dropped. Nothing persisted.` |
| `validation_fail` | `validation_fail · rejected` | `Output rejected by the role's grounding guard after passing the JSON schema — e.g. a quote not verbatim in the log corpus, an extracted exception class absent from the data, or a rubric label that contradicts its claimed rule. Retried once, then dropped — nothing persisted, never a hallucinated field.` |
| `timeout` | `timeout · transport` | `Transport-level failure — HTTP timeout, 5xx, connect error, or malformed body. Counts toward the circuit breaker (3 consecutive ⇒ open). Schema/validation failures do NOT count — the server was up, the output was bad.` |
| `breaker_open` | `breaker_open · skipped` | `The breaker was open when the job ran — completed instantly with no call. Opens after 3 consecutive transport failures; cooldown 60 s, doubling per re-open, capped at 15 min; the first job after cooldown is the half-open probe.` |
| `dropped` | `dropped · queue` | `Evicted from the bounded job queue (max 500) under backpressure before it could run — no call was made.` |

### 3.3 `llm_cache` columns

| Field | Tooltip |
|---|---|
| `cache_key` | `sha256(model ‖ role ‖ prompt_hash) — a model upgrade or role change never serves a stale shape.` |
| `project_id` | `Tenancy isolation: cache rows are keyed per project and never served cross-project.` |
| `template_hash` | `Extractor only: xxhash64(exception_fp ‖ sorted template_ids) — the feature-time lookup key. The GBM reads extractor facts by this hash, so one inference covers every item with the same template set.` |
| `hits` | `Times this row was served instead of a new inference call.` |
| `created_at` / `last_hit_at` | `Freshness is checked at read time against the role's TTL: 90 d for explainer/extractor/coldstart, 14 d for judge (candidate sets drift, §3.0). Stale rows are ignored, not deleted.` |
| `output.failing_layer` (extractor) | `Extractor's verdict on where the failure lives — enum test_code · app_code · infrastructure · environment. Feeds GBM features; enum-constrained, so no free-text drift.` |
| `output.error_class` (extractor) | `One of 13 coarse classes (assertion, timeout, connection, http_4xx, http_5xx, null_reference, not_found, permission, data_format, resource_exhausted, config, concurrency, other) — GBM feature input.` |
| `output.root_exception` / `wrapper_chain` (extractor) | `Exception classes copied from the log — post-validation rejects any name not verbatim in the sanitized corpus.` |

### 3.4 `llm_role_state` columns (kill-switch)

| Field | Tooltip |
|---|---|
| `enabled` | `Per-project runtime switch the nightly eval writes. Missing row = default on. The eval only ever writes false — re-enabling is an admin action.` |
| `reason` | `Why the row is in its state: auto_disabled_precision (nightly eval) or admin. Unknown codes render raw.` |
| `decided_at` | `When this state was last written — not when the underperformance started; the eval looks at a trailing 30-day window.` |
| `stats.n` | `Gated comparison cases in the 30-day window — paired items for the judge, gated LLM-arm cases for cold-start. Auto-disable requires N ≥ 50.` |
| `stats.precision_llm` / `stats.precision_classical` | `Each arm's precision against the label_event ground-truth stream over the window.` |
| `stats.gap` | `precision_llm − precision_classical. Auto-disable requires the LLM arm below classical by more than 0.02.` |
| `stats.diff_lower` | `95 % lower confidence bound of (precision_llm − precision_classical), Wilson intervals combined via MOVER (z 1.96). Independent-arms combination — conservative (wider), so the switch never fires on a narrower-than-real interval. Auto-disable requires this < 0.` |

### 3.5 `suggestion` LLM fields

| Field | Tooltip |
|---|---|
| `llm_used` | `An LLM role wrote to this suggestion (explanation, judge annotation, or the row itself for cold-start). false = the label path never touched the LLM.` |
| `explanation` | `Explainer's 2–3 sentence rationale (≤ 700 chars). Guards: every quoted substring verified verbatim against the sanitized log corpus; tamper phrases rejected. Empty after a rejected call — never a fallback text.` |
| `model_ver` = `rubric+{model}` | `Cold-start provisional row: the fixed 14-rule rubric applied by {model}. Confidence maps low → 0.46, med → 0.55, high → 0.65 — always inside the suggest band (< τ_auto 0.75), so it can never auto-apply.` |
| `features.coldstart.rule` | `The rubric rule the model claimed (R1–R14 or none). Post-validation enforces rule ↔ label agreement with the fixed table — a mismatched pair is a validation_fail, not a suggestion.` |
| `features.judge.choice` / `chosen_item_id` | `Judge verdict on the Stage-C top candidates: candidate_k (promote that item to position 0 on the suggest read path), none (demote all). It only reorders — predicted_label, confidence and band membership are untouchable (§4.3).` |
| `features.judge.prompt_hash` | `Links the annotation to its llm_event row — same hash, same call.` |

### 3.6 Prompt-hygiene drawer line (static contract, shown in the L4 `Technical
Details` drawer of the LLM card — policy, not per-item data, and labeled as such)

> `Prompt hygiene (code contract, sanitizer.py): log-derived text is sanitized before prompting — chat-template control tokens and [INST]/<<SYS>> markers → ⟨stripped⟩, role-line colons defanged to U+2236, forged envelope markers removed, C0 controls dropped — then wrapped in a per-request 8-hex-nonce envelope the data cannot forge. The nonce is not stored; this line documents policy, not this item's payload.`

---

## 4. Latency + count formatting

- `{latency_fmt}`: `< 1000 ms` → `{latency_ms} ms`; `≥ 1000` → 1-decimal `s`
  (`8.4 s`); `0` with `cache_hit` → `cache`; NULL → `—`.
- `{outcome_breakdown}`: comma list of non-ok outcomes with counts, mono, only
  those present (`2 validation_fail, 1 timeout`). Never render a zero.
- `{p50_latency_fmt}` computed over non-cache-hit ok events only — cache zeros
  would flatter the number; the tooltip on the p50 says so:
  `median inference latency, ok calls only, cache hits excluded.`

---

## 5. Breaker honesty — the only permitted liveness wording

The breaker (open/half-open/closed) lives in the analyzer process and is **not
persisted**. The Inspector must not claim to know it.

| Surface | String |
|---|---|
| Liveness line (events exist) | `last llm_event {last_event_ago} ago — breaker state is in-process on the analyzer and not persisted; liveness here is inferred from event flow only.` |
| Recent `breaker_open` run (≥ 1 in last hour of events) | `{breaker_open_count} breaker_open outcomes in the last hour of events — the sidecar was paused at those moments. Limits: opens after 3 consecutive transport failures; cooldown 60 s doubling to a 15 min cap; half-open probe success closes it.` |
| Long gap, no breaker_open evidence | `no llm_event for {last_event_ago} — could be no eligible items, roles off, or an open breaker; this DB cannot distinguish them.` |

---

## 6. Slot reference — every `{slot}` and its real source

| Slot | Source | Format |
|---|---|---|
| `{event_count}` `{ok_count}` `{rejected_count}` `{cache_hit_count}` | count over `llm_event` rows (item- or project-scoped) | int |
| `{role_key}` `{outcome_key}` | `llm_event.role` / `.outcome` raw | mono |
| `{model}` | `llm_event.model` (per event) or distinct over project | mono |
| `{latency_ms}` `{latency_fmt}` `{p50_latency_fmt}` | `llm_event.latency_ms` (§4 rules) | §4 |
| `{ts_short}` `{last_event_ago}` `{decided_short}` | `created_at` / `decided_at` via `shortTime()` / client age arithmetic | text |
| `{outcome_breakdown}` | group-count of non-ok `llm_event.outcome` | mono list |
| `{label}` `{confidence}` `{model_ver}` | `suggestion.predicted_label` / `.confidence` / `.model_ver` | mono / 2-dec / mono |
| `{conf_word}` `{rubric_rule}` | `suggestion.features.coldstart.confidence` / `.rule` | mono |
| `{judge_choice}` `{chosen_item_id}` | `suggestion.features.judge.choice` / `.chosen_item_id` | mono |
| `{failing_layer}` `{error_class}` | `llm_cache.output.*` for the extractor row keyed by this item's `template_hash` | mono |
| `{entry_count}` `{total_hits}` | `count(*)` / `sum(hits)` over `llm_cache` | int |
| `{n}` `{precision_llm}` `{precision_classical}` `{diff_lower}` | `llm_role_state.stats.*` | int / 3-dec |
| `{reason_raw}` | `llm_role_state.reason` raw | mono |
| `{trigger_gloss}` | static table §2.2 keyed by role | text |
| `{breaker_open_count}` | count of `outcome='breaker_open'` in window | int |

Constants quoted in copy (single source of truth, never restated differently):
`τ_suggest 0.45` / `τ_auto 0.75` (`core/decision.py`); judge window
`[0.45, 0.75)` + `≥ 2 candidates` (`core/analysis.py` l.803–812, `judge_tau`
defaults to `TAU_AUTO`); breaker `3 / 60 s / ×2 / 15 min` (`breaker.py`); retry
`seed 7 → 8, once` (`engine.py`); queue `500` (`queue.py`); kill-switch
`N ≥ 50, gap 0.02, z 1.96, 30 d window` (`eval.py`); TTL `90 d / judge 14 d`
(role classes); coldstart map `low 0.46 · med 0.55 · high 0.65`
(`roles/coldstart.py`).

---

## 7. Honesty constraints found while grounding

1. **`validation_fail` is the grounding guard, not the schema guard.** The brief's
   example gloss ("rejected by schema guard") would be wrong per `engine.py`:
   schema violations are `schema_fail`; `validation_fail` means schema-valid JSON
   rejected by role post-validation (verbatim-quote check, corpus membership,
   rubric agreement, candidate range). §3.2 wording keeps the brief's spirit
   ("rejected by guard — nothing persisted") with the correct guard named.
2. **Judge abstain leaves no suggestion trace** (`apply_judge` returns before any
   write) — only the `ok` llm_event exists. T-L4 therefore keys on
   `features.judge`, not on the event, and the abstain case falls through with the
   event still visible in the table.
3. **Breaker state must never be rendered as fact** — §5 is the only liveness
   vocabulary allowed; everything else would be dummy data.
4. **Judge zero-events copy (T-R2) is correct for the current stand** (0 rows,
   auto/abstain polarization per `demo-data/LLM-DEMO.md` case 3). The string is
   count-derived, so it self-corrects the day a judge event lands.
5. **`dropped` events may be under-observed**: `queue.py` signals queue_full via
   the metrics `on_drop` hook; whether a `dropped` llm_event row is written for
   the evicted job depends on the queue path, not the engine. The §3.2 tooltip
   describes the semantics; the tab must render whatever count the table actually
   has (including 0) and never synthesize one from metrics.
6. **Extractor facts for an item are found via `template_hash`**, not `item_id` —
   the T-L5 slot lookup mirrors `build_extractor_feature_lookup` (`wiring.py`),
   so the card shows exactly what the GBM saw, including a stale-TTL miss
   rendering as `—`.
