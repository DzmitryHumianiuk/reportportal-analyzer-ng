# Lens 1 — Information hierarchy for the QA persona

Scope: the **Grouping** card and the **Decision** ("Features & decision") card of the
Item Journey view (`inspector/static/js/views/journey.js`, `groupingCard` /
`decisionCard`). Audience is dual: a QA engineer with no ML background must get the
point in one glance; an ML engineer must lose **nothing** — every number stays on the
card, it just recedes.

Grounding sources (all statements below trace to these):

- `inspector/static/js/views/journey.js` — current rendering.
- `inspector/backend/payloads.py` — journey payload: `grouping.{group_id, fingerprint,
  member_count, dominant, si_prior}`; `decision.{predicted_label, predicted_group,
  confidence, band, tau_suggest=0.45, tau_auto=0.75, model_ver, llm_used, explanation,
  outcome, features[], feature_count, feature_total}`; `matching.stage ∈ {A, AB, C,
  abstain, none}`.
- `analyzer-ng-plan/specs/03-pipeline.md` §5 — grouping/burst:
  `si_prior = min(0.9, 0.5 + members/total)` fired only when the signature is **new**,
  `members ≥ N=5`, and `members/total > X=0.40`; otherwise `si_prior = 0`.
- `analyzer-ng-plan/specs/03-pipeline.md` §6.6 — decision policy: bands at
  τ_suggest=0.45 / τ_auto=0.75; every suggestion row stores
  `method ∈ {hash, kb, gbm, rule_cold}` and `abstain_reason`.
- `inspector/backend/features_meta.py` — 46 feature definitions with groups
  `{retrieval, history, kb, grouping, signal, other}`.

Hard project rule honored throughout: **every rendered string is real data, a
deterministic derivation of real data, or an honest status.** Every takeaway sentence
below is a fixed template whose slots are payload fields and whose fixed clauses are
bound to explicit predicates. No sentence can render unless its predicate is true of
the data; no adjective appears that is not tied to a predicate.

---

## 1. The three-level hierarchy (shared model for both cards)

| Level | Who it serves | What it is | Visual treatment |
|---|---|---|---|
| **L1 — Takeaway** | QA first, everyone | One sentence, derived by the rules in §3/§4. Answers "so what?" | Largest text on the card, `--ink`, sits directly under the card head. |
| **L2 — Key numbers** | QA + ML | The few numbers that justify L1, each with a plain-word label and its unit/denominator/threshold visible. | Normal card body. Plain-word labels; raw field name available on hover (`title=` attr) so ML users can map back to the schema. |
| **L3 — Expert detail** | ML | Raw identifiers, hashes, model version, the full feature vector. Nothing deleted — collapsed behind one disclosure per card, closed by default. | `<details>`-style "Raw / expert" drawer, `--muted` summary line, monospace inside. |

Shared derivation conventions:

- **Slot notation**: `{name}` is a payload field (or a defined function of one).
  Fixed text outside slots is constant per variant.
- **Rounding**: confidences and priors render with 2 decimals (`fmt(v, 2)`, as today);
  percentages as whole percent; counts verbatim.
- **Label names**: `{LABEL}` is the defect-type name resolved through the real RP
  defects mapping already loaded by `setDefects(d.rp.defects)` (falls back to the raw
  label code — an honest fallback, not an invention).
- **Nulls**: if a slot's field is null, that variant is unreachable by its predicate,
  or the variant defines an explicit fallback clause. Never render an empty slot.
- **Precedence**: variants are evaluated top-to-bottom in the order listed; the first
  matching predicate wins. This makes the sentence a total, deterministic function of
  the payload.
