# Lens 2 — Data contract & modal architecture

Make Decision redesign, contract layer. Grounded in:

- Analyzer reply model: `src/analyzer_ng/amqp/models.py` — `SuggestAnalysisResult` (line 251).
- Suggest pipeline: `src/analyzer_ng/core/analysis.py` — `suggest()` (282), `_render_suggestions()`
  (1014, the `proxy < TAU_SUGGEST → []` cutoff at 1027), `_suggestion_candidates()` (1100),
  `_maybe_rubric_fallback()` (349), `_model_info()` (1360).
- Bands: `src/analyzer_ng/core/decision.py` — `TAU_AUTO = 0.75`, `TAU_SUGGEST = 0.45` (35–36).
- Patched UI (5.15.3 + our patch, checkout at `/tmp/rp-ui-patch`):
  `makeDecisionModal.jsx`, `makeDecisionTabs/makeDecisionTabs.jsx`,
  `tabs/machineLearningSuggestions/machineLearningSuggestions.jsx`, `analyzerSuggestionMeta.js`.
- Patch notes: `docs/rp-patches/README-PATCH.md`.
- Inspector permalink shape: `inspector/static/js/views/llm.js:251` —
  `#view=journey&project=${project}&launch=${launch_id}&item=${item_id}` under `/inspector/` on the
  same host.

---

## 1. The wire reality: three hops, and the middle one is lossy

```
analyzer-ng ──AMQP──▶ service-api (stock Java) ──REST──▶ patched service-ui
SuggestAnalysisResult   SuggestInfo DTO (typed)     item.suggestRs (JSON)
```

We control **both ends** but not the middle. service-api deserializes the analyzer's reply into its
typed `SuggestInfo` DTO, wraps it as `{ testItemResource, logs, suggestRs }`, and re-serializes to
the UI. Consequence — and this is the load-bearing fact of this lens:

> **A brand-new JSON key added to `SuggestAnalysisResult` is expected to be *tolerated* by stock
> service-api (ignored on deserialization) but *dropped* before it reaches the UI.** It never
> appears in `suggestRs`. The same stripping happens in reverse on the userChoice PUT
> (`URLS.choiceSuggestedItems` → analyzer `index_suggest_info` route, `dispatcher.py:184`).

This is why the shipped patch already rides metadata inside `modelInfo` (an existing pass-through
string field) rather than new keys. It is proven live (image `5.15.3-ng1`, analyzer `mk18`).

**Verification required before relying on "tolerated":** we have not read the service-api source of
the running stand. Canary test (one-time, per RP upgrade): make the analyzer answer one suggest with
an extra unknown key (behind a debug env var), open the modal on that item, confirm (a) the reply is
accepted (modal renders suggestions, no 5xx in service-api logs), (b) the extra key is absent from
the `/item/suggest/<id>` response body (Network tab). If (a) fails — Jackson configured to fail on
unknown properties — first-class field additions are **forbidden** on the wire and everything below
still works, because transport is via `modelInfo` anyway.

### Design consequence: dual carriage

