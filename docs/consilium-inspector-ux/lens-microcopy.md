# Lens 3 — Microcopy for the Grouping and Decision cards

Exact strings for the Item Journey redesign. Audience is dual: a QA engineer with no ML
background reads the plain words; an ML engineer greps the expert term kept in
parentheses. Every string below is either a static label, or a **template with `{slots}`
that are filled only from real payload data** (sources in §6). No string invents facts.

Grounding: `inspector/static/js/views/journey.js` (current cards),
`inspector/backend/features_meta.py` (39-feature table),
`src/analyzer_ng/core/features.py` (46-feature schema, v4),
`src/analyzer_ng/core/decision.py` (methods, bands, abstain reasons, thresholds),
`src/analyzer_ng/core/grouping.py` (burst gate: new fingerprint, ≥ 5 members, > 40 % of
launch failures; `si_prior = min(0.9, 0.5 + share)`),
`inspector/backend/payloads.py` (journey payload fields).

---

## 0. Voice rules (apply to every string)

- Plain term first, expert term in parentheses after it, once per surface:
  "shared error ID (`fingerprint`)". Tooltips may repeat the expert term.
- Short sentences. Present tense. No idioms, no jokes, no cute metaphors —
  bilingual-friendly English that survives literal translation.
- Numbers: confidence-like values with 2 decimals (`0.82`); shares as whole percents
  (`43 %`); counts as plain integers. Thresholds always shown with their name:
  "0.75 (`tau_auto`)".
- Never say "probably", "likely", "seems" unless the pipeline itself is uncertain
  (abstain band). Uncertainty language is reserved for real uncertainty.
- Missing data renders `—` plus the honest tooltip "not recorded for this item";
  never a placeholder sentence.
- A takeaway sentence (§4, §5) is always assembled from one template variant whose
  every slot is fillable. If a slot cannot be filled, use the listed fallback variant —
  never a partially-filled sentence.

---

## 1. Grouping card

### 1.1 Card frame

| Surface | String |
|---|---|
| Card title | `Grouping` |
| Card subtitle | `who else failed like this in the same run (co-failure launch group, spec §5)` |
| Empty state (keep current, reworded) | `No group — grouping needs at least one failure with a comparable error ID (error_hash) in this launch. This item's failure was not comparable to any other.` |

### 1.2 Field labels and tooltips

Plain label replaces the raw key on screen; raw key stays visible in the tooltip (and as
a small mono suffix for grepping).

| Raw key | Plain label | Tooltip text |
|---|---|---|
| `group_id` | `group #{group_id}` | `Internal number of this failure group inside the launch (group_id). Only used to reference the group; the number itself carries no meaning.` |
| `fingerprint` | `shared error ID (fingerprint)` | `A compact numeric ID of the group's representative error after variable parts (numbers, IDs, timestamps) are masked (signed 64-bit hash of the masked error, = representative error_hash). Two failures with the same ID have the same error text. It is equal or not equal — it is not a similarity score.` |
| `member_count` | `failures with this error (members)` | `How many failed items in this launch landed in this group. 1 = this failure is alone. Many = one diagnosis can cover all of them.` |
| `dominant` | `burst (dominant)` | `On = this group is a burst: a previously unseen error that suddenly took a large share of this launch's failures (new fingerprint, at least 5 members, more than 40 % of failures). Off = normal group.` |
| `si_prior` | `burst signal (si_prior)` | `How strongly this burst pattern points at one shared infrastructure problem (System Issue prior, 0 to 0.9). Non-zero only for bursts: 0.5 + the group's share of launch failures, capped at 0.9. This is one input signal to the decision, not a final probability and not added to confidence.` |

Naming decision, recorded: **not** "outage likelihood boost". `si_prior` is not a
likelihood (it is a prior evidence score) and not a boost (it is one feature the model
weighs, nothing is added to confidence). "Burst signal" is what it literally measures.

### 1.3 Dial label (replaces bare `si prior` under the gauge)

- Value line: `{si_prior}` (2 decimals, unchanged).
- Caption: `burst signal (si_prior)`
- Dial tooltip: `Scale 0 to 0.9 — the maximum the pipeline ever assigns (SI_PRIOR_CAP).`

### 1.4 Takeaway sentence templates (one per group shape)

Rendered as the first line of the card, before the fields. Pick exactly one:

**T-G1 — solo** (`member_count == 1`)
> `This failure is alone — no other failure in this launch shares its error. A cause specific to this one test is the working assumption (solo group, members = 1).`