- **Interpretation clauses** ("likely an individual cause", "strong sign of a shared
  cause") are fixed strings attached to predicates and hedged ("likely", "sign of") —
  they state the documented meaning of the pipeline signal (spec §5/§6.6), never a
  per-item judgment invented by the UI.

---

## 2. What each card must answer, in order

**Grouping** — QA reading order:
1. *Is this failure part of something bigger in this launch, or alone?* (L1)
2. *How many failures share it, out of how many? Is it a burst?* (L2)
3. *Which group row / fingerprint exactly?* (L3)

**Decision** — QA reading order:
1. *What did the analyzer decide, and do I need to act?* (L1)
2. *How sure was it, against which thresholds, and which mechanism decided?* (L2)
3. *Exact model version and the full 46-feature input vector?* (L3)

---

## 3. Grouping card

### 3.1 L1 takeaway — variants and derivation rules

Inputs: `g = d.grouping` (`member_count`, `dominant`, `si_prior`), plus
`{T}` = total failing items in this launch. `{T}` is available today as
`item_count` in the launches payload (`payloads.py` line ~76) but **not** in the
journey payload — see §3.4 data-contract note. Until it is exposed, the share clause
(marked ⟨…⟩) is omitted rather than approximated: render the sentence without the
bracketed clause. Nothing else changes.

Evaluate in this order; first match wins:

| # | Variant | Predicate (exact) | Sentence template |
|---|---|---|---|
| G0 | No group | `d.grouping == null` | Keep the existing empty state verbatim (already an honest status): "No launch_group covers this item's error_hash in its launch…" |
| G1 | Solo | `g.member_count == 1` | "This failure is **alone in its launch** — no other failure shares its signature, so an individual cause (this test or its code path) is likely, not a shared outage." |
| G2 | Shared group | `g.member_count >= 2 && !g.dominant` | "This failure is **one of {member_count} in this launch with the same signature** — one diagnosis likely covers all {member_count}." |
| G3 | Dominant burst | `g.member_count >= 2 && g.dominant` | "A **new** failure signature just hit **{member_count} failures at once**⟨ — {round(100·member_count/T)}% of this launch's failures⟩ — a strong sign something shared broke (System-Issue prior {fmt(si_prior,2)} of max 0.9)." |

Notes binding every word of G3 to data:

- "**new**" is derivable, not decorative: spec §5 sets `dominant`/`si_prior > 0` only
  when `error_hash` was never seen before (`is_new`). `dominant == true` ⇒ the
  signature is new. If the burst rule ever changes to drop `is_new`, this word must be
  re-derived or dropped.
- "**strong**" is bound to the prior value by a fixed qualitative map (§3.2):
  `si_prior ≥ 0.7 → "strong"`, `0 < si_prior < 0.7 → "elevated"` (reachable only
  under non-default burst config, see §3.2), `si_prior == 0 → no burst clause at
  all`. G3's predicate (`dominant`) guarantees `si_prior > 0` under the spec; if a
  payload ever carries `dominant && si_prior == 0`, render G3 without the
  parenthetical prior clause (honest omission) rather than inventing a strength word.
- Degenerate guard: `dominant && member_count == 1` cannot occur under spec §5
  (burst needs ≥ 5 members). If such a row appears, render **G1** (solo wins) —
  never announce a one-failure "burst".

### 3.2 L2 — key numbers with plain-word labels

Replace the current raw `kv` list and bare dial with:

- **"Failures with this signature: {member_count}⟨ of {T} in this launch⟩"** —
  plain-word replacement for `members`. Hover title: `member_count`.
- **Burst / System-Issue prior**, shown **only when `si_prior > 0`**:
  - Label: **"System-Issue prior"** (never "si_prior"). Sub-label, constant string
    bound to spec §5: "a new signature covering a large share of one launch — prior
    evidence for a shared/infrastructure cause, capped at 0.9; a feature for the
    model, not a verdict."
  - Value: `{fmt(si_prior,2)} / 0.9` rendered as a **discrete banded bar with the
    scale end labeled 0.9**, not a naked ring. Qualitative word from the fixed map:
    `≥ 0.7 → strong`, `(0, 0.7) → elevated`.
  - Honesty note driving this design: with default config (`X = 0.40`), the spec
    formula `min(0.9, 0.5 + share)` with `share > 0.40` **always caps at 0.9** — the
    prior is effectively binary {0, 0.9}. A continuous gauge fabricates precision
    that the data does not have (see anti-pattern AP-G3). Intermediate values are
    reachable only if `X` is configured below 0.4, so the bar must show the cap, not
    pretend a smooth scale.
- **When `si_prior == 0` and not dominant**: one muted line, constant string:
  "No burst signal — this signature is not new to history, or it covers too small a
  share of the launch (burst needs ≥ 5 members and > 40% of failures)." (Thresholds
  are the real config defaults `N=5`, `X=0.40` from spec §5; if these become
  config-driven per install, the backend must supply them — do not hardcode
  silently-wrong numbers.)
