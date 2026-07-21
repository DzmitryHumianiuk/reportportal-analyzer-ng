# Lens 1 — Triage Workflow UX: Make Decision modal redesign spec

Grounded in: `makeDecisionModal/**` (5.15.3 + our `service-ui-5.15.3-suggestion-cards.patch`,
see `docs/rp-patches/README-PATCH.md`), `SuggestAnalysisResult`
(`src/analyzer_ng/amqp/models.py:251`), bands in `src/analyzer_ng/core/decision.py`
(`TAU_SUGGEST = 0.45`, auto ≥ 0.75), abstain-explainer wiring in `core/analysis.py:922-976`,
live screenshots in `docs/rp-patches/evidence/`.

---

## 1. The triager's job (what the modal must optimize)

The modal is a **decision accelerator**, not an investigation surface. The job, in order,
with a target time budget of **≤ 20 s for an endorsed suggestion, ≤ 60 s otherwise**:

| Step | Task | Question in the triager's head | Current UI region |
|---|---|---|---|
| 1. Orient | Glance context | *What failed, and how?* | Left column: item name + error log (`executionSection`) |
| 2. Scan | Read hypotheses | *Does the machine already know?* | Central tab strip cards (`makeDecisionTabs`) |
| 3. Pick / adjust | Choose defect + comment | *Do I agree? What do I write?* | Active tab content + footer comment (`infoBlock/commentSection`) |
| 4. Scope | Apply breadth | *Just this one, or the 17 similar TI?* | Left column: "Apply for:" (`optionsSection`) |
| 5. Commit | Apply | — | Footer Apply (Ctrl+Enter) |

**Reading order = salience order.** Everything below is placed and weighted by its value to
this sequence. Anything that serves *investigation* rather than *decision* is demoted to a
link out to the Inspector journey permalink.

### Salience tiers used below

- **P1** — must be readable without scrolling or clicking; carries accent color.
- **P2** — visible but visually quiet; no accent; one glance to parse.
- **P3** — behind one interaction (collapse/hover/scroll); never animated.

---

## 2. Layout spec by region (variant A/B skeleton — stays in RP dark modal)

```
┌────────────────────────────┬──────────────────────────────────────────────┐
│ EXECUTION TO CHANGE   (P1) │ SELECT DEFECT                                │
│  item name · status pill   │ ┌────────┬─────────────────────────┬───────┐ │
│  error log (first frame)   │ │ Manual │ [82% Suggested][61% …]  │ Hist. │ │  P1: suggest-band cards
│                            │ │ select │ [65% LLM hypothesis]    │ test  │ │
│ APPLY FOR:            (P2) │ └────────┴─────────────────────────┴───────┘ │
│  scope dropdown            │  ▸ Declined by analyzer (2)             (P3) │  collapsed row, no accent
│  similar-TI checklist      │ ──────────────────────────────────────────── │
│                            │  TAB HEADER  band + confidence + provenance  │  P1 text, honest wording
│                            │  ┌ AI summary — 2-line clamp ──────── (P2) ┐ │
│                            │  │ "why" text  · [See why in Inspector ↗] │ │
│                            │  └────────────────────────────────────────┘ │
│                            │  SUGGESTED DEFECT  ● Product Bug  chip (P1) │  (patch row A/B, kept)
│                            │  [Accept & edit comment]  (rubric only)     │
│                            │  neighbor / item detail, similar log   (P2) │
├────────────────────────────┴──────────────────────────────────────────────┤
│ FOOTER: decision recap · comment editor · Cancel / Apply             (P1) │
└───────────────────────────────────────────────────────────────────────────┘
```

Changes vs. today, in priority order:

1. **Central card strip is band-partitioned** (§3, §4). Suggest-band cards (≥ 0.45) keep
   today's look including the first-card jump animation. Declined-band cards (< 0.45) are
   **not in the strip at all** — they live in a collapsed "Declined by analyzer (N)" row
   *below* the strip.
2. **Tab header gains the band sentence** (§3) — extends the patch's existing rubric header
   pattern ("LLM cold-start hypothesis · 65% rubric confidence") to all rows.
3. **AI summary strip** (§5) — one clamped block under the tab header, present whenever the
   reply carries an `explanation`; the patch's rubric why-block (design C) generalizes into
   this slot.
4. **One modal-level "See why in Inspector ↗"** (§6) — right-aligned inside the AI summary
   strip (or the tab-header row when no summary exists). Nothing else links out.
5. Left column, footer, scope selector, comment flow: **unchanged**. They already serve
   steps 1/3/4/5 well and the patch's prefill flow (design D) plugs into them.

---

## 3. Banding vocabulary (exact strings — use verbatim)

Bands come from the analyzer, not recomputed in the UI (§8). One name per band everywhere —
card label, tab header, chip, telemetry.

