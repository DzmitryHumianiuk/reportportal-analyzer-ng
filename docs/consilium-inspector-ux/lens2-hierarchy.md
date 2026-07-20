# Lens 1 (Round 2) — Information hierarchy for the Signature, Matching, and Feedback cards

Scope: the three remaining stage cards of the Item Journey view —
**Signature** (`signatureCard`, `inspector/static/js/views/journey.js` l.144),
**Matching trace** (`matchingCard`, l.250, including the live Stage-B reconstruction
table), and **Feedback** (`feedbackCard`, l.407). Round 1 (Grouping + Decision) is
approved and defines the design language this lens extends without change:

- **L1** — one terse engineer-grade takeaway sentence: a total deterministic template
  over payload fields, expert terms visible in parentheses / mono. No hand-holding
  ("это инженеры").
- **L2** — the visual, with plain labels and units/thresholds printed.
- **L3** — hover: exact value + definition (existing tooltip pattern).
- **L4** — `<details class="eng">` "Details for engineers" drawer (`engDetails`,
  round-1 lens-disclosure §1.1): **every** raw field the card carries today or the
  payload silently drops. Nothing deleted, everything recedes.
- **No dummy data**: every rendered string is real data, a deterministic derivation of
  real data, or an honest status. Every fixed clause below is bound to an explicit
  predicate; first matching variant wins (total function of the payload).

Grounding sources (all statements trace to these):

- `inspector/static/js/views/journey.js` — current rendering.
- `inspector/backend/payloads.py` — journey payload: `signature` block (l.206:
  `exception_fp`, `error_hash`, `top_frames[]`, `template_ids[]`, `exc_text`,
  `msg_text`, `frames_text`, `tmpl_text`, `signature_text`, `only_numbers`,
  `status_codes[]`, `urls[]`, `paths[]`, `emb_model_ver`, `has_emb`); `templates[]`
  (l.223: `template_id`, `pattern`, `token_count`, `example`, `match_count`,
  `first_seen`, `last_seen`, `missing`); `_matching_decision` (l.375: `stage ∈ {A, AB,
  C, abstain, none}`, `stage_label`, `stage_note`, `matched_item_id`,
  `matched_mode_id`, `matched_mode{status,label,title,purity,support,seed_key}`);
  `feedback[]` (l.323: `event_id`, `old_label`, `new_label`, `old_group`, `new_group`,
  `source`, `suggestion_id`, `ts`; `ORDER BY ts DESC LIMIT 50`).
- `inspector/backend/reconstruct.py` — reconstruction block:
  `hybrid_retrieval_version`, `query{salient_terms, exc_text, template_ids,
  emb_model_ver, dense_active}`, `candidates[]` (`sparse_rank`, `dense_rank`,
  `lex_score`, `cosine`, `rrf_score`, `jaccard_templates`, `error_hash`,
  `exception_fp`, `issue_type`, `launch_id`, `launch_number`, `is_auto_analyzed`,
  `label_source`, `mode_id`, `is_self`), `note`.
- `src/analyzer_ng/db/repositories/queries.py` — the shipped hybrid SQL: FTS
  `ts_rank_cd('{0.1, 0.2, 0.4, 1.0}', …)` = weights `{D,C,B,A}` for
  tmpl/frames/msg/exc (l.49); RRF fusion `1/(60 + l_rank) + 1/(60 + d_rank)`
  (l.96–102); `ORDER BY f.rrf_score DESC, f.item_id DESC` (l.126).
- `src/analyzer_ng/db/migrations/0001_init.sql` l.186–189 — the tsvector:
  `exc_text → A`, `msg_text → B`, `frames_text → C`, `tmpl_text → D`.