- Drop the "dominant: no" row entirely — G1/G2 sentences plus the "no burst signal"
  line carry that information in words. Keep the 🔥 burst badge only in the G3 state,
  paired with the word "burst" (identity never color/emoji-alone, per app.css design
  rule).

### 3.3 L3 — expert drawer

Collapsed `details` titled **"Raw group identifiers"**, containing exactly what the
card shows today, unchanged and monospace:

- `group_id` = `{group_id}`
- `fingerprint` = `{fingerprint}` (signed 64-bit exception_fp; keep the sign — it is
  the real stored value)
- `si_prior` raw = `{si_prior}` (the un-rounded float, for ML users diffing against
  the feature vector's feature #32 `si_prior`)
- keep the card-sub spec pointer here: "co-failure launch group (spec §5)".

### 3.4 Data-contract note (backend, additive)

To render the ⟨share⟩ clause honestly, add to the journey grouping payload:
`launch_failed_total` (count of analyzed failing items in the item's launch — same
count the launches list already computes as `item_count`). Until then the share
clause is **omitted**, never estimated from `si_prior` (the cap at 0.9 destroys the
inverse mapping `share = si_prior − 0.5`, so back-deriving would fabricate data).

---

## 4. Decision card

### 4.1 Mechanism ("method") — derivation

The takeaway needs the deciding mechanism. Spec §6.6: every suggestion row stores
`method ∈ {hash, kb, gbm, rule_cold}`. The journey payload does not surface it yet;
until it does (preferred fix — expose `suggestion.method` and `abstain_reason`
verbatim), derive deterministically from existing fields, in this order:

1. `matching.stage == "A"` → `hash`
2. `matching.stage == "AB"` → `kb`
3. `decision.model_ver.split(";")[0] == "rule_cold"` → `rule_cold`
   (real observed format: `rule_cold;fs=2;emb=e5s-int8-r614241f6`)
4. otherwise → `gbm`

Fixed mechanism clauses (constant string per method; slots are payload fields):

| method | Clause |
|---|---|
| `hash` | "this failure is an exact match to item {matched_item_id}, which was already labeled {LABEL} by a human" |
| `kb` | "it matches the known failure mode "{matched_mode.title \|\| `mode ` + matched_mode_id}" from the catalog" |
| `gbm` | "the trained model weighed all the evidence for this failure" |
| `rule_cold` | "built-in rules decided — this project does not yet have enough human labels (< 50) to train a model" (the 50 is the real cold-start threshold from spec §6.5; source it from config/backend, same rule as §3.2) |

### 4.2 L1 takeaway — band × method matrix

Inputs: `dec = d.decision` (`band`, `confidence`, `tau_suggest`, `tau_auto`,
`predicted_label` → `{LABEL}`), method per §4.1, `{REASON}` = `abstain_reason`
verbatim once surfaced. `{C} = fmt(confidence, 2)`, `{TA} = tau_auto`,
`{TS} = tau_suggest`. Band is already a pure function of confidence in
`payloads.py` (`≥ 0.75 → auto`, `≥ 0.45 → suggest`, else `abstain`).

Variant D0 first, then the 3 × 4 matrix; predicates are mutually exclusive and total:

| # | Predicate | Sentence |
|---|---|---|
| D0 | `d.decision == null` | Keep existing empty state verbatim: "No decision recorded — no suggestion row exists for this item…" |
| auto × hash | `band=="auto" && method=="hash"` | "**Applied {LABEL} automatically** — this failure is an exact match to item {matched_item_id}, which was already labeled {LABEL} by a human (confidence {C}, at or above the {TA} auto threshold)." |
| auto × kb | `band=="auto" && method=="kb"` | "**Applied {LABEL} automatically** — it matches the known failure mode "{MODE}" from the catalog (confidence {C} ≥ {TA} auto threshold)." |
| auto × gbm | `band=="auto" && method=="gbm"` | "**Applied {LABEL} automatically** — the trained model weighed all the evidence and was confident enough to act without a human (confidence {C} ≥ {TA} auto threshold)." |
| auto × rule_cold | `band=="auto" && method=="rule_cold"` | "**Applied {LABEL} automatically by built-in rules** — this project does not yet have enough human labels to train a model (confidence {C} ≥ {TA} auto threshold)." |
| suggest × hash | `band=="suggest" && method=="hash"` | "**Suggests {LABEL} — a human should confirm.** It resembles item {matched_item_id}, already labeled {LABEL}, but confidence {C} is below the {TA} auto bar (suggest band {TS}–{TA})." |
| suggest × kb | `band=="suggest" && method=="kb"` | "**Suggests {LABEL} — a human should confirm.** It matches known failure mode "{MODE}", but confidence {C} is below the {TA} auto bar (suggest band {TS}–{TA})." |
| suggest × gbm | `band=="suggest" && method=="gbm"` | "**Suggests {LABEL} — a human should confirm.** The trained model leans this way but is not sure enough to act alone (confidence {C}, suggest band {TS}–{TA})." |
| suggest × rule_cold | `band=="suggest" && method=="rule_cold"` | "**Suggests {LABEL} — a human should confirm.** Built-in rules lean this way; the project has too few human labels for a trained model (confidence {C}, suggest band {TS}–{TA})." |
| abstain × hash | `band=="abstain" && method=="hash"` | "**No label applied — sent to To Investigate.** An exact-match candidate existed (item {matched_item_id}) but confidence {C} fell below the {TS} suggest threshold.{ Reason: {REASON}}" |
| abstain × kb | `band=="abstain" && method=="kb"` | "**No label applied — sent to To Investigate.** A catalog mode matched ("{MODE}") but confidence {C} fell below the {TS} suggest threshold.{ Reason: {REASON}}" |
| abstain × gbm | `band=="abstain" && method=="gbm"` | "**No label applied — sent to To Investigate.** The model was not sure enough to guess (confidence {C} < {TS} suggest threshold) — an honest "don't know", not a failure of the pipeline.{ Reason: {REASON}}" |
| abstain × rule_cold | `band=="abstain" && method=="rule_cold"` | "**No label applied — sent to To Investigate.** Built-in rules found no confident match, and the project has too few human labels for a trained model (confidence {C} < {TS}).{ Reason: {REASON}}" |

Rules:

- `{ Reason: {REASON}}` renders only when `abstain_reason` is non-empty — verbatim,
  never paraphrased. Until the backend surfaces it, the clause is omitted (the
  confidence-vs-threshold clause is already a complete honest explanation).
- In abstain variants, never present `predicted_label` as a decision (it may be
  `ti`); if `predicted_label` is a real class, it belongs in L2 as "model's leading
  guess: {LABEL}" — a factual statement about the stored row.
- "To Investigate" in the abstain sentences is resolved through the RP defects
  mapping like every `{LABEL}` (it is the real `ti` group name), keeping the
  RP-configured display name.
- The matrix is intentionally total: combinations the policy today makes rare
  (e.g. `abstain × kb`) still have defined sentences, so the UI can never be caught
  with data it refuses to describe.

### 4.3 L2 — key numbers

- **Confidence vs thresholds**: replace the unlabeled color gauge with a horizontal
  **banded bar**: fixed segments `0–{TS}` labeled "don't know", `{TS}–{TA}` labeled
  "suggest to human", `{TA}–1` labeled "auto-apply", threshold ticks annotated with
  their numbers (0.45 / 0.75 from the payload, never hardcoded in the view), and a
  marker at `{C}` with the number beside it. Same band colors as today
  (`--band-abstain/--band-suggest/--band-auto`) but always paired with the text
  labels (CVD rule from app.css header).
- **Decided by**: one chip with the plain-word mechanism —
  `hash → "exact match to a labeled failure"`, `kb → "known failure-mode catalog"`,
  `gbm → "trained model"`, `rule_cold → "built-in rules (untrained project)"`.
  Hover title shows the raw token (`hash|kb|gbm|rule_cold`) for ML users.
- **Predicted label badge** (as today) and, for abstain, "model's leading guess"
  framing per §4.2.
- **Outcome** (existing values, plain-word captions, deterministic map):
  `accepted → "a human accepted this decision"`,
  `corrected → "a human changed it — see Feedback (stage 5)"`,
  `ignored → "no human acted on it"`, `pending → "awaiting human review"`.
- **LLM judge**: keep, reworded: `llm_used → "an LLM judge reviewed this decision"`,
  else omit the row (a "no" row is noise; absence of the chip is the honest default).
- `dec.explanation`: keep, but drop the decorative curly quotes — it is machine
  output, not a human quotation; label it "analyzer's explanation".

### 4.4 L3 — expert drawer

Collapsed `details` titled **"Model internals — {feature_count} of {feature_total}
features"**:

- `model_ver` verbatim (moves out of the card subtitle — see AP-D2).
- The full feature waterfall exactly as implemented (`drawFeatureBars`: per-feature
  key, value, definition, range, default, group color from
  `FEATURE_GROUP_COLORS`), unchanged. Nothing is removed from it.
- **Honesty constraint on the section title**: today's "Feature vector (… sorted by
  magnitude)" must become "**Feature inputs the model saw** (sorted by |value|)".
  These are input values, not contributions — the payload has no SHAP/gain data, so
  the UI must never imply "these features *drove* the decision". If per-feature
  contributions are added later, an importance-sorted view becomes legitimate; until
  then, importance claims are inventions and therefore banned.
