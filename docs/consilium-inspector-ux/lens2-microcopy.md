# Lens 3 — Microcopy: Signature, Matching, Feedback cards (Round 2)

Exact strings for the remaining three Item Journey stage cards. Tone = the **approved
Round-1 revision** (mockup.html takeaways, not the pre-revision drafts): terse,
engineer-grade, key fact bold and first, expert terms visible inline in mono or muted
parentheses, thresholds always named (`τ_auto 0.75`), no hand-holding. Every string is
real payload data, derived from it, or an honest status — never invented.

Grounding: `inspector/static/js/views/journey.js` (`signatureCard()` l.144,
`matchingCard()` l.250, `feedbackCard()` l.407), `inspector/backend/payloads.py`
(journey payload fields), `inspector/backend/reconstruct.py` (live hybrid re-run),
`analyzer-ng-plan/specs/03-pipeline.md` §3 (signature/fingerprints), §5 (grouping),
§6.1–6.3 (stages), §6.4 (decay/src_weight), §6.7 (feedback),
`analyzer-ng-plan/specs/02-database.md` §5.1 (tsvector weights, RRF SQL),
`src/analyzer_ng/core/features.py` (`_SRC_WEIGHT = {rp: 1.0, human: 0.9,
ai_suggested: 0.3, seed: 0.6}`).

---

## 0. Voice rules (Round-2 revision — supersede Round 1 §0 where they differ)

- One-line L1 takeaway: **bold verdict/fact first**, then dash, then evidence; expert
  terms stay visible (`error_hash`, `RRF`, `τ_suggest`) — the audience is engineers.
  No "plain word first, expert term second" doubling in L1; that pairing lives in
  tooltips and L2 labels only.
- Mono expert key next to a plain label wherever a label appears:
  `burst signal (si_prior)` pattern, unchanged.
- Numbers: cosine 3 decimals, jaccard 2, RRF 4 (matches current `fmt()` calls);
  confidence 2; counts plain integers. Hashes shown full in mono (they are equality
  keys — truncation would break copy-grep).
- Missing value renders `—`, tooltip `not recorded for this item`. Never a filler
  sentence.
- Exactly one takeaway variant per card render; variants are checked in listed order,
  first match wins; a slot that cannot be filled forces the next (fallback) variant.

---

## 1. Signature card (stage 1)

### 1.1 Card frame

| Surface | String |
|---|---|
| Title | `Signature` |
| Subtitle | `fingerprints + field-weighted FTS doc (spec §3)` |
| Empty state | `No signature — no error logs survived filtering, so the builder had nothing to fingerprint or index (no failure_signature row).` |

### 1.2 L1 takeaway templates (first match wins)

Derivations: `{exc_root}` = first space-token of `signature.exc_text` (chain is
root-cause-first, spec §3.2); `{top_frame}` = `top_frames[0]`; `{template_count}` =
`len(template_ids)`; `{n_fields}` = count of non-empty among exc/msg/frames/templates/codes.

**T-S1 — full signature, embedded** (`exc_text` set, `top_frames` non-empty, `has_emb`)
> `**{exc_root}** at {top_frame} — fingerprinted (exception_fp, error_hash), indexed as {n_fields} FTS fields · {template_count} templates; embedded (emb_model_ver {emb_model_ver}).`

**T-S2 — full signature, not embedded** (as T-S1 but `has_emb == false`)
> `**{exc_root}** at {top_frame} — fingerprinted (exception_fp, error_hash), indexed as {n_fields} FTS fields · {template_count} templates; **not embedded** — dense retrieval off, FTS-only.`

**T-S3 — exception without frames** (`exc_text` set, `top_frames` empty)
> `**{exc_root}** — no in-app frames (FRAMES omitted); exception_fp from classes only, {template_count} templates indexed{, embedded-suffix per T-S1/T-S2}.`

**T-S4 — no exception fingerprint** (`exception_fp == "0"`)
> `**No exception extracted** (exception_fp 0) — exact-hash matching (Stage A) disabled for this item; signature carries MSG + {template_count} templates{ + codes {status_codes_joined}}.`

Suffix rule: when `status_codes` is non-empty, T-S1..T-S3 append
`· codes {status_codes_joined}` (e.g. `· codes HTTP_503`).

### 1.3 Fingerprint chips (`fpChip`) — keep mono value, add real tooltips