- **Source of truth:** new *first-class, defaulted* fields on `SuggestAnalysisResult`. They make
  analyzer code and tests honest, are persisted/visible to the Inspector via the DB, and become the
  real wire contract the day we patch service-api (deliberately out of scope now — a Java build +
  upgrade-drift cost we don't need yet).
- **Transport through stock service-api:** the same values deterministically encoded into
  `modelInfo` by one encoder, parsed by one parser module in the UI (`analyzerSuggestionMeta.js`).
  One pair of functions, no scattered string munging.

A pleasant side effect: because `modelInfo` is echoed back verbatim on the userChoice PUT, the
feedback loop is **lossless** through stock service-api — the analyzer can re-parse `band=` from the
returned row and learn "human accepted a below-bar candidate", which first-class fields would lose
until service-api is patched.

---

## 2. Contract v1

### 2.1 New first-class fields (Pydantic, all defaulted)

```python
class SuggestAnalysisResult(BaseModel):
    ...existing fields unchanged...
    # -- contract v1 additions (ng=1). All Optional/defaulted: the userChoice
    # roundtrip echoes rows stripped by stock service-api, so the analyzer must
    # accept rows missing every one of these.
    band: Literal["auto", "suggest", "below_suggest", "rubric"] | None = None
    confidence: float | None = None   # raw calibrated p* in [0,1]; NOT matchScore
    explanation: str | None = None    # grounded text, ≤600 chars on the wire
    explKind: Literal["match", "decline", "rubric"] | None = None
    provenance: str | None = None     # human-confirmed | auto-analyzed | seed | kb-mode | unlabeled
```

Field-name choices: camelCase to match the rest of the reply; `confidence` is deliberately separate
from `matchScore` because `matchScore` is (a) displayed by stock UI as a percentage and (b) already
overloaded (similarity for classical rows, pseudo-confidence for rubric rows). We do **not** try to
fix `matchScore` semantics in v1 — stock UI renders it, so its meaning per row kind is frozen;
`confidence` is the clean channel.

Serialization tolerance, analyzer side: additions with defaults are backward compatible in Pydantic
for both directions (old peers simply omit them; extras on inbound are ignored by default). The
inbound `index_suggest_info` route parses with `_identity` (raw payload, `dispatcher.py:184`), so
nothing breaks there either. Add one regression test: `SuggestAnalysisResult(**old_shape_dict)`
still validates, and `model_dump()` of a new row contains the old keys unchanged.

### 2.2 `modelInfo` grammar v1 (the actual UI transport)

Rules: `;`-separated `key=value` tokens; **`why=` must be the last token** and its value is the
verbatim remainder of the string (free text may contain `;`/`=` — this is already how
`parseRubricWhy` works). Grammar is versioned by the presence of `ng=1`.

Classical rows (today: `analyzer-ng;gbm=..;emb=..;kb_mode=..;src=<prov>`):

```
analyzer-ng;ng=1;gbm=..;emb=..;kb_mode=..;src=<prov>;band=<band>;conf=<p* 0.000-1.000>[;ek=<match|decline>][;why=<text>]
```

Rubric rows (today: `coldstart_rubric;<model_ver>;why=<text>`):

```
coldstart_rubric;<model_ver>;ng=1;band=rubric;conf=<0.000-1.000>;ek=rubric;why=<text>
```

Back-compat with the **already-shipped** parsers is preserved by construction:

- `isRubricHypothesis` keys on `methodName === 'coldstart_rubric'` — untouched.
- `parseRubricModelVer` takes token `[1]` — still the model version (new tokens inserted after it).
- `parseProvenance` matches `/src=([^;]+)/` — `src=` still precedes any `;`-bearing free text.
- `parseRubricWhy` takes the remainder after the first `why=` — `why=` stays last.

So UI-patch **ng1** (current image) renders a **ng=1** analyzer correctly, just without the new
chrome. The reverse (new UI, old analyzer) degrades the same way: every new parser returns
`null`/`''` on a string without its token and renders nothing extra — same defensive discipline the
patch already follows.

### 2.3 Row invariants (unchanged and new)

1. **No fabrication** — `relevantItem`/`relevantLogId` must be a real, loadable item/log
   (service-api loads it to build `testItemResource`; a bad id kills the whole reply). Rubric rows
   keep the documented self-reference.
2. **Unique `relevantItem` per reply** — the modal keys cards by `testItemResource.id` and looks the
   selected card up with `find(item => item.testItemResource.id === itemId)`
   (`makeDecisionTabs.jsx:62,153`). Duplicate relevant items would collide React keys and
   mis-select. `_suggestion_candidates` already dedupes (analysis.py:1127); the new below-band path
   and the rubric fallback must dedupe against the full outgoing list (rubric self-id can in
   principle equal nothing else, but assert it anyway).
3. **Ordering** — `resultPosition` is 0..N over the whole reply, `suggest`-band rows first
   (best-first, judge promotion preserved), then `below_suggest` rows best-first, rubric row last.
   Rationale for rubric-last: below-band rows are evidence (real neighbors); the rubric row is a
   hypothesis about the item itself. Total rows ≤ `suggest_max`.
4. **Band is per-row, not per-reply** — see §3.1.

---

## 3. Analyzer-side behavior change

### 3.1 Per-row banding (fixes an existing honesty gap)

Today `_render_suggestions` gates the *whole reply* on the decision proxy (line 1027) — but once the
top candidate clears τ_suggest, **secondary stage-C rows ship regardless of their own similarity**,
visually identical to the endorsed one. A 0.52 suggest can arrive flanked by 0.38 alternates that
today look equally endorsed. v1 makes band a per-row fact:

- Row 0 (the decision's answer) gets the decision band: `auto` (p* ≥ 0.75), `suggest`
  (0.45 ≤ p* < 0.75).
- Each secondary stage-C row gets its own band by its own score: `suggest` if
  `score ≥ TAU_SUGGEST`, else `below_suggest`.
- `conf=` carries row 0's calibrated p*; secondary rows carry their cosine in `matchScore` as today
  and omit `conf` (they have no calibrated probability — do not invent one).

### 3.2 The abstain path: return flagged candidates instead of `[]`

Replace the early return at analysis.py:1027:

- When `proxy < TAU_SUGGEST`: render up to **K = `ANALYZER_SUGGEST_BELOW_MAX` (default 2, hard cap
  `suggest_max` = 3)** stage-C candidates with `band=below_suggest`, subject to a noise floor
  `ANALYZER_SUGGEST_BELOW_FLOOR` (default **0.30** cosine). Floor rationale: below ~0.3 the neighbor
  is lexical noise and burns triage attention; the Inspector journey page already shows the full
  candidate list for the curious. Both knobs are config so calibration data, not taste, gets the
  final word.
- The §6.6 invariant is untouched: the suggestion row (including abstain + feature snapshot) is
  persisted in `suggest()` *before* rendering (analysis.py:303-307); this change is render-only.

**"Never when a real suggest exists, or always append?" — decision: mixed replies are allowed and
per-row banded; no top-up.** Concretely: when ≥1 suggest-band row exists, the candidate list is
exactly today's list (secondary rows now honestly banded, some of them `below_suggest`); we do not
fetch *extra* below-bar candidates to pad the reply. When no suggest-band row exists (today's `[]`),
we return up to K below-bar rows. Rationale: (a) the empty-reply case is where user goal 1 actually
lives — "analyzer said nothing" is the moment the human wants the declined shortlist; (b) padding a
confident reply with declined alternates dilutes the endorsement signal and slows triage (Lens 1
territory, but the contract shouldn't force the UI to fight its own data); (c) reply size stays
bounded and stable.

Rubric interplay: `_maybe_rubric_fallback` currently fires only on `out == []`. New rule: append the
rubric provisional whenever **no suggest-band row** exists (so it can coexist with below-band rows,
ordered last per §2.3-3), still never displacing or reordering an evidence-backed suggestion, still
read-only on the suggest budget.

**Rollout gate (critical):** below-band emission ships behind
`ANALYZER_SUGGEST_BELOW_ENABLED=false`. A **stock** RP UI renders every reply row as a normal
"Analyzer Suggestion NN%" card — a below-bar row would look fully endorsed, violating goal 1's
"must never look endorsed". The flag stays off until the paired UI is live (§5). (The already-shipped
rubric row has this same exposure on stock UI; that tradeoff was accepted and documented, but we
don't widen it.)

### 3.3 Surfacing the persisted explanation (goal 2)

The explainer/judge write `suggestion.explanation` **asynchronously after** the reply that triggered
them (`suggestion_ops.set_explanation`, `llm/roles/explainer.py`), so the *first* modal open usually
races it. Contract: the suggest read path attaches the **latest persisted** explanation when one
exists and is not stale:

- Read `latest_suggestion(project, item)` (suggestion_ops.py:160) alongside the existing
  `latest_judge` read — same LLM-on gate (`_llm_on()`), so the LLM-off path stays byte-identical.
- Attach as `ek=match;why=<text>` on **row 0 only**, and only if the persisted row's label matches
  the rendered row-0 `issueType` **and** its relevant item matches `relevantItem`. Mismatch = the
  world moved since the explanation was written → attach nothing. No re-ranking, no label edits —
  text only.
- **Abstain narration:** the `abstain_explainer` output (analysis.py:922-956) is attached to the
  **first below-band row** as `ek=decline;why=<text>` under the same staleness rule. `ek=decline`
  is the UI's signal to render it as "why the analyzer declined", never as endorsement of the row it
  happens to ride on (a list-of-rows wire format has no reply-level slot; the marker keeps the
  semantics honest).
- Wire cap 600 chars (UI truncates at ~300 with expander; full text lives in Inspector).

---

## 4. Modal-side architecture

### 4.1 Consumption map (who reads what)

| Component | Reads (via `analyzerSuggestionMeta.js` only) | Renders |
|---|---|---|
| `analyzerSuggestionMeta.js` | raw `suggestRs` | new parsers: `parseNgVersion`, `parseBand` (fallback: `methodName==='coldstart_rubric'→'rubric'`, else `matchScore/100 >= 0.45 ? 'suggest' : null` — never guess `below_suggest` from a legacy analyzer), `parseConfidence`, `parseExplanation` (generalizes `parseRubricWhy`), `parseExplKind`, `getInspectorJourneyUrl(suggestRs)` |
| `makeDecisionTabs.jsx` | `band` per card | card strip: below-band cards after a divider ("Below confidence bar — analyzer declined these"), muted/outlined style, no `%`-as-headline (similarity shown small), **`jumping` animation gated to `band==='suggest'`** (today it fires on index 0 unconditionally — a below-band or rubric card at index 0 must not bounce like an endorsement, `makeDecisionTabs.jsx:159`), never pre-selected |
| `machineLearningSuggestions.jsx` | `band`, `explanation`, `explKind`, `provenance`, `confidence` | explanation block (generalize the shipped `rubric-why` block): `ek=match` → "Why the analyzer suggests this"; `ek=decline` → warning-toned "Why the analyzer declined" banner above below-band content; provenance chip as shipped; "See why in Inspector →" link |
| `makeDecisionModal.jsx` | nothing new | flows unchanged: selecting a below-band card is the stock suggestChoice flow (real neighbor, real issue — `prepareDataToSend` untouched); rubric keeps `acceptSuggestedHypothesis`. `sendSuggestResponse` untouched — it already echoes every row with `userChoice`, which now feeds back band-tagged human choices for free |
| `makeDecisionFooter` | `band` of `suggestChoice` | Apply confirmation nuance only (e.g. "applying a below-bar candidate") — optional, Lens 1's call |

Analytics: `getClickOnApplyEvent` already receives `suggestedItems` and `extraAnalyticsParams`
(makeDecisionModal.jsx:308-345) — add `band` of the applied choice to `extraAnalyticsParams` so
acceptance-rate-by-band is measurable without new plumbing.

### 4.2 Inspector permalinks (goal 3)

- **Per-row link** — built entirely from fields already in every `suggestRs`: `project` (numeric id,
  models.py:254), `launchId`, `testItem`:
  `/inspector/#view=journey&project=${project}&launch=${launchId}&item=${testItem}` (exact shape the
  Inspector itself emits, `inspector/static/js/views/llm.js:251`). Same host ⇒ relative URL, no
  config needed.
- **Current-item link when there are no suggestions** (the "analyzer said nothing at all" screen):
  `activeProjectSelector` (controllers/user) yields the project **name**, not the id — do not use
  it. `projectInfoIdSelector` (`controllers/project/selectors.js:44`) provides the numeric id and is
  populated on any inside-project page; item id = `itemData.id`, launch id = `itemData.launchId`
  (present on step/log page items; fall back to hiding the link if absent — never emit a broken
  permalink).
- **New-window semantics:** plain `<a target="_blank" rel="noopener noreferrer">` (the pattern the
  modal already uses for the analyzer-docs link, makeDecisionTabs.jsx:128-135) — not
  `window.open()`, so cmd-click/middle-click behave natively. The modal stays open; Inspector is a
  parallel surface, not a navigation. Track via `onClickExternalLinkEvent`-style event with a
  distinct source tag (`'inspector_journey'`).

This is the whole goal-3 mechanism: the modal renders at most ~300 chars of explanation and one
link; everything deeper (candidate table, feature vector, judge/explainer event log) is the
Inspector's job via the permalink. No new modal panels.

---

## 5. Versioning & rollout of the pair

Version tokens: UI patch generation `ng1` (shipped) → `ng2` (this redesign); analyzer contract
`ng=1` grammar token in `modelInfo`; analyzer image continues `mkNN` tags. The UI never sniffs
analyzer versions — it sniffs **tokens** (`ng=1`, `ek=`, `band=`), which is what makes every mixed
pairing safe.

| | stock UI 5.15.3 | patched UI ng1 (live) | patched UI ng2 |
|---|---|---|---|
| **analyzer mk18 (live)** | stock behavior | **live baseline** | new parsers find no `ng=1` tokens → renders exactly like ng1 |
| **analyzer ng=1, below-flag OFF** | extra fields dropped by service-api; `modelInfo` ignored | rubric/provenance render as today; new tokens harmless to shipped regexes (§2.2) | full redesign minus below-band rows |
| **analyzer ng=1, below-flag ON** | **UNSAFE — below rows look endorsed** | below rows look endorsed (ng1 has no band parser) | **target state** |

Rollout order (each step independently shippable and reversible):

1. Analyzer with contract v1, `ANALYZER_SUGGEST_BELOW_ENABLED=false` — pure metadata enrichment,
   safe under every UI. Run the §1 canary here.
2. UI ng2 (thin-overlay image `reportportal/service-ui:5.15.3-ng2`, same build recipe as
   README-PATCH.md) — renders enriched metadata; below-band section simply never gets data.
3. Flip `ANALYZER_SUGGEST_BELOW_ENABLED=true`. **Kill switch = flip it back** (env-only, no
   image change, no UI dependency).

Pairing is recorded in one place: a `docs/rp-patches/PAIRING.md` row per (ui-image, analyzer-image,
flags) triple actually deployed, so "what does the stand run" is greppable during incidents.

Deferred, deliberately: patching service-api's `SuggestInfo` to carry the first-class fields
natively. Do it only if/when a third consumer appears or `modelInfo` stuffing grows past ~3 more
tokens; the contract above is designed so that migration is a transport swap, not a redesign
(parsers read fields first, fall back to tokens).

---

## 6. Verification checklist (before each rollout step)

1. **Canary unknown-key** through service-api (§1): reply accepted; key absent in UI response.
2. **Pydantic round-trip:** old-shape dict validates; new-shape `model_dump()` superset of old keys;
   `index_suggest_info` accepts a stripped (stock-service-api-shaped) userChoice echo.
3. **Parser property tests (UI):** every parser returns `null`/`''` on mk18-era and stock-analyzer
   `modelInfo` strings; `why=` remainder survives embedded `;`/`=`; `src=`/model-ver parsing
   unchanged against the shipped ng1 fixtures.
4. **Uniqueness invariant:** analyzer test asserting no duplicate `relevantItem` in any reply
   (suggest + below + rubric combined).
5. **Live smoke (per README-PATCH.md discipline, evidence screenshots):** abstain item shows
   below-band section with divider + decline banner, no jumping animation, cards not pre-selected;
   suggest item unchanged vs ng1 except explanation block; Inspector link opens the journey view for
   the exact row item in a new window; Apply on a below-band card writes the neighbor's defect and
   `userChoice=1` lands on the analyzer (check `suggest_info` handling logs); console clean.
6. **Staleness:** re-analyze an item after a label change; confirm the old explanation is not
   attached (label-match guard, §3.3).