- Optional QA bridge (still fully derived): a one-line legend mapping the six
  feature groups to plain words — retrieval = "similarity to past failures",
  history = "this test's past labels", kb = "catalog match strength", grouping =
  "launch-burst signals", signal = "log/failure shape", other — sourced from
  `features_meta.py` groups, constant strings.

### 4.5 Data-contract notes (backend, additive)

1. Surface `suggestion.method` verbatim (spec §6.6 stores it) — removes the §4.1
   derivation heuristic.
2. Surface `suggestion.abstain_reason` verbatim — today the honest "why abstain"
   exists in the DB but never reaches the UI (see AP-D8).
3. Thresholds 0.45/0.75 already ride the payload (`tau_suggest`/`tau_auto`) — the
   view must keep reading them from there, so a config change can never desync the
   sentence from the gauge.

---

## 5. Anti-patterns in the current cards

Each entry: what the card does today → why it fails the dual audience → the fix
defined above.

### Grouping card

- **AP-G1 — Bare signed hash near the top.** `fingerprint` (a signed 64-bit int,
  often negative) is the second row of the kv list, peer to meaning-bearing fields.
  To QA a negative 19-digit number reads as garbage or an error; it answers no
  question at glance level. → L3 drawer (§3.3).
- **AP-G2 — Leaked variable name "si_prior".** An internal snake_case feature name
  used as a UI label, with no expansion anywhere on the card. → "System-Issue prior"
  with the one-line spec-bound sub-label (§3.2); raw name on hover.