**T-G2 — small group** (`member_count >= 2` and `dominant == false`)
> `{member_count} failures in this launch share this error. Diagnosing one of them should explain all {member_count} (shared-cause group).`

**T-G3 — burst** (`dominant == true`; requires launch failure count)
> `A new error suddenly hit {member_count} of {launch_failed_count} failures in this launch ({group_share_pct}). This pattern usually means shared infrastructure broke, so the analyzer leans toward System Issue (burst; si_prior = {si_prior}).`

**T-G3b — burst, fallback** (`dominant == true`, launch failure count not in payload)
> `A new error suddenly hit {member_count} failures at once in this launch. This pattern usually means shared infrastructure broke, so the analyzer leans toward System Issue (burst; si_prior = {si_prior}).`

---

## 2. Decision card

### 2.1 Card frame

| Surface | String |
|---|---|
| Card title | `Decision` |
| Card subtitle | `what the analyzer decided, how sure it was, and why (features & policy, spec §6)` |
| Empty state (keep current, reworded) | `No decision recorded — the analyzer has not scored this item yet (no suggestion row).` |

### 2.2 Confidence gauge

| Surface | String |
|---|---|
| Gauge value caption | `confidence` |
| Gauge tooltip | `How sure the analyzer is about its label, 0 to 1 (calibrated confidence). Below 0.45 it stays silent; from 0.45 it suggests to a human; from 0.75 it applies the label itself.` |
| Band mark at 0.45 | `suggest from 0.45 (tau_suggest)` |
| Band mark at 0.75 | `auto from 0.75 (tau_auto)` |

### 2.3 Band chips (replaces bare band badge)

| Code | Chip | Tooltip |
|---|---|---|
| `auto` | `applied automatically (auto)` | `Confidence {confidence} is at or above 0.75 (tau_auto). The label was applied without waiting for a human. A human can still correct it later.` |
| `suggest` | `suggested — needs a human (suggest)` | `Confidence {confidence} is between 0.45 (tau_suggest) and 0.75 (tau_auto). The label is shown as a suggestion; a human confirms or corrects it.` |
| `abstain` | `no answer — sent to To Investigate (abstain)` | `Confidence {confidence} is below 0.45 (tau_suggest), or a safety guard fired. The analyzer says "I do not know" instead of guessing. The item goes to To Investigate.` |

### 2.4 Method chips ("which mechanism decided")

Chip text is short; the explainer is the tooltip.

| Code | Chip | Tooltip |
|---|---|---|
| `hash` | `exact match (hash)` | `An identical failure (same error ID, error_hash) was seen before and labeled by a human. That label is inherited. Extra guards: the human label is recent (≤ 180 days), trusted (confidence ≥ 0.9), and the two failures still agree on unmasked details such as HTTP status codes and identifiers (discriminant gate). Confidence is fixed at 0.95 (Stage A).` |
| `kb` | `known failure mode (kb)` | `This failure matches an entry in the catalog of known, confirmed failure modes (knowledge base): match score ≥ 0.85, catalog entry ≥ 95 % label-pure with ≥ 10 confirmed members. Confidence is capped at 0.93.` |
| `gbm` | `learned model (gbm)` | `A trained model (gradient-boosted trees, LightGBM) weighed all {feature_total} evidence signals — similar past failures, this test's track record, the launch failure pattern, log contents — and produced a calibrated probability for each label.` |
| `rule_cold` | `starter rules (rule_cold)` | `No trained model is available yet (cold start). Simple safety rules decide: exact match, then catalog, then a pre-configured seed rule for this failure kind (seed prior, needs confidence ≥ 0.7); otherwise the analyzer abstains.` |

### 2.5 Abstain reasons (machine code → human sentence)

Shown only in the abstain band, directly under the band chip. Keep the code as a mono
suffix.

| Code | Human string |
|---|---|
| `no_confident_rule` | `No rule was confident enough: no identical labeled failure (hash), no catalog match (kb), and no trusted seed rule. With no trained model yet, the honest answer is "investigate" (no_confident_rule).` |
| `gbm_below_suggest` | `The model scored every label below the suggestion bar of 0.45 (tau_suggest). Best guess was {predicted_label_name} at {confidence}, which is too weak to show (gbm_below_suggest).` |
| `gbm_boilerplate_only_neighbor` | `The model's only support was a look-alike failure that shares no concrete evidence with this one — no matching error ID, no shared log templates, no shared identifiers, only generic error text. That is not real support, so the suggestion was withdrawn (gbm_boilerplate_only_neighbor).` |
| any unknown code | `The analyzer abstained for reason "{abstain_reason}" (code not yet documented in the Inspector).` |

