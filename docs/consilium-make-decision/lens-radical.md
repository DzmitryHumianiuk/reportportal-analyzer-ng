# Lens 3 — Out-of-the-box concept ("variant C")

Scope: ignore the current Make Decision modal's *structure* (three-tab layout, suggest cards,
manual selection pane) but keep its *job*: **fast, correct triage of one failed test item,
ending in the same RP issue update** (`prepareDataToSend` → `saveDefect` → item issue PUT,
plus `sendSuggestResponse` / `userChoice` feedback — see
`makeDecisionModal.jsx:139-232` in the 5.15.3 tree).

Hard constraints that survive the redesign:

1. **Real data only** — everything rendered comes from `SuggestAnalysisResult`
   (`src/analyzer_ng/amqp/models.py:251`) plus RP's own item/log records. We control both
   ends of the AMQP contract, so the reply may gain first-class fields (proposed below)
   instead of overloading `modelInfo`.
2. **Abstains never look endorsed.** Anything below the suggest bar (< 0.45) is shown, but
   visibly *declined by the analyzer* — the human adopts it as their own decision, never
   "accepts a suggestion".
3. **Deep detail is delegated** to Inspector journey permalinks
   (`/inspector/#view=journey&project=<id>&launch=<id>&item=<id>`, new window). The modal
   must not become a second Inspector.
4. **Apply flow is unchanged at the wire level**: defect-type update + optional comment +
   suggest-feedback, identical payloads to today.

---

## Three candidate interaction models

### Model 1 — Decision strip (evidence-card conveyor)

A single horizontal strip of *evidence cards* ranked by decisive power: Stage-A exact match
first, then GBM neighbors by calibrated score, then the LLM rubric hypothesis. One card is
"in focus" at a time; `→`/`←` moves focus, `Enter` accepts the focused card's verdict,
`Esc` skips to manual. Each card is one screenful: verdict pill, score band, one evidence
line, Inspector link.

- **Strength:** ruthless speed for the common case; pure keyboard triage; trivially
  learnable.
- **Weakness:** serializes what is fundamentally a *comparison*. When two cards disagree
  (LLM says `ab`, history says `pb`) the user must hold card 1 in memory while reading
  card 3 — precisely the situation where triage errors happen. Also biases toward whatever
  is ranked first.

### Model 2 — Disagreement-first "bench of advisors" (consilium view)

Reframe the analyzer's outputs as **independent advisors** that each cast a verdict:
*Precedent* (Stage-A exact-match inherit), *Similarity* (GBM neighbor(s)), *Rubric* (LLM
cold-start hypothesis). The modal leads with the **state of agreement**, not with a list:

- All advisors agree above the bar → a single verdict line and a one-keystroke apply.
- Advisors split → the modal opens directly on the **conflict**: the two competing verdicts
  side by side, and the *differentiating evidence* between them.
- Nobody advises (all abstain) → the bench is visibly empty and declined candidates sit in
  a distinct "below the bar" dock.

- **Strength:** effort scales with case difficulty (the 80% unanimous case costs one
  keystroke; the hard case opens already focused on what makes it hard). Abstention is a
  first-class visual state, not a styling afterthought. Maps 1:1 onto analyzer-ng's actual
  architecture (Stage-A / GBM bands / LLM sidecar), so nothing is fabricated.