- **AP-G3 — Unexplained, structurally dishonest dial.** The ring (a) has no visible
  scale — the 0.9 cap exists only inside `siDial`'s `v/0.9` math; (b) is always
  `--warning` amber, signaling caution even at 0.00; (c) renders a continuous gauge
  for a value that is binary {0, 0.9} under default config (§3.2), fabricating
  precision; (d) renders as a full empty ring when `si_prior == 0`, which reads as a
  broken widget. → banded bar with labeled cap, shown only when the prior fired;
  words otherwise (§3.2).
- **AP-G4 — "dominant: no" and unexplained "🔥 burst".** A negative boolean row is
  noise; the positive case shows an emoji + jargon word with no definition. → drop
  the row; burst meaning lives in the G3 sentence and the badge caption (§3.2).
- **AP-G5 — Count without denominator.** "members: 5" — five of what, out of how
  many? The launch share is the entire point of the signal. → "{member_count}⟨ of
  {T}⟩ failures with this signature" (§3.2, §3.4).
- **AP-G6 — No takeaway; identifiers lead.** The card's first content is
  `group_id`; the reader must synthesize "alone vs shared vs burst" from raw fields.
  → L1 sentence G1–G3 (§3.1).
- **AP-G7 — Spec pointer as the card's only explanation.** Subtitle
  "co-failure launch group (spec §5)" orients the spec author, not the reader. →
  plain-word subtitle; spec pointer moves to the L3 drawer (§3.3).