Note for `gbm_below_suggest`: fill `{predicted_label_name}`/`{confidence}` from the
stored suggestion row; if the stored label is already `ti`, use the shorter variant:
`The model scored every label below the suggestion bar of 0.45 (tau_suggest) (gbm_below_suggest).`

### 2.6 Other decision fields

| Raw key | Plain label | Tooltip |
|---|---|---|
| `predicted` | `label` | `The defect label the analyzer chose: {predicted_label_name} ({predicted_label}). Base kinds: Product bug (pb), Automation bug (ab), System issue (si), No defect (nd), To investigate (ti).` |
| `outcome` | `what happened next` | see 2.7 |
| `model_ver` | `decided by version (model_ver)` | `Exact version of the model or rule set that made this decision. Lets you compare decisions made before and after a model update.` |
| `llm_used` (true) | badge `LLM judge consulted (llm_used)` | `A large language model was asked to double-check this decision before it was stored.` |
| `llm_used` (false) | `no` (muted, as today) | `Decided without consulting a large language model.` |
| `explanation` | (quoted line, as today) | `The analyzer's own stored explanation for this decision — written when the decision was made, not generated for this page.` |
| Feature section header | `Evidence the decision weighed ({feature_count} of {feature_total} signals, largest first) (feature vector)` | `Every signal the decision function received, with its stored value. Sorted by absolute value; zero-valued signals may be omitted from storage.` |

### 2.7 Outcome badges

| Code | Badge | Tooltip |
|---|---|---|
| `accepted` | `human agreed (accepted)` | `A person reviewed the suggestion and kept it.` |
| `corrected` | `human changed it (corrected)` | `A person reviewed the suggestion and picked a different label. This correction feeds future training.` |
| `ignored` | `no action taken (ignored)` | `Nobody acted on the suggestion before it expired or the item moved on.` |
| `pending` | `waiting for review (pending)` | `The suggestion is still open; no person has acted on it yet.` |

### 2.8 Feature group legend (waterfall color groups)

| Group code | Legend label |
|---|---|
| `retrieval` | `similar past failures (retrieval)` |
| `history` | `this test's track record (history)` |
| `kb` | `known failure modes (kb)` |
| `grouping` | `this launch's failure pattern (grouping)` |
| `signal` | `log contents (signal)` |
| `llm` | `LLM extractor (llm)` |
| `discriminant` | `exact-detail agreement (discriminant)` |
| `other` | `uncatalogued (other)` |

`llm` and `discriminant` are new legend entries: 7 of the 46 schema features are not in
`features_meta.FEATURE_DEFS` (it stops at 39) and currently render as
"(unknown feature — not in schema)". §3 provides their strings so the table can be
extended; until it is, the honest `other`/unknown rendering stays.

---

## 3. Feature tooltips — all 46 signals

Tooltip pattern: plain sentence, then `(key = definition)` for grepping. Plain label is
what the waterfall axis shows; the mono key stays in the tooltip line 1 (as today).

### Similar past failures (retrieval)

| # | Key | Plain label | Tooltip |
|---|---|---|---|
| 0 | `top1_cosine` | `best match similarity` | `How similar the single most similar past failure is, 0 to 1 (cosine of the best Stage-C candidate).` |
| 1 | `top1_rrf` | `best match combined rank` | `Combined text + meaning rank score of the best match (RRF fused score, post-boost, roughly 0 to 0.05).` |
| 2 | `top1_jaccard` | `shared log templates with best match` | `Overlap of masked log-line templates between this failure and the best match, 0 to 1 (template-set Jaccard).` |
| 3 | `margin_cos` | `lead over second-best match` | `Similarity gap between the best and the second-best match. A big gap means one clear winner (top1_cosine − top2_cosine).` |
| 4 | `mean_top5_cosine` | `average similarity, top 5` | `Average similarity of the five best matches (mean top-5 cosine).` |
| 5 | `n_candidates` | `how many matches found` | `Number of comparable past failures retrieved, scaled (count / 20).` |
| 6 | `label_hist_entropy` | `label disagreement among matches` | `Do the matches agree on a label? 0 = all agree, 1 = maximum disagreement (Shannon entropy of candidate labels / ln 4).` |
| 7 | `top1_label_frac` | `matches sharing best match's label` | `Fraction of all matches that carry the same label as the best match.` |
| 8 | `same_test_case_top1` | `best match is the same test` | `1 = the best match is a past failure of this very test case (same test_case_hash).` |
| 9 | `same_error_hash_top1` | `best match has identical error ID` | `1 = the best match has the exact same masked error (equal error_hash).` |
| 10 | `same_exception_fp_top1` | `best match has identical exception` | `1 = the best match throws the identical exception from the identical code path (equal exception_fp).` |
| 11 | `recency_top1` | `how recent the best match is` | `Freshness of the best match: near 1 = very recent, near 0 = old (time-decay of its age in days).` |
| 12 | `src_weight_top1` | `how trusted the best match's label is` | `Who labeled the best match: human defect update 1.0, human UI accept 0.9, seed 0.6, unreviewed AI 0.3 (label-source weight).` |