- `analyzer-ng-plan/specs/03-pipeline.md` §3 (signature format, `exception_fp`,
  `error_hash`, edge case `exception_fp = 0` disables Stage A), §6.1–6.3 (stages),
  §6.7 (feedback); `analyzer-ng-plan/specs/02-database.md` §2.7 (`label_event.source
  CHECK IN ('rp_defect_update','analyzer_suggestion_accepted','human_ui')`), §5.1
  (the hybrid SQL the reconstruction re-runs).

---

## 1. What each card must answer, in order

**Signature** — reading order:
1. *What got indexed, and which retrieval abilities does this item have?* (L1: built or
   empty; embedded or lexical-only; exact-match capable or `exception_fp = 0`; codes?)
2. *The fields and their search weights; the masked templates.* (L2)
3. *Raw hashes, the verbatim FTS/embedding document, dropped columns.* (L4)

**Matching** — reading order:
1. *Which stage won — or did nothing match?* (L1)
2. *The evidence: matched item / mode vs its thresholds; the live candidate table with
   a visible "why this order".* (L2)
3. *Raw ids, the query the SQL actually received, per-candidate hashes.* (L4)

**Feedback** — reading order:
1. *Was this ever (re)labeled, how many times, and who acted last — human or a human
   accepting the analyzer?* (L1)
2. *The event timeline, oldest→newest, with plain actors and links back to the
   suggestion each event judged.* (L2)
3. *Raw event ids, raw source tokens, full timestamps, the query cap.* (L4)

---

## 2. Signature card

### 2.1 L1 takeaway — total deterministic template

Inputs: `s = d.signature`. Derived slots (all defined derivations of payload fields):

- `{ROOT}` = first whitespace-separated token of `s.exc_text` — the root cause, because
  spec §3.2 stores the chain root-cause-first. `{CHAIN}` = token count of `s.exc_text`.
- `{NF}` = `s.top_frames.length`; `{NT}` = `s.template_ids.length`;
  `{CODES}` = `s.status_codes.join(' ')`.
- `{EMB}` = `s.emb_model_ver` verbatim (int).

| # | Variant | Predicate (exact) | Sentence template |
|---|---|---|---|
| S0 | No signature | `d.signature == null` | Keep the existing empty state verbatim (already honest): "No signature — this item has no failure_signature row…" |
| S1 | Built | `d.signature != null` | "Indexed: {PARTS} — {EMB-CLAUSE}{FP-CLAUSE}." |

`{PARTS}` = ` · `-join of the present part-clauses, each gated by its own predicate
(order fixed; omit absent parts, never render an empty slot):

| Part | Predicate | Clause |
|---|---|---|
| exception | `s.exc_text` non-empty | `` `{ROOT}` `` and, iff `{CHAIN} > 1`, `+{CHAIN−1} chained` |
| frames | `NF > 0` | `{NF} frames` |
| templates | `NT > 0` | `{NT} templates` |
| codes | `s.status_codes.length > 0` | `codes {CODES}` |
| fallback | all four predicates false | `message text only (no exception, frames, or templates)` — reachable per spec §3.4 (MSG is the only always-present field) |

`{EMB-CLAUSE}` (exactly one):

| Predicate | Clause |
|---|---|
| `s.has_emb` | `embedded (emb_model_ver {EMB})` |
| `!s.has_emb` | `not embedded — dense retrieval leg inactive, lexical-only` (the exact consequence `reconstruct.py` l.91 computes as `dense_active`) |

`{FP-CLAUSE}` (appended only when its predicate holds):

| Predicate | Clause |
|---|---|
| `s.exception_fp == "0"` | `; exception_fp = 0 → exact match (stage A) disabled` — spec §3.4: hash too weak, Stage A skipped |
| otherwise | *(nothing — capability is the default, stating it every time is noise)* |

Examples of the assembled sentence (real-shaped, from the template):
"Indexed: `AssertionError` +1 chained · 3 frames · 7 templates · codes HTTP_503 —
embedded (emb_model_ver 614241)." /
"Indexed: message text only (no exception, frames, or templates) — not embedded —
dense retrieval leg inactive, lexical-only; exception_fp = 0 → exact match (stage A)
disabled."