### Decision card

- **AP-D1 — Thresholds encoded as color only.** The gauge's gray/amber/green arcs
  are the *only* representation of 0.45/0.75; no legend, no numbers on the arc
  boundaries. QA cannot recover what the colors mean; color-alone also violates the
  project's own CVD rule (app.css header). → labeled banded bar (§4.3).
- **AP-D2 — `model_ver` as the card subtitle.** The second-most-prominent slot on
  the card carries `rule_cold;fs=2;emb=e5s-int8-r614241f6`. Expert metadata in a
  glance position — and it silently *is* the method signal, which nobody can read.
  → subtitle becomes the band in plain words; model_ver to L3 (§4.4).
- **AP-D3 — Unexplained band jargon.** "Abstain" appears with no consequence
  attached; QA doesn't learn the item stayed To Investigate, or that abstaining is
  designed honesty. → abstain sentences in §4.2 state the consequence and the
  "honest don't-know" framing.
- **AP-D4 — The deciding mechanism is invisible on the deciding card.** Method
  lives one card up as "Stage A/AB/C" stage labels; answering "who decided" requires
  correlating two cards and knowing the stage taxonomy. → mechanism clause inside
  the L1 sentence + "decided by" chip (§4.1–§4.3).
- **AP-D5 — 46-bar waterfall dominates by default.** `Math.max(220, n*15)` px of
  raw feature names (`top1_cosine`, `hist_pb`, `identifier_jaccard_top1`…) is the
  visually largest element for every persona, drowning the actual decision. → L3
  drawer, fully intact but collapsed (§4.4).
- **AP-D6 — "sorted by magnitude" implies importance.** Sorting input *values* by
  |value| next to the word "decision" invites reading the top bars as "the reasons".
  Feature values are inputs, not contributions; the payload has no SHAP/gain. This
  misleads QA and annoys ML. → retitle "Feature inputs the model saw" (§4.4).
- **AP-D7 — Machine text in quotation marks.** `'"' + dec.explanation + '"'`
  typographically presents generated text as a human quote. → plain "analyzer's
  explanation" label, no quotes (§4.3).
- **AP-D8 — The honest abstain reason never reaches the UI.** Spec §6.6 stores
  `abstain_reason` on every suggestion row, but the journey payload omits it — the
  one field designed to answer "why no label?" is dropped before the card. →
  surface verbatim; render in abstain L1 (§4.2, §4.5).
- **AP-D9 — "llm: no" noise row and cryptic "🧠 judge used".** A kv row spent on a
  negative, and the positive case is an emoji + jargon. → chip only when true, in
  words (§4.3).