### This test's track record (history)

| # | Key | Plain label | Tooltip |
|---|---|---|---|
| 13 | `hist_pb` | `match evidence: Product bug` | `Trust- and recency-weighted share of match evidence pointing at Product bug (weighted candidate mass for pb).` |
| 14 | `hist_ab` | `match evidence: Automation bug` | `Same, pointing at Automation bug (weighted candidate mass for ab).` |
| 15 | `hist_si` | `match evidence: System issue` | `Same, pointing at System issue (weighted candidate mass for si).` |
| 16 | `hist_nd` | `match evidence: No defect` | `Same, pointing at No defect (weighted candidate mass for nd).` |
| 26 | `flakiness_score` | `test flakiness` | `How often this test flips outcome between consecutive runs, last 30 days. 0.5 also means "no history yet" (1 − p(same outcome as previous run); default 0.5).` |
| 27 | `test_fail_rate_30d` | `test fail rate, 30 days` | `Failures divided by runs for this test, last 30 days. 0.5 also means "no history yet".` |
| 28 | `flips_30d` | `pass/fail flips, 30 days` | `How many times this test flipped between pass and fail in 30 days, log-scaled (log1p(flips)/log1p(20)).` |
| 33 | `test_age_days` | `test age` | `How long this test has existed, log-scaled to 1 year (log1p(min(days,365))/log1p(365)).` |

### Known failure modes (kb)

| # | Key | Plain label | Tooltip |
|---|---|---|---|
| 17 | `kb_top1_score` | `best catalog match score` | `Match score against the best entry in the known-failure-mode catalog (score_mode, spec §6.2).` |
| 18 | `kb_purity` | `catalog entry label purity` | `How consistently members of that catalog entry carry one single label, 0 to 1 (mode purity).` |
| 19 | `kb_support` | `catalog entry size` | `How many confirmed members that catalog entry has, log-scaled (log1p(support)/log1p(1000)).` |
| 20 | `kb_same_fp` | `exception listed in catalog entry` | `1 = this failure's exact exception fingerprint appears in the catalog entry (exception_fp in mode fps).` |
| 21 | `kb_prior_conf` | `seed rule confidence` | `Confidence pre-configured on the matched seed rule (prior confidence of matched seed mode).` |
| 22 | `seed_pb` | `seed rule says: Product bug` | `1 = the matched seed rule is pre-labeled Product bug (one-hot seed prior = pb).` |
| 23 | `seed_ab` | `seed rule says: Automation bug` | `1 = the matched seed rule is pre-labeled Automation bug (one-hot seed prior = ab).` |
| 24 | `seed_si` | `seed rule says: System issue` | `1 = the matched seed rule is pre-labeled System issue (one-hot seed prior = si).` |
| 25 | `seed_nd` | `seed rule says: No defect` | `1 = the matched seed rule is pre-labeled No defect (one-hot seed prior = nd).` |

### This launch's failure pattern (grouping)

| # | Key | Plain label | Tooltip |
|---|---|---|---|
| 29 | `co_failure_group_size` | `group size` | `Size of this failure's group in the launch, log-scaled (log1p(size)/log1p(200)). Same group as the Grouping card.` |
| 30 | `group_dominance` | `group's share of failures` | `This group's share of all failures in the launch (group size / launch failures).` |
| 31 | `launch_fail_fraction` | `launch failure rate` | `Share of the whole launch that failed (launch failures / launch items). High = the run itself was broken.` |
| 32 | `si_prior` | `burst signal` | `Same value as the Grouping card's burst signal: strength of the "new error hit a big share of the launch" pattern (si_prior, 0 to 0.9).` |

### Log contents (signal)