| Chip | Tooltip |
|---|---|
| `exception_fp {value}` | `xxh3_64("class1\|class2#frame1\|frame2\|frame3") — exception chain root-cause-first (≤4 normalized classes) + top-3 in-app frames, signed 64-bit (spec §3.2). Same exception from the same code path ⇒ same fp. 0 = no classes and no frames → Stage A disabled (§3.4).` |
| `error_hash {value}` | `xxh3_64(exception_fp + "#" + ordered template_hashes) — fp plus the ordered Drain template sequence (spec §3.3). Equality-only key: drives Stage A inherit and launch grouping. One changed frame or template ⇒ a completely different hash.` |
| `emb_model_ver {v}` | `Version of the e5-small embedding model that vectorized signature_text. Dense retrieval only compares vectors with equal emb_model_ver.` |
| `● embedded` | `Embedding stored — dense (vector) leg of hybrid retrieval is active for this item.` |
| `○ not embedded` | `No embedding stored — retrieval falls back to lexical (FTS) only; dense rank will always be —.` |

### 1.4 Field badges — FTS doc with weights (EXC / MSG / FRAMES / TEMPLATES / CODES)

Badge text unchanged; add weight suffix in the badge tooltip. Weights are real:
`signature_tsv` setweights (02-database.md l.274) and `ts_rank_cd '{0.1,0.2,0.4,1.0}'`
(l.824).

| Badge | Tooltip |
|---|---|
| `EXC` | `Exception chain, root-cause first — tsvector weight A, rank weight 1.0 (highest). A lexical hit here dominates FTS ranking.` |
| `MSG` | `Cleaned primary-log message (last log with a stacktrace; first 8 lines, masked per §2.1) — weight B, rank 0.4. Truncated first when the doc exceeds the ~512-token / 2000-char cap.` |
| `FRAMES` | `Top in-app stack frames pkg.Class.method (project namespace heuristic, §3.2) — weight C, rank 0.2.` |
| `TEMPLATES` | `Ordered Drain3 template_hash ids of all logs, max 30 — weight D, rank 0.1 (lowest for FTS), but the set drives jaccard_templates in retrieval and grouping.` |
| `CODES` | `Unmasked status codes (e.g. HTTP_503). Not part of the ranked tsvector — feeds the discriminant gate (status_codes_match_top1) that blocks look-alike hash inherits.` |

### 1.5 Drain template list

| Surface | String |
|---|---|
| Section header | `Drain3 templates referenced ({template_count})` |
| Header tooltip | `The masked log-line clusters whose ordered ids form the TEMPLATES field and the error_hash input. Highlighted tokens (<NUM>, <UUID>, …) are Drain masks — variables removed before hashing, so reworded values still match.` |
| `#{template_id}` chip tooltip | `template_hash — Drain3 cluster id, stable across runs (§2.3). Grep key for this masked line.` |
| Per-template meta (keep) | `{token_count} tok · {match_count} matches` — tooltip: `token_count = tokens in the masked pattern; match_count = raw log lines this cluster has absorbed project-wide.` |
| Missing row (keep, reword) | `template row missing — id referenced by the signature but absent from log_template (evicted or not yet persisted).` |

### 1.6 L4 drawer extras (fields already in the payload, currently unshown)

`only_numbers`, `urls`, `paths`, `signature_text` — render in the `<details class=eng>`
drawer as raw mono. One shared tooltip: `Raw extraction side-channels stored on
failure_signature; not used for FTS ranking.`

---

## 2. Matching card (stage 3)

### 2.1 Card frame

| Surface | String |
|---|---|
| Title | `Matching` |
| Subtitle | `stage A → B → C cascade (spec §6.1–6.3)` |
| No-suggestion state | `Not matched yet — no suggestion row; the matcher has not run for this item (or it was never a failure).` |

Naming note (honest-labels fix): the pipeline spec calls hybrid retrieval **Stage C**
(§6.3) while the SQL in 02-database.md §5.1 is titled "Stage B: item-history hybrid" —
the UI standardizes on the **pipeline** letters (A exact hash, B KB modes, C hybrid
RRF) everywhere; the SQL's internal name stays only in the L4 drawer
(`HYBRID_RETRIEVAL v{n}`). The current `journey.js` header "Live Stage-B
reconstruction" is renamed accordingly (§2.5).

### 2.2 Stage chips (payload `matching.stage` → chip)