- **Weakness:** needs one new server-side notion ("which evidence separates two
  hypotheses") to fully deliver; degrades to Model 1 behavior when there's only one advisor.

### Model 3 — Two-pane precedent diff

Left pane: this failure's error log. Right pane: the best precedent's log,
**diff-highlighted** (common trunk dimmed, divergent tokens hot). Header: the precedent's
defect + who labeled it + when. `Enter` = "same failure, inherit the label".

- **Strength:** the single most convincing evidence display for the *similarity* method —
  you literally see why the analyzer thinks it's the same failure.
- **Weakness:** it's an evidence *view*, not a whole interaction model: it has no answer
  for cold-start (no precedent exists), for rubric hypotheses, or for disagreement between
  methods. Great component, wrong top-level frame.

### Verdict

**Model 2 wins**, with Model 3 embedded as its evidence stage and Model 1's keystroke
economy as its fast path. That composite is **variant C — "The Bench"**.

---

## Variant C — "The Bench"

A full-height takeover (same `DarkModalLayout` chrome slot as today, but the interior
abandons the three-tab anatomy). Three horizontal regions, top to bottom:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ITEM STRIPE  attributes check · FAILED · To Investigate      [Inspector ↗]│
├──────────────────────────────────────────────────────────────────────────┤
│ THE BENCH (advisor chips, left→right = decisive power)                   │
│  ┌────────────┐ ┌────────────┐ ┌−−−−−−−−−−−−┐   ····· the bar ·····     │
│  │ PRECEDENT  │ │ SIMILARITY │ │ RUBRIC     │   ┆ 2 below the bar ┆     │
│  │ PB · exact │ │ PB · 0.81  │ │ AB · 65%*  │   ┆ (declined)   ▸  ┆     │
│  └────────────┘ └────────────┘ └−−−−−−−−−−−−┘   ·····················    │
├──────────────────────────────────────────────────────────────────────────┤
│ EVIDENCE STAGE (content depends on agreement state, see States)          │
│                                                                          │
├──────────────────────────────────────────────────────────────────────────┤
│ VERDICT BAR   [ Product Bug ▾ ]  ☑ attach reasoning as comment           │
│               Apply (Enter) · Manual… (M) · Cancel (Esc)                 │
└──────────────────────────────────────────────────────────────────────────┘
```

### Elements

**Item stripe.** Test name, status, current defect (reuses `itemHeader`'s
`DefectTypeItem` pill), and the *only* always-present Inspector link: **"Open journey in
Inspector ↗"** → the item's permalink, new window. Error log preview collapsed behind the
stripe (one click), because the evidence stage below usually shows the relevant lines
anyway.

**The bench.** One chip per advisor that actually produced a verdict at or above the
suggest bar (band `suggest` ≥ 0.45 or `auto` ≥ 0.75):

- Chip anatomy: method glyph + name (**Precedent / Similarity / Rubric**), verdict pill
  (defect type from project config, exactly like patch design A), confidence rendered as a
  **band label, not a bare number**: `auto` chips get a filled edge ("would auto-apply"),
  `suggest` chips a half-filled edge; the rubric chip always carries the patch-B framing —
  dashed outline, "provisional" tag, and its number labeled *rubric confidence*, never
  similarity.
- Chip footer: provenance one-liner from the (now first-class) contract — *"labeled by
  d.gumeniuk, launch #266"* for `src=human-confirmed`, *"auto-labeled"* dimmed for
  `src=auto-analyzed`.
- Clicking a chip focuses its evidence in the stage; it does **not** apply anything.
- Each chip has its own **"why ↗"** link → Inspector journey permalink of the *relevant
  item* (for neighbors) or of the current item (for the rubric hypothesis, whose
  `relevantItem` is a self-reference per the patch README).

**The bar + below-the-bar dock** (user goal 1). A literal thin dotted vertical rule after
the last endorsed chip, labeled "the bar · 0.45". To its right, a collapsed dock:
**"2 below the bar (analyzer declined) ▸"**. Expanded, declined candidates render in a
deliberately different visual grammar so they can never be mistaken for endorsements:

- grayscale/desaturated, dashed borders, **no verdict pill** — the defect name appears as
  plain struck-through-band text: `pb — declined at 0.31`;
- no score edge-fill at all (the fill *is* the endorsement language);
- header inside the dock: *"The analyzer declined these. Shown for your judgment only."*;
- the only action is **"Decide this yourself…"** which jumps to the verdict bar with that
  defect *pre-highlighted but not selected* — the user must still click/keystroke the
  defect type, making the adoption an explicit human act. Applying from this path sends
  the normal issue update plus suggest feedback tagged with the abstain band (so the
  analyzer learns from overrides).
- Keyboard fast-apply (`Enter`) **never** targets dock items.

**Evidence stage.** State-dependent (below). Always at most *one screen* of evidence: the
stage shows spans, not logs. "More context" is always an Inspector link, never an
accordion of full logs — this is the anti-second-Inspector valve (user goal 3).

**Verdict bar.** A single defect-type selector (the same project defect-type source the
manual tab uses today) pre-set to the bench's leading verdict when one exists, *empty* when
the bench is empty. Checkbox **"attach reasoning as comment"** — checked by default when an
LLM explanation exists (user goal 2): applying then lands `suggestion.explanation` into the
item comment through the existing comment plumbing (same as patch design D, minus the
forced detour through the manual tab for the unanimous case; an "edit first" affordance
opens the comment editor inline for those who want to touch it). Apply = existing
`saveDefect()`; accepted advisor → `sendSuggestResponse` with `userChoice: 1` for its row,
0 for the rest, exactly today's semantics.

### States

**S1 — Unanimous / fast path.** All present advisors agree (or only one advisor exists at
`auto` band; Stage-A exact-match inherit is the canonical case). Bench collapses to a
single wide verdict line: *"Product Bug — precedent (exact match) and similarity (0.81)
agree."* Evidence stage shows one proof artifact: the matched log line of the precedent
with its label provenance. The whole modal reads in ~2 seconds; `Enter` applies.
The LLM explanation, when present, renders under the verdict line as a quoted
one-paragraph block captioned **"Analyzer's reasoning (LLM)"** with `data-provenance`
marking, plain-text rendered (no HTML), with "See why in Inspector ↗" at its end.

**S2 — Split bench (the disagreement view).** Two or more advisors above the bar disagree.
The stage becomes a **two-column confrontation**, one column per verdict (not per advisor —
agreeing advisors stack in one column):

```
│  PRODUCT BUG (Precedent + Similarity)   │  AUTOMATION BUG (Rubric, provisional) │
│  neighbor log, diff vs current item     │  rubric why-text (real, from          │
│  (Model-3 pane: common trunk dimmed,    │  explanation field), the rubric line   │
│  divergence hot)                        │  R1 it fired on                        │
│  "labeled by <user>, launch #266  ↗"    │  "cold-start hypothesis, no precedent" │
```

Crowning it, **the tiebreaker line** (the wow moment, see below). Keyboard: `1` selects
the left verdict, `2` the right, `Enter` applies the selection; nothing is preselected in
a split — a disagreement demands a choice, so the verdict bar starts empty here.

**S3 — Cold start (rubric only).** No precedent, no neighbor ≥ 0.45; only the rubric
hypothesis is above the bar (or it, too, is below it). The bench shows the lone dashed
rubric chip; stage shows the why-text plus the actual failing log line the rubric matched.
Verdict bar is *empty* — accepting the hypothesis is patch design D verbatim: **"Accept &
edit comment"** opens the inline comment editor prefilled with the why-text, defect
preselected, Apply enabled after review. Provisional never gets the one-keystroke path.

**S4 — Empty bench.** Everything abstained. Big honest empty state: *"The analyzer
declined to advise on this item."* Dock auto-expands (it's the only content), manual
defect selector in the verdict bar becomes the primary focus, Inspector link prominent.
This state is the design's integrity test: a wrong-but-confident-looking UI here is
exactly what the abstain band exists to prevent.

**S5 — Degraded.** Analyzer unavailable / still loading / bulk selection: bulk keeps the
current modal flow untouched (the bench is a single-item instrument — same boundary the
stock modal already draws with `isMLSuggestionsAvailable`); loading shows bench-chip
skeletons; analyzer-off shows S4 minus the dock.

### The wow moment — the tiebreaker line

In S2, the modal asks the analyzer (new suggest-reply field, we own both ends) for the
**evidence span that most separates the competing hypotheses**: for GBM, the
highest-leverage feature span (we already ship `modelFeatureNames`/`modelFeatureValues`);
for the rubric, the log line its winning rule anchored on. The stage renders that single
line diff-highlighted at the top of the confrontation:

> `expected undefined to deeply equal { Object (key, value) }` — *precedent's failure has
> the same assertion; rubric read the `undefined` as a test-code fault.*

The human reads **one line and one sentence of tension** instead of two stack traces —
that is the moment triage gets faster rather than merely prettier. If the analyzer can't
produce a span, the stage falls back to the Model-3 diff panes alone (real data only — no
synthesized "tension" sentence without a grounded span behind it).

### Contract additions (both ends are ours)

Extend `SuggestAnalysisResult` (additive, defensively parsed like the current patch, so a
stock analyzer keeps working):

| field | type | replaces today's hack |
|---|---|---|
| `confidenceBand` | `"auto" \| "suggest" \| "abstain"` | threshold math in the UI |
| `explanation` | `str \| None` (grounded LLM text, persisted `suggestion.explanation`) | `modelInfo` `why=` stuffing |
| `explanationSource` | `"llm" \| "rubric" \| None` | `methodName` sniffing |
| `provenance` | `"human-confirmed" \| "auto-analyzed" \| "unlabeled"` + `labeledBy/labeledAt` | `modelInfo` `src=` parsing |
| `stage` | `"exact" \| "gbm" \| "rubric"` | `methodName` overloading |
| `evidenceSpans` | `[{logId, lineStart, lineEnd, role: "match" \| "tiebreak"}]` | none (new capability) |
| `inspectorUrl` | `str` (journey permalink) | UI-side URL assembly |

Abstain-band candidates ride the same reply list with `confidenceBand: "abstain"` — today
they're filtered out server-side; the suggest handler starts including them (capped, e.g.
top 3 below the bar) since the UI now has a safe place to put them.

### Why this is still the same apply flow

Nothing after the Apply click changes: verdict-bar state feeds the existing
`prepareDataToSend()` shape (issue type, comment, ignoreAA untouched), `saveDefect()`
performs the identical item update, `sendSuggestResponse()` reports `userChoice` per
suggestion row — now including declined rows the human adopted, which is free training
signal the current modal structurally cannot collect.

### Mock checklist (for the visual pass)

- Dark theme; may diverge from RP's modal grammar (this is the radical variant) but keep
  RP's defect-type colors — they are the user's learned vocabulary.
- Endorsement language = color + edge-fill; abstention language = grayscale + dashed + no
  fill. These two grammars must never blend.
- Bench chips: fixed height, max 4 above the bar before overflow ("+1 more").
- Evidence stage: hard max-height, internal scroll only inside diff panes.
- Keyboard legend rendered in the verdict bar (`Enter` / `1`,`2` / `M` / `Esc`).
- States to mock: S1, S2, S3, S4, dock-expanded.