| # | Key | Plain label | Tooltip |
|---|---|---|---|
| 34 | `item_log_count` | `error logs kept` | `How many error logs survived filtering for this item, scaled (logs / 20).` |
| 35 | `has_stacktrace` | `has a stack trace` | `1 = at least one log contains a stack trace (§1.3 flag).` |
| 36 | `is_assertion` | `is an assertion failure` | `1 = the failure is a test assertion (expected vs actual), not a runtime error (§3.4 flag).` |
| 37 | `is_merged_small_logs` | `logs were merged` | `1 = many tiny logs were merged into one document before analysis (§3.4 flag).` |
| 38 | `exception_count` | `distinct exceptions` | `How many distinct exceptions the logs contain, scaled (min(count,5)/5).` |

### LLM extractor (llm) — not yet in `features_meta.FEATURE_DEFS`

| # | Key | Plain label | Tooltip |
|---|---|---|---|
| 39 | `llm_failing_layer` | `LLM: which layer failed` | `Layer where the failure sits according to the LLM extractor, as a code; 0 = unknown or LLM off (spec 04 §4.2).` |
| 40 | `llm_error_class` | `LLM: error class` | `Error class according to the LLM extractor, as a code; 0 = unknown or LLM off (spec 04 §4.2).` |

### Exact-detail agreement (discriminant) — not yet in `features_meta.FEATURE_DEFS`

These exist because masking collapses details: an HTTP 500 and a 503, or the same
exception from two different app areas, can share one `error_hash`. These carry the
unmasked disagreement to the model (2026-07-18 errata).

| # | Key | Plain label | Tooltip |
|---|---|---|---|
| 41 | `status_codes_present` | `has status codes to compare` | `1 = this failure's text contains unmasked status codes (e.g. HTTP codes), so agreement can be checked at all.` |
| 42 | `status_codes_match_top1` | `status codes match best match` | `1 = this failure and the best match carry exactly the same set of status codes.` |
| 43 | `identifier_jaccard_top1` | `shared identifiers with best match` | `Overlap of identifier tokens (class names, endpoints, test names) with the best match, 0 to 1 (identifier-token Jaccard). 0 can mean "nothing to compare" — see "has identifiers to compare".` |
| 44 | `hash_gate_blocked` | `exact match found but rejected` | `1 = an identical error ID (error_hash) existed, but the safety gate rejected inheriting its label because unmasked details disagreed (status codes / identifiers). A warning sign for look-alike traps.` |
| 45 | `identifiers_present` | `has identifiers to compare` | `1 = this failure's message contains identifier tokens, so "shared identifiers" 0 means real disagreement, not missing data (v4, 2026-07-18b).` |

### Signature-card raw fields referenced by these tooltips

(The Signature card is stage 1, but its chips are the terms the tooltips above lean on;
give them matching tooltips.)

| Raw key | Tooltip |
|---|---|
| `error_hash` | `Compact ID of this failure's masked error text: log templates plus normalized stack frames, hashed (signed 64-bit). Identical failures get identical IDs; one changed frame changes the ID completely.` |
| `exception_fp` | `Compact ID of the exception itself: exception type plus normalized stack frames, hashed. Stricter than error_hash — same exception from the same code path.` |
| `test_case_hash` (`tch` chip) | `Compact ID of the test case, so "the same test failed before" can be checked without names (test_case_hash).` |
| `emb_model_ver` | `Version of the text-embedding model used to turn this failure into a vector for similarity search.` |
| `embedded / not embedded` | `Whether a meaning-vector (embedding) exists for this failure. Without it, only exact text search finds matches (no dense retrieval).` |

---

## 4. Takeaway sentence templates — Decision card

First line of the card. One sentence, then the band chip row. Variants by
`band × method`; `{predicted_label_name}` is the project's real defect-type name from
the RP defects map (fallback: base-group name from the fixed pb/ab/si/nd/ti vocabulary).

### auto

**T-D1 hash·auto**
> `Labeled {predicted_label_name} automatically: an identical failure was labeled by a human before, and this one inherits that label (exact match, hash; confidence {confidence} ≥ 0.75 tau_auto).`

**T-D2 kb·auto**
> `Labeled {predicted_label_name} automatically: this failure matches the known failure mode "{mode_title}" — {mode_support} confirmed cases, {mode_purity_pct} carrying this label (catalog match, kb; confidence {confidence}).`

**T-D2b kb·auto, fallback** (no mode row in payload)
> `Labeled {predicted_label_name} automatically: this failure matches a known failure mode in the catalog (kb; confidence {confidence} ≥ 0.75 tau_auto).`