| Band (confidence *p*) | Card label (strip/row) | Tab-header sentence | Chip |
|---|---|---|---|
| **auto** (p ≥ 0.75) | `82% · Suggested` | `Analyzer suggestion · 82% confidence — auto-apply range` | `high confidence` |
| **suggest** (0.45 ≤ p < 0.75) | `61% · Suggested` | `Analyzer suggestion · 61% confidence` | *(none)* |
| **abstain / declined** (p < 0.45) | `32% · Declined` | `Analyzer declined this candidate · 32% — below the 45% suggestion bar. Applying it is your judgment.` | `below bar` |
| **rubric** (pseudo-confidence) | `65% · LLM hypothesis` *(as patched)* | `LLM cold-start hypothesis · 65% rubric confidence` *(as patched)* | `LLM hypothesis (provisional)` |

Vocabulary rules:

- **"Suggested"** is reserved for p ≥ 0.45. The words "suggested/suggestion" must never
  appear on a declined card — the analyzer declined it; the UI must not re-endorse it.
- **"Declined"** (not "low confidence", not "other candidates") — names the analyzer's
  action, which is the honest frame: *the machine looked and said no; you may overrule.*
- Confidence numbers are always shown, always with the band word, never a bare percent on a
  declined row (a bare "32%" one card below a bare "61%" reads as a ranking, not a verdict).
- Rubric pseudo-confidence is never mixed into similarity sorting and keeps its own noun
  ("rubric confidence"), exactly as the patch already enforces.

---

## 4. Problem (a) — sub-0.45 candidates: "analyzer declined these — your call"

**Placement.** A single full-width disclosure row directly **under** the central card strip,
**above** the tab-content divider:

> `▸ Declined by analyzer (2) — below the 45% suggestion bar`

- **Collapsed by default. Always.** No animation, no accent, no count badge coloring. A
  triager who never opens it loses nothing the analyzer endorsed.
- Expanded: declined cards render in the same row, sorted by confidence desc **within the
  band**, but visually demoted: outline-only card (no fill), 80–90% scale, muted text color,
  label per §3 (`32% · Declined`). They are never interleaved with suggest-band cards and
  never re-sorted into the strip — two ordered groups, hard divider, period.
- Expanding sets a per-user sticky preference (localStorage) so heavy overrulers aren't
  taxed with a click per item — but the *default* for a fresh user is collapsed.

**Selection behavior.** Clicking a declined card drives the exact same machinery as today
(`selectMachineLearningSuggestionItem` → `suggestChoice` → ML tab), with three deltas:

1. The active-card state uses a **neutral gray outline**, not the topaz active fill — the
   demotion survives selection. It must never look like the endorsed active card.
2. The tab header shows the declined sentence from §3 — this is the "friction": one honest
   line, **no confirm dialog** (a dialog would break the ≤ 60 s budget and teaches users to
   click through).
3. The tab title (`messages.machineLearningSuggestions`) renders `32% · Declined`, not the
   stock "NN% Analyzer Suggestion".

**Never-endorsed guarantees (checklist for implementation/review):**

- Declined cards never receive the `jumping` animation (today: first strip card only — keep
  that scoped to the suggest band).
- Declined cards are never auto-selected on open under any state.
- The declined disclosure never appears when the suggest band is empty *but* the strip shows
  the stock "no suggestions" prompt — instead the prompt becomes: *"The analyzer has no
  suggestion above the 45% bar."* followed by the disclosure row. An abstain with candidates
  is not "no results"; it is a decline, and saying so is goal (1)'s core.
- `sendSuggestResponse` telemetry includes declined rows with their band, and a pick sets
  `userChoice = 1` on the declined row exactly as for endorsed ones — human overrules of
  declines are the highest-value calibration signal we can collect.

## 5. Problem (b) — LLM summary placement: informs, never dominates

The `explanation` belongs to **the decision about this item**, not to any one neighbor card.
So it renders **once**, as a strip between the tab header and the suggested-defect row —
the generalization of the patch's rubric why-block (design C), same `data-provenance`
discipline:

- Caption by decision kind: rubric → `AI hypothesis — why this defect` (as patched);
  suggest/auto with explainer output → `AI summary — why this suggestion`; abstain →
  `AI summary — why the analyzer declined` (from the `abstain_explainer` job,
  `core/analysis.py:947`). The abstain summary doubles as the header content of the
  declined section's expanded state.