| `stage` | Chip | Tooltip |
|---|---|---|
| `A` | `A exact hash` | `Stage A — a labeled in-scope item with equal error_hash. Guards: ≥2 unanimous matches, or 1 human-labeled with conf ≥ 0.9; newest ≤ 180 d; never inherits ti or auto-applied nd. Label inherited, confidence fixed 0.95, method hash (spec §6.1).` |
| `AB` | `B KB modes` | `Stage B — every kb_mode scored: score_mode = 0.6·cos(centroid) + 0.25·jaccard(templates) + 0.15·fp_overlap. Short-circuits only when confirmed ∧ purity ≥ 0.95 ∧ support ≥ 10 ∧ score ≥ 0.85; conf = min(0.93, purity·score), method kb. Below that it only feeds features (spec §6.2).` |
| `C` | `C hybrid RRF` | `Stage C — lexical FTS top-50 + dense cosine top-50 fused by RRF (1/(60+rank) each leg), ×1.1 launch boost, top-20 candidates → 46-feature vector → GBM (spec §6.3).` |
| `abstain` | `no match → abstained` | `A found no inheritable hash, B no short-circuit mode, C no candidate the model trusted — item left ti. The Decision card carries the abstain_reason.` |
| `none` | `not matched yet` | `No suggestion row exists for this item.` |

### 2.3 L1 takeaway templates

**T-M-A** (`stage == "A"`)
> `**Inherited via exact hash** from item {matched_item_id} — same error_hash, guards passed (≤ 180 d, human-trusted); conf fixed 0.95 (Stage A).`

**T-M-B** (`stage == "AB"`, `matched_mode` present)
> `**Matched KB mode** "{mode_title}" (#{matched_mode_id}) — purity {purity}, support {support}{, seed:{seed_key}} (Stage B; conf cap 0.93).`

**T-M-B-fallback** (`stage == "AB"`, `matched_mode` row missing)
> `**Matched KB mode** #{matched_mode_id} — mode row not found in failure_mode (retired?); score details unavailable (Stage B).`

**T-M-C** (`stage == "C"`)
> `**No exact hash, no KB short-circuit** — went to hybrid retrieval: FTS + cosine fused by RRF, top-20 → GBM scored the evidence (Stage C).`

**T-M-abstain** (`stage == "abstain"`)
> `**Nothing matched** — no inheritable hash (A), no confident mode (B), no trusted candidate (C); item stays ti. Reason on the Decision card.`

**T-M-none** (`stage == "none"`) — card body = no-suggestion state line (§2.1), no takeaway.

### 2.4 Matched-mode chip row (keep, add tooltips)

| Chip | Tooltip |
|---|---|
| `purity {0.00}` | `Share of this mode's members carrying its label — 1.00 = unanimous. ≥ 0.95 required to short-circuit.` |
| `support {n}` | `Confirmed members of this mode. ≥ 10 required to short-circuit.` |
| `seed:{seed_key}` | `Shipped seed catalog rule (spec §9) — this mode existed before any project data; regex/keyword rule hits add seed features.` |
| `matched item {id}` | `The Stage-A source item — its human label was inherited. Opens in RP.` |

### 2.5 Live hybrid reconstruction (renamed from "Live Stage-B reconstruction")

| Surface | String |
|---|---|
| Section header | `Live retrieval re-run (Stage C, HYBRID_RETRIEVAL v{hybrid_retrieval_version})` |
| Note template (with embedding) | `Re-executed the analyzer's versioned hybrid RRF SQL against current data — ranks are today's, not the ranks at decision time.` |
| Note suffix (no embedding) | `Dense leg inactive — this item has no embedding (emb_model_ver 0); ranking is lexical-only.` |
| Empty state | `0 candidates from HYBRID_RETRIEVAL v{hybrid_retrieval_version} — no lexical or dense hit in scope against current data (too few comparable signatures).` |
| `this item` chip (is_self row) | tooltip: `The query item retrieved itself — proof the index sees it; excluded from candidate features.` |

### 2.6 Candidate table — column headers + tooltips

Headers stay terse; every header gets a title tooltip.