**T-D3 gbm·auto**
> `Labeled {predicted_label_name} automatically: the model weighed all {feature_total} evidence signals and reached confidence {confidence}, above 0.75 (tau_auto) (learned model, gbm {model_ver}).`

**T-D4 rule_cold·auto**
> `Labeled {predicted_label_name} automatically: no trained model yet, but a pre-configured seed rule for this failure kind is trusted at {confidence}, above 0.75 (tau_auto) (starter rules, rule_cold).`

### suggest

**T-D5 gbm·suggest**
> `Suggests {predicted_label_name} at confidence {confidence} — enough to propose (≥ 0.45 tau_suggest) but not enough to act alone (< 0.75 tau_auto). A human confirms or corrects (learned model, gbm {model_ver}).`

**T-D6 rule_cold·suggest**
> `Suggests {predicted_label_name} at confidence {confidence}: a pre-configured seed rule points here, but not strongly enough to act alone (starter rules, rule_cold; ≥ 0.45 tau_suggest, < 0.75 tau_auto).`

**T-D7 generic·suggest** (defensive, any other method — today hash is fixed at 0.95 and kb ≥ 0.85, so both land in auto; keep this variant so a threshold change never renders a wrong sentence)
> `Suggests {predicted_label_name} at confidence {confidence} — a human confirms or corrects ({method}; ≥ 0.45 tau_suggest, < 0.75 tau_auto).`

### abstain

Template = fixed lead-in + the mapped reason sentence from §2.5.

**T-D8 abstain lead-in**
> `No label — the analyzer says "I do not know" instead of guessing, and the item goes to To Investigate (abstain).`

Then on the next line the §2.5 sentence for `{abstain_reason}`
(`no_confident_rule` → rule_cold path; `gbm_below_suggest`, `gbm_boilerplate_only_neighbor` → gbm path; unknown code → unknown-code fallback).

---

## 5. Cross-card takeaway consistency

When the Grouping card shows T-G3/T-G3b (burst) **and** the Decision card's predicted
base group is `si`, append to the Decision takeaway:

> `The burst in this launch (burst signal {si_prior}) supports this.`

Only when both facts are true in the payload (`grouping.dominant == true` and
`decision.predicted_group == 'si'`); never inferred otherwise.

---

## 6. Slot reference — every `{slot}`, its real data source

| Slot | Source (journey payload unless noted) | Format |
|---|---|---|
| `{group_id}` | `grouping.group_id` | integer |
| `{member_count}` | `grouping.member_count` | integer |
| `{si_prior}` | `grouping.si_prior` | 2 decimals |
| `{launch_failed_count}` | count of items in `api.items(project, launch_id)` (already fetched by the sidebar) | integer |
| `{group_share_pct}` | `member_count / launch_failed_count` | whole percent, e.g. `43 %` |
| `{confidence}` | `decision.confidence` | 2 decimals |
| `{predicted_label}` | `decision.predicted_label` | locator, mono |
| `{predicted_label_name}` | RP defects map (`setDefects`) long name for `decision.predicted_label`; fallback: base-group name (`Product bug` / `Automation bug` / `System issue` / `No defect` / `To investigate`) from `util.js` | text |
| `{feature_total}` | `decision.feature_total` | integer |
| `{feature_count}` | `decision.feature_count` | integer |
| `{model_ver}` | `decision.model_ver` | mono |
| `{method}` | suggestion `method` column (`hash`\|`kb`\|`gbm`\|`rule_cold`) — **not yet in the journey payload; must be added in `payloads.py`** | chip per §2.4 |
| `{abstain_reason}` | suggestion `abstain_reason` column — **not yet in the journey payload; must be added in `payloads.py`** | code, mono |
| `{mode_title}` | `matching.matched_mode.title` | text |
| `{mode_support}` | `matching.matched_mode.support` | integer |
| `{mode_purity_pct}` | `matching.matched_mode.purity` | whole percent |

Honesty constraints found while grounding (blockers for full rendering, not for this
lens): (1) `method` and `abstain_reason` are stored on `DecisionResult`
(`src/analyzer_ng/core/decision.py`) but the journey decision block in
`inspector/backend/payloads.py` does not expose them — the method chip and abstain
sentence can only render once those two fields are added to the payload; until then the
card must omit them, not guess. (2) `features_meta.FEATURE_DEFS` covers 39 of 46
schema features; the 7 strings in §3 (llm + discriminant groups) are written so the
table can be extended; until then those features honestly render as unknown.