### 2.2 L2 — fields with their real search weights

- **Field grid stays** (EXC / MSG / FRAMES / TEMPLATES / CODES with values), with two
  corrections:
  - Each FTS field badge carries its **tsvector weight, printed**: `EXC · A ×1.0`,
    `MSG · B ×0.4`, `FRAMES · C ×0.2`, `TEMPLATES · D ×0.1`. The letters are the real
    `setweight` labels (migration 0001 l.186–189); the multipliers are the real
    `ts_rank_cd` weight array (queries.py l.49; array order `{D,C,B,A}` =
    `{0.1, 0.2, 0.4, 1.0}`). Hover title: the source constant, e.g.
    `setweight(to_tsvector('simple', left(exc_text, 2000)), 'A') · ts_rank_cd weight 1.0`.
    These are schema constants fixed at migration time — safe to render as constants
    with a source comment in the view; if the SQL ever re-weights, the view constant
    must change with it (single source: lift them into one exported map).
  - **CODES is not in the tsvector** — it must not wear a weight. Its badge annotation
    is the honest role: `CODES · not searched — discriminant input` (status codes feed
    the exact-detail agreement features, round-1 microcopy §3 #41–42, not the FTS doc).
- **Capability chips replace the raw hash chips at the top** (hashes move to L4):
  - `embedded → dense + lexical retrieval` or `not embedded → lexical-only` (from
    `has_emb`; same predicate as the L1 clause).
  - `stage-A capable (exception_fp ≠ 0)` or `stage A disabled (exception_fp = 0)`.
- **Drain templates section stays** (mask highlighting is the best element of the
  current card), with the count line reworded to carry meaning: per template keep
  `#{template_id}` + pattern, and annotate `seen {match_count}× across the project ·
  {token_count} tokens`. The `missing: true` row's honest "template row missing" text
  stays verbatim.
- Drop the raw `TEMPLATES` value row from the field grid (a comma list of ids that is
  repeated, better, by the template section immediately below) — the ids remain in L4.

### 2.3 L4 — engineer drawer (`engDetails('signature', …)`)

Everything raw, verbatim, mono:

- `exception_fp` and `error_hash` — the full signed 64-bit values (currently the top
  chips; they recede, they do not disappear).
- `signature_text` — **the verbatim document that was FTS-indexed and embedded**.
  Present in the payload today (payloads.py l.215) and rendered nowhere; for an ML
  engineer this is the single most useful field on the card.
- `exc_text` / `msg_text` / `frames_text` / `tmpl_text` as stored columns (the exact
  strings the tsvector was built from, including truncation effects).
- `template_ids` raw list; per-template `example`, `first_seen`, `last_seen` (in the
  payload, currently dropped by the view).
- `only_numbers`, `urls[]`, `paths[]` — payload fields never rendered today; honest
  labels, e.g. `only_numbers = {value}` with the column name as the label.
- `emb_model_ver` raw; card-sub spec pointer "field-separated FTS doc + fingerprints
  (spec §3, 02 §2.4)" moves here.

### 2.4 Data-contract notes

None required — every slot above exists in today's payload. One consolidation: export
the FTS weight map (`{exc: ['A', 1.0], …}`) from one place (backend constant or a
generated JS map) so the badge annotations can never desync from the SQL.

---

## 3. Matching card

### 3.1 L1 takeaway — variants and derivation rules

Inputs: `m = d.matching`, `mm = m.matched_mode`, `dec = d.decision`
(`predicted_label` → mono locator). Evaluate in order; first match wins:

| # | Variant | Predicate (exact) | Sentence template |
|---|---|---|---|
| M0 | No trace | `m.has_suggestion == false` | Keep the existing honest text: "No suggestion recorded — no suggestion row exists for this item yet…" (payloads.py l.383). |
| MA | Exact match | `m.stage == "A"` | "**Exact match (stage A)**: `{dec.predicted_label}` inherited from item {matched_item_id} — identical `error_hash`, no ML involved." |
| MB | KB match, mode row present | `m.stage == "AB" && mm != null` | "**KB match (stage A/B)**: mode "{mm.title}" ({mm.status}) — label `{mm.label}`, purity {fmt(purity,2)}, support {support}{, seed `{seed_key}`}." (seed clause iff `seed_key != null`) |
| MB′ | KB match, mode row missing | `m.stage == "AB" && mm == null` | "**KB match (stage A/B)**: mode #{matched_mode_id} — mode row missing from failure_mode (deleted or re-clustered)." |
| MX | Abstained | `m.stage == "abstain"` | "**No stage matched** — no exact `error_hash`, no KB mode, retrieval not confident; item left `ti` (abstain)." |
| MC | Hybrid | `m.stage == "C"` | "**No exact or KB match** — decided on hybrid history retrieval scored by the GBM (stage C)." |

Rules:

- The brief's target sentence "Exact match: 2 human-labeled identical failures
  (stage A)" is **not renderable today**: the payload carries one `matched_item_id`,
  not the Stage-A guard evidence (match count, unanimity, label-event source, age —
  spec §6.1). MA above is the honest maximum; §3.4 defines the additive contract that
  upgrades it to "**Exact match (stage A)**: {n_hash_matches} identical failures
  ({n_human} human-labeled), unanimous `{label}` — inherited from newest, item
  {matched_item_id}." Until those fields exist, the count clause is omitted, never
  guessed.
- MC deliberately carries **no candidate count**: the stage-C candidate set at
  decision time is not persisted; the table below is a *re-execution against current
  data* (reconstruct.py docstring). Candidate numbers belong to the reconstruction
  headline (§3.2), which owns that caveat. Mixing them into MC would present today's
  retrieval as the decision's evidence — an invention.
- Stage-letter tokens (`stage A`, `stage A/B`, `stage C`) stay: expert terms in
  parentheses per the round-1 voice, greppable against spec §6.1–6.3.

### 3.2 Reconstruction sub-block — own L1 + the "why this order" table

The reconstruction is a distinct object (live re-run, not the stored trace) and gets
its own headline. Inputs: `r = d.reconstruction`, `n = r.candidates.length`,
`top = r.candidates[0]` (already RRF-ordered by the SQL), `V = r.hybrid_retrieval_version`.

| # | Predicate | Headline template |
|---|---|---|
| R0 | `n == 0` | Keep the existing empty state verbatim ("The versioned hybrid RRF SQL returned no rows…", already engineer-grade honest). |
| R1 | `n > 0 && r.query.dense_active` | "Re-ran hybrid retrieval v{V} against **current** data: {n} candidates — RRF fusion `1/(60+r_lex) + 1/(60+r_dense)`, top cosine {fmt(top.cosine,3)}." |
| R2 | `n > 0 && !r.query.dense_active` | "Re-ran hybrid retrieval v{V} against **current** data: {n} candidates — **lexical leg only** (no embedding, `emb_model_ver 0`), ranked by FTS rank alone." |

The RRF formula string is a constant copied from the shipped SQL (queries.py
l.96–102) — the literal answer to "why this order". Table changes (L2):

- **Leading `#` rank column** (1..n, the SQL's output order) so "order" is a visible
  fact, not an inference from bar widths.
- **RRF column hover** (L3) shows the per-row arithmetic, fully derived:
  `1/(60+{sparse_rank}) + 1/(60+{dense_rank}) = {fmt(rrf_score,4)}` (null rank →
  `0` term, matching the SQL's `COALESCE`). Footer line under the table, constant:
  "ordered by `rrf_score DESC, item_id DESC` (tie-break)" — the real `ORDER BY`
  (queries.py l.126).
- **New "label trust" cell** from `label_source` (in the payload today, dropped by the
  view): plain map `human → labeled by human`, `ai_suggested → auto-suggested`,
  `seed → seed rule`, null → `—`; raw token in the hover. This is the difference
  between a table of trustworthy neighbors and a hall of mirrors of the analyzer's
  own outputs — plus `is_auto_analyzed` as the existing halo convention.
- **Column-level state instead of cell noise**: when `!r.query.dense_active`, the
  `dense rank` and `cosine` headers render muted with one shared title "inactive —
  item has no embedding"; the per-cell `—` then reads as a column fact, not 20
  mysteries.
- `is_self` row keeps the "this item" chip (honest and useful — proves the query
  finds its own signature).
- **Drift check (derived, optional but free)**: when `dec.features` contains
  `top1_cosine` and the live top non-self candidate's cosine differs from it by
  > 0.005, render one muted line: "stored `top1_cosine` at decision time
  {fmt(stored,3)} vs {fmt(live,3)} now — retrieval has drifted since the decision."
  Both numbers are real payload data; the clause renders only when both exist.

### 3.3 L2 — mode panel with thresholds printed

When `mm != null`, the purity/support chips gain their decision thresholds (spec
§6.2 short-circuit: confirmed + `purity ≥ 0.95` + `support ≥ 10` +
`score_mode ≥ 0.85`): `purity {fmt(purity,2)} (short-circuit ≥ 0.95)`,
`support {support} (≥ 10)`, status chip `{mm.status}` verbatim. A number next to its
bar is information; a number alone is trivia. `score_mode` itself is **not** in the
payload — no chip pretends it is (§3.4).

### 3.4 L4 — engineer drawer (`engDetails('matching', …)`)

- `suggestion_id`, raw `stage` token, `matched_item_id` / `matched_mode_id` raw,
  `mode_id`, `seed_key`, `stage_note` verbatim.
- Reconstruction internals: `hybrid_retrieval_version`; the **query block** exactly as
  sent to the SQL — `salient_terms` (the real websearch_to_tsquery input),
  `template_ids`, `emb_model_ver`, `dense_active`; `r.note` verbatim.
- Per-candidate dropped fields: `lex_score` raw, `error_hash`, `exception_fp`,
  `launch_id`/`launch_number`, `mode_id`, raw `label_source` — as an expanded table
  twin (`table.data` in `.table-wrap`).

### 3.5 Data-contract notes (backend, additive)

1. **Stage-A guard evidence**: persist/expose `n_hash_matches`, `n_human_labeled`,
   `newest_match_age_days` from the §6.1 guard evaluation. Unlocks the brief's target
   sentence; until then MA renders without counts.
2. **`score_mode`** for the matched KB mode (spec §6.2) — the number the thresholds
   apply to; today only its factors (purity/support) surface.
3. `method` / `abstain_reason` — already specified in round-1 lens-hierarchy §4.5;
   the matching card benefits identically (MX can then append the mapped reason
   sentence from round-1 microcopy §2.5).

---

## 4. Feedback card

### 4.1 L1 takeaway — variants and derivation rules

Inputs: `F = d.feedback` (DESC by `ts`), `N = F.length`, `last = F[0]`,
`first = F[N−1]`. `{ACTOR(e)}` is a fixed total map over the DB CHECK values
(spec 02 §2.7):

| `source` | `{ACTOR}` clause |
|---|---|
| `rp_defect_update` | `human (RP defect edit)` |
| `human_ui` | `human (Inspector UI)` |
| `analyzer_suggestion_accepted` | `human accepted analyzer suggestion` |
| any other value | the raw token verbatim (honest fallback; the CHECK makes this unreachable today) |

The schema stores no user identity — "human" is the exact resolution the data
supports; never render a name or "someone on the team".

Variants, first match wins:

| # | Variant | Predicate (exact) | Sentence template |
|---|---|---|---|
| F0 | Never labeled | `N == 0` | Keep the existing empty state verbatim ("This item has never been (re)labeled…" — already the honest status). |
| F1a | Labeled once, fresh | `N == 1 && last.old_label == null` | "Labeled once: `{last.new_label}` set by {ACTOR(last)} — {shortTime(last.ts)}." |
| F1b | Labeled once, changed | `N == 1 && last.old_label != null && last.old_label != last.new_label` | "Relabeled once: `{last.old_label}` → `{last.new_label}` by {ACTOR(last)} — {shortTime(last.ts)}." |
| F1c | Labeled once, re-confirmed | `N == 1 && last.old_label == last.new_label` | "Label `{last.new_label}` re-confirmed by {ACTOR(last)} — {shortTime(last.ts)}." |
| FN | Labeled N times | `N >= 2` | "Labeled {N}×: {ORIGIN-CLAUSE}latest `{last.old_label ?? '(none)'}` → `{last.new_label}` by {ACTOR(last)}{CORRECTION-CLAUSE} — {shortTime(last.ts)}; first {shortTime(first.ts)}.{CAP-CLAUSE}" |

Bound clauses of FN:

| Clause | Predicate | Text |
|---|---|---|
| `{CORRECTION-CLAUSE}` | `last.old_label != null && last.old_label != last.new_label` | ` (correction)` |
| `{ORIGIN-CLAUSE}` | `last.suggestion_id != null && dec != null && last.old_label == dec.predicted_label` | `` analyzer's `{dec.predicted_label}` overridden — `` (the event demonstrably judged the stored suggestion: same id linkage, old label equals the prediction; anything weaker would be a guess) |
| `{CAP-CLAUSE}` | `N == 50` | ` Showing the last 50 events (query cap).` (payloads.py `LIMIT 50` — silent truncation becomes an honest status) |

This yields the brief's target shape when the data supports it, e.g.
"Labeled 2×: analyzer's `pb001` overridden — latest `pb001` → `ab001` by human
(RP defect edit) (correction) — 14:02; first 09:31." — every clause predicate-bound,
nothing about "auto" asserted unless the suggestion linkage proves it.

### 4.2 L2 — the timeline itself

- **Chronological, oldest → newest** (reverse of today's DESC render), because the
  `old → new` arrows inside each row point forward in time — today the rows' internal
  arrows and the list's implicit order run in opposite directions. Newest row gets the
  emphasis treatment (it is what L1 talks about).
- Each row keeps `old badge → new badge` (the strongest current element); `(new)`
  becomes `(first label)` — "(new)" collides with the *new_label* column name.
- Source chip renders the `{ACTOR}` plain form; raw token in the hover title
  (`rp_defect_update` etc. stay greppable).
- `suggestion_id`, when present, renders as a chip `suggestion {id}` — the explicit
  bridge to the Decision card's outcome badge (`corrected → "see Feedback"` already
  points here; the return pointer currently does not exist).
- Timestamps: keep `shortTime(ts)` mono; full ISO in the hover (and verbatim in L4).

### 4.3 L4 — engineer drawer (`engDetails('feedback', …)`)

- Table twin of the timeline: `event_id`, raw `source` token, `old_label`/`new_label`
  raw locators, `suggestion_id`, full ISO `ts`.
- Constant provenance line: "append-only `analyzer.label_event`, newest-first query,
  cap 50 (spec 02 §2.7, §6.7)".

### 4.4 Data-contract notes

None blocking. Optional: if RP's `defect_update` payload ever carries the acting
user, persist it — until a column exists, the actor stays source-level (§4.1) and the
UI must not sharpen it.

---

## 5. Anti-pattern audit of the current three cards

Format as round 1: what the card does today → why it fails → the fix above.

### Signature card

- **AP-S1 — Two 19-digit hashes lead the card.** The first row is
  `exception_fp` + `error_hash` chips (`fpChip`, journey.js l.155–160) — the least
  glanceable data on the card in the most glanceable position; the same failure as
  round-1 AP-G1, doubled. → capability chips in L2, raw hashes to L4 (§2.2, §2.3).
- **AP-S2 — No takeaway.** The card never states the one thing the stage exists to
  establish: *what this item can do downstream* (embedded? exact-match capable?). The
  `has_emb` glyph `● embedded / ○ not embedded` names a state, not its consequence
  (lexical-only retrieval, dense leg dead — reconstruct.py l.108–110 knows it; the
  card doesn't say it). → L1 S1 with `{EMB-CLAUSE}`/`{FP-CLAUSE}` (§2.1).
- **AP-S3 — The `exception_fp = 0` cliff is invisible.** Spec §3.4: `exception_fp = 0`
  disables Stage A entirely. The current card renders `exception_fp 0` as just
  another chip value — the reader cannot tell a degraded item from a normal one. →
  explicit L1 clause + L2 capability chip (§2.1, §2.2).
- **AP-S4 — Field badges without their weights.** The subtitle promises
  "field-separated FTS doc", but nothing says *why* it is field-separated: the
  A/B/C/D tsvector weights (×1.0/0.4/0.2/0.1) that make EXC dominate ranking exist
  only in migration SQL and `ts_rank_cd` (queries.py l.49). The letters, where an ML
  engineer meets them in the SQL, are unexplained; in the UI they are absent. →
  printed weight annotations sourced from the real constants (§2.2).
- **AP-S5 — Field badges wear defect-label colors.** `.fb-exc` is `--lbl-pb`,
  `.fb-msg` is `--lbl-si`, `.fb-frames` is `--lbl-nd`, `.fb-templates` is `--lbl-ab`
  (app.css l.185–189) — the exact hues the whole app reserves for Product bug /
  System issue / No defect / Automation bug now mean "exception text" and "message".
  A semantic collision with the app's strongest color convention. → neutral field
  palette (design-token change; flagged here, specified in the disclosure/microcopy
  lenses).
- **AP-S6 — CODES implies searchability it doesn't have.** The CODES badge sits as a
  peer of the four FTS fields, but `status_codes` is not in the tsvector — it is a
  discriminant-gate input. Rendering it as a fifth search field misstates the
  retrieval mechanics. → `not searched — discriminant input` annotation (§2.2).
- **AP-S7 — The indexed document itself is never shown.** `signature_text` — the
  exact string that was embedded and FTS-indexed, with its §3.1 truncation applied —
  rides the payload and is dropped by the view, along with `only_numbers`, `urls`,
  `paths`, and per-template `example`/`first_seen`/`last_seen`. Data silently
  discarded between backend and screen violates the "ML engineer loses nothing"
  contract. → all of it in the L4 drawer (§2.3).
- **AP-S8 — `template_ids` rendered twice, meaningfully zero times.** The TEMPLATES
  grid row prints raw ids; the section below prints the same ids with patterns. The
  duplicate row costs the space the weights annotation needs. → drop the grid row,
  ids stay in L4 (§2.2).

### Matching card

- **AP-M1 — Two names for the same mechanism, on one card.** The stage badge says
  "Stage C — hybrid retrieval + GBM" (spec 03 §6.3 taxonomy) while the table below is
  titled "Live Stage-B reconstruction" (spec 02 §5.1 / `STAGE_B_HYBRID_SQL` naming).
  Both letters label the *same* hybrid query; a reader who trusts the letters
  concludes these are two different stages. → one user-facing name ("hybrid
  retrieval, re-run live"); the B/C provenance letters move to the L4 drawer with
  their spec pointers (§3.2, §3.4).
- **AP-M2 — RRF table with no "why this order".** The fused score has a microbar and
  4 decimals, but the formula `1/(60+r_lex) + 1/(60+r_dense)`, the k=60 constant, and
  the `ORDER BY rrf_score DESC, item_id DESC` tie-break exist only in the SQL. The
  columns (lex rank, dense rank, RRF) are presented as unrelated facts; the table's
  entire point — *rank fusion* — is left as an exercise. → formula in the R1/R2
  headline, per-row arithmetic on hover, ORDER BY footer (§3.2).
- **AP-M3 — Live data wearing the stored trace's clothes.** The reconstruction
  renders inside the matching card with only a `note` paragraph separating "what the
  matcher saw at decision time" from "what this SQL returns now". The caveat exists
  (good, honest) but as a wall of text below a section title that says
  "reconstruction" only once; the candidate table itself carries no marker. → own
  headline with "**current** data" in the sentence and the drift-check line making
  then-vs-now a rendered fact, not a footnote (§3.2).
- **AP-M4 — Candidate label trust is dropped.** `label_source`
  (`human`/`ai_suggested`/`seed`) is in every candidate row of the payload and never
  rendered — yet Stage-A guards and the feature `src_weight_top1` exist precisely
  because an auto-suggested neighbor label is worth a third of a human one. The table
  shows *what* the neighbors are labeled but not *whether the label means anything*.
  → label-trust cell (§3.2).
- **AP-M5 — Thresholds absent from the mode panel.** `purity 0.87 · support 12` — the
  reader cannot tell whether this mode was one tick away from short-circuiting the
  whole pipeline (needs ≥ 0.95 / ≥ 10 / score ≥ 0.85, spec §6.2) or nowhere near. The
  numbers are rendered; their decision meaning is withheld. → thresholds printed next
  to each value (§3.3).
- **AP-M6 — `—` cells with no column-level cause.** When the item has no embedding,
  20 rows of `—` appear under `dense rank`/`cosine`; the reason (dense leg inactive)
  is stated once, in prose, in the note paragraph. Per-cell mystery for a
  single-cause column state. → muted column headers with one shared explanation +
  the R2 lexical-only headline (§3.2).
- **AP-M7 — Stage-A card cannot show its own guards.** Spec §6.1 inherits only under
  guards (≥ 2 unanimous or 1 trusted human, ≤ 180 days, no auto-`nd`), but the
  payload reduces the whole event to one `matched_item_id` — the UI can name the
  donor item and nothing about why inheriting was legal. Not a rendering bug; a
  payload gap that caps the card's honesty. → additive contract §3.5(1); sentence
  upgrades automatically.

### Feedback card

- **AP-F1 — Timeline without a takeaway.** The card is a bare list; "labeled twice,
  human corrected the analyzer" — the entire story — must be assembled by the reader
  from badge pairs and chip tokens. → L1 variants F0–FN (§4.1).
- **AP-F2 — Raw source tokens as UI text.** `rp_defect_update` /
  `analyzer_suggestion_accepted` render verbatim in chips; the reader must know the
  AMQP topology to answer "human or auto?". → fixed `{ACTOR}` map, raw token on
  hover (§4.1, §4.2).
- **AP-F3 — Reverse-chronological rows containing forward-pointing arrows.** The list
  is `ts DESC` (payloads.py l.328) with no order marker, while each row's `old → new`
  arrow points forward in time — reading top-to-bottom, the item appears to be
  relabeled *backwards*. → chronological order, newest emphasized (§4.2).
- **AP-F4 — "(new)" is ambiguous.** For `old_label == null` the row prints "(new)" —
  adjacent to a column literally named `new_label`. → "(first label)" (§4.2).
- **AP-F5 — The suggestion link is severed.** `suggestion_id` is in every event of
  the payload and never rendered — the one field that ties a correction to the exact
  decision it judged (and the Decision card's `corrected` outcome points at this card
  expecting the handshake). → suggestion chip in L2, raw id in L4 (§4.2, §4.3).
- **AP-F6 — Silent truncation at 50.** `LIMIT 50` with no indication; a heavily
  churned item shows exactly 50 rows and claims completeness. → `{CAP-CLAUSE}` in L1
  + provenance line in L4 (§4.1, §4.3).