| Header | Tooltip |
|---|---|
| `lex rank` | `Position in the lexical leg (sparse_rank): websearch_to_tsquery over the weighted tsvector, ts_rank_cd weights EXC 1.0 · MSG 0.4 · FRAMES 0.2 · TEMPLATES 0.1. — = no lexical hit.` |
| `dense rank` | `Position in the vector leg (dense_rank): cosine over e5-small embeddings, same emb_model_ver only. — = candidate or query has no comparable vector.` |
| `cosine` | `Embedding cosine similarity, 0–1 — semantic closeness of the two signature docs. — = dense leg inactive for this pair.` |
| `jaccard` | `jaccard_templates — |∩|/|∪| of the two Drain template_hash sets, 0–1. Structural overlap: same masked log lines regardless of wording.` |
| `RRF fused` | `rrf_score = 1/(60+lex_rank) + 1/(60+dense_rank), missing leg → 0 (k = 60). Theoretical max 2/61 ≈ 0.0328. This column is the ranking key; the bar is scaled to the top row.` |
| `label` | `Candidate's current issue_type — the label Stage C evidence votes with, weighted by decay × src_weight (see Feedback card).` |
| (row detail) `lex_score` | `Raw ts_rank_cd value behind lex rank — comparable within this query only, never across items.` |
| (row detail) `same test case` | `Candidate shares this item's test_case_hash — the same test failing before. Feeds feature same_test_case_top1; strong history signal.` |
| (row detail) `same error_hash` / `same exception_fp` | `Equality of the candidate's hash with this item's — the {0,1} features same_error_hash_top1 / same_exception_fp_top1 when the candidate is top-1.` |

---

## 3. Feedback card (stage 5)

### 3.1 Card frame

| Surface | String |
|---|---|
| Title | `Feedback` |
| Subtitle | `label_event log — append-only (spec §6.7)` |
| Empty state | `No label events — never labeled or relabeled since ingest. Events append on RP defect updates (rp), UI accepts (human), auto-apply (ai_suggested), seed catalog (seed).` |

### 3.2 L1 takeaway templates

Derivations: `{event_count} = len(feedback)`; `{last_*}` from the newest event;
`{human_event_count}` = events with `source ∈ {rp, human}`.

**T-F1 — single event, first label** (`event_count == 1`, `old_label == null`)
> `**{new_label_name}** — labeled once ({source_key} · src_weight {src_weight}) · {ts_short}.`

**T-F2 — single event, relabel** (`event_count == 1`, `old_label` set)
> `**{old_label_name} → {new_label_name}** — relabeled via {source_key} (src_weight {src_weight}) · {ts_short}.`

**T-F3 — multiple events** (`event_count ≥ 2`)
> `{event_count} label events → current **{last_new_label_name}** (last: {last_source_key} · {last_ts_short}); {human_event_count} human — these train the model (src_weight ≥ 0.9).`

**T-F3b — multiple events, none human** (`event_count ≥ 2`, `human_event_count == 0`)
> `{event_count} label events → current **{last_new_label_name}** (last: {last_source_key} · {last_ts_short}); no human confirmation yet — evidence weight stays ≤ 0.6.`

### 3.3 Source chips (`label_event.source`) — chip + tooltip

Chip = mono key + plain qualifier, per the established `method-chip` pattern
(`<span class=k>rp</span> defect update`).

| `source` | Chip | Tooltip |
|---|---|---|
| `rp` | `rp · defect update` | `Label changed in the ReportPortal UI (defect_update AMQP) — human ground truth. src_weight 1.0: the strongest vote this item can cast as future retrieval evidence.` |
| `human` | `human · UI accept` | `A person accepted an analyzer suggestion in the UI. src_weight 0.9 — near-ground-truth; also scores suggestion outcomes (metrics_daily).` |
| `ai_suggested` | `ai_suggested · auto` | `The analyzer's own auto-applied label, unreviewed. src_weight 0.3 — counts weakly as evidence until a human confirms; auto-applied nd never propagates via Stage A.` |
| `seed` | `seed · catalog` | `Label from the shipped seed failure-mode catalog (spec §9), pre-project-data. src_weight 0.6.` |
| unknown | mono `{source}` | `source code not documented in the Inspector.` |

### 3.4 Event row strings