- **2-line clamp** with a "more" toggle; plain text only (no HTML injection — keep the
  patch's parser discipline); muted foreground, thin left border, no accent fill.
- If no explanation exists (legacy row, sidecar cold, explainer not yet persisted): the
  strip is absent entirely — no skeleton, no "generating…" spinner. The modal never waits
  on the LLM.

Anti-domination rules: the strip is below the tab header (band verdict reads first), above
the defect pill only because it justifies the pill; it never pushes the similar-log detail
below the fold at the reference viewport (1440×900 — see evidence screenshots); it never
appears inside strip cards (cards stay two-line glanceable).

## 6. Problem (c) — Inspector links: one modal-level link (decided)

**Decision: a single modal-level `See why in Inspector ↗`**, right-aligned in the AI-summary
strip (or in the tab-header row when there is no summary), opening
`/inspector/#view=journey&project=<id>&launch=<id>&item=<id>` in a new window
(`target="_blank" rel="noopener"`).

Rationale: the permalink is a **per-item journey** — it explains the decision for the item
under triage, not any single neighbor. Per-candidate "See why…" links would all resolve to
the same journey today, which trains users that the links are noise. One link, placed where
the "why" question actually arises (next to the summary/verdict), is the honest mapping.

- Bulk/cluster mode (`MLSuggestionsByCluster`): link targets the representative item of the
  cluster — same rule, still one link.
- **Deferred, gated on Inspector support:** if the journey view later accepts
  `&candidate=<relevantItem>` anchors, add per-candidate links *only* inside the expanded
  declined section and the active tab detail — never on strip cards.
- No Inspector link in the footer. The footer is the commit zone; an exit link next to
  Apply invites abandonment mid-decision.

## 7. Problem (d) — the three accept flows, differentiated

| Flow | Trigger | Mechanics | Verb the user sees | Comment behavior |
|---|---|---|---|---|
| **Endorsed suggestion** (suggest/auto band, classical) | Click strip card → Apply | Stock: `suggestChoice.issue` copied via `prepareDataToSend` | `Apply` (footer, unchanged) | Neighbor's comment carried per stock rules |
| **Declined pick** (abstain band) | Expand disclosure → click declined card → Apply | Same copy path; band sentence in tab header (§4); telemetry marks band | `Apply` — same verb; the *header sentence* carries the responsibility framing, not a scary button | Same as endorsed; do **not** auto-inject "overrode analyzer" notes into the user's comment |
| **Rubric hypothesis** | Click LLM card → `Accept & edit comment` | As shipped (patch D): routes to *Select defect manually* with defect pre-selected + `why=` prefilled in the editor; user reviews, then Apply | `Accept & edit comment` (distinct verb = distinct contract: nothing applies until reviewed) | Prefilled explanation, editable — the reasoning lands in the item's comment |

The deliberate asymmetry: endorsed and declined picks share the fast two-click path
(difference is framing + demotion, not friction), while the rubric keeps its mandatory
review step because a self-referenced rubric row has no real neighbor issue to copy and its
reasoning *is* the payload.

Scope interaction (step 4): all three flows respect the existing "Apply for:" selector
unchanged. One guard: when a **declined** pick is active and scope covers "similar TI in
launch" with items checked, the footer recap row (`infoBlock/resultRow`) prepends
`Declined-band choice → N items` — overruling at fan-out breadth is the one case worth an
extra visible cue (still no dialog).

## 8. Contract deltas (we own both ends — stop stuffing `modelInfo`)

Extend `SuggestAnalysisResult` (`src/analyzer_ng/amqp/models.py`):

```python
confidenceBand: str | None = None   # "auto" | "suggest" | "abstain" | "rubric"
explanation: str | None = None      # persisted suggestion.explanation, plain text
declinedByAnalyzer: bool = False    # true ⇒ UI must render demoted (§4)
```

- The **suggest reply includes abstain-band candidates** (top-K from Stage-C, the same refs
  `_abstain_candidates` already assembles for the explainer) instead of returning `[]` —
  each with `declinedByAnalyzer: true`. `matchScore` keeps carrying the calibrated
  confidence as today.
- UI derives *nothing* about bands from thresholds; it renders what the reply says. If both
  `confidenceBand` and the `modelInfo` microformat are absent → stock rendering (the
  patch's defensive-parsing rule stands, so legacy analyzers are unaffected).
- **Risk to verify first:** README-PATCH claims RP relays `suggestRs` verbatim, but
  service-api deserializes into its own DTO and may drop unknown fields. Verify pass-through
  on the stand before building on the new fields; **fallback** is the proven channel —
  extend the `modelInfo` microformat (`…;band=abstain;why=…`) and the existing
  `analyzerSuggestionMeta.js` parser. Either way the UI reads through one accessor module.

## 9. Guardrails — what the modal must NOT become

- **No journey timeline, no feature tables, no per-stage breakdown** in the modal —
  `modelFeatureNames/Values` stay unrendered; that is the Inspector's job behind the one
  link (§6).
- No blocking on LLM output, ever (§5).
- No third column, no new tab: declined candidates and the summary fit inside the existing
  central block; the tri-tab strip (Manual / suggestions / History) keeps its geometry.
- Reading order is the acceptance test: a triager must be able to say *what failed → what
  the analyzer thinks (and how strongly, in band words) → what applying will do (defect
  pill + scope recap) → Apply* without opening a single disclosure. Everything else is P3.