| Surface | String |
|---|---|
| `(new)` badge (old_label null) | tooltip: `First label event for this item — no prior label recorded.` |
| `→` between badges | tooltip: `old_label → new_label as stored on the event; the log is append-only, current label = newest event.` |
| Timestamp | keep `shortTime(ts)`; tooltip: full ISO `{ts}` plus `age {age_days} d · decay {decay_value}` |
| Decay/weight micro-line (per event, muted) | `evidence weight now: src_weight {src_weight} × decay {decay_value} = {effective_weight}` |
| Decay tooltip | `decay(d) = exp(−ln 2 · d / 90) — half-life 90 days (spec §6.4). An event's pull on future decisions is src_weight × decay(age); a year-old rp label (1.0) weighs ≈ 0.06.` |
| `suggestion_id` (L4 drawer) | tooltip: `The suggestion this event answered, if any — links feedback to the decision it confirmed or corrected.` |

Derived values `{age_days}`, `{decay_value}` (2 decimals), `{effective_weight}`
(2 decimals) are computed client-side from `ts` and the source table above —
deterministic arithmetic on payload data, allowed under the no-dummy-data rule.

---

## 4. Cross-card consistency

- Stage chip (§2.2) and Decision method chip (Round 1 §2.4) must always agree:
  `A exact hash ↔ hash`, `B KB modes ↔ kb`, `C hybrid RRF ↔ gbm`,
  `abstained ↔ rule_cold/no_confident_rule or gbm abstain`. Both derive from the same
  suggestion row; if they ever disagree, render both raw values in mono — never paper
  over.
- The Signature chips' `error_hash` / `exception_fp` tooltips (§1.3) are the canonical
  wording; Matching row details (§2.6) and Round-1 feature tooltips reference the same
  terms and must not re-define them differently.
- `src_weight` / `decay` wording (§3.3–3.4) is shared with Round-1 feature tooltips
  `src_weight_top1` / `recency_top1`; the value tables (1.0/0.9/0.6/0.3, half-life
  90 d) come from `features.py` `_SRC_WEIGHT` and spec §6.4 — single source of truth.

---

## 5. Slot reference — every `{slot}` and its real source

| Slot | Source (journey payload unless noted) | Format |
|---|---|---|
| `{exc_root}` | first space-token of `signature.exc_text` (chain is root-cause-first, §3.2) | mono |
| `{top_frame}` | `signature.top_frames[0]` | mono |
| `{n_fields}` | count of non-empty among exc/msg/frames/templates/codes | int |
| `{template_count}` | `len(signature.template_ids)` | int |
| `{status_codes_joined}` | `signature.status_codes` joined with space | mono |
| `{emb_model_ver}` | `signature.emb_model_ver` | int |
| `{exception_fp}` / `{error_hash}` | `signature.exception_fp` / `.error_hash` (stringified) | mono, full |
| `{matched_item_id}` | `matching.matched_item_id` (+ `matched_item_url` link) | mono |
| `{matched_mode_id}` | `matching.matched_mode_id` | mono |
| `{mode_title}` `{purity}` `{support}` `{seed_key}` | `matching.matched_mode.*` | text / 2-dec / int / mono |
| `{hybrid_retrieval_version}` | `reconstruction.hybrid_retrieval_version` | int |
| `{event_count}` | `len(feedback)` | int |
| `{old_label_name}` `{new_label_name}` | RP defects map (`setDefects`) long name for the event's locator; fallback base-group name | text |
| `{source_key}` / `{last_source_key}` | `feedback[i].source` | mono chip §3.3 |
| `{src_weight}` | static table §3.3 keyed by `source` | 1 decimal |
| `{ts_short}` / `{last_ts_short}` | `shortTime(feedback[i].ts)` | text |
| `{age_days}` `{decay_value}` `{effective_weight}` | derived from `ts` + `src_weight` (client arithmetic) | int / 2-dec / 2-dec |
| `{human_event_count}` | count of events with `source ∈ {rp, human}` | int |

Honesty constraints found while grounding: (1) the reconstruction is a **live re-run**
— every string in §2.5 says so; no string may imply these were the decision-time
candidates. (2) `matching.stage` is inferred in `payloads.py` from
`matched_item_id`/`matched_mode_id`/confidence, not stored — if `method` is added to
the payload (Round-1 §6 blocker), derive the chip from `method` instead and drop the
inference. (3) The "Stage B" name inside `reconstruct.py`/02-database §5.1 clashes
with pipeline Stage C; UI uses pipeline letters only (§2.1 note). (4) `lex_score`,
`same_test_case`, per-candidate `label_source` are already returned by
`reconstruct.py`/SQL but not rendered — §2.6 strings cover them so Lens 1/2 can
surface them without new backend work.
