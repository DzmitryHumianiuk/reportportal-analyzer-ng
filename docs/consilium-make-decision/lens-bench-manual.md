# Lens (Bench) 2 — Manual path + comment flow

Refines **variant C "The Bench"** (see `lens-radical.md`) for the case the Bench under-serves:
**"none of these — I already know what it is."** A large share of real triage is a human who does
not need an advisor. The Bench must make the manual pick a *first-class, always-visible* action, not
a tab you route into, while keeping the commit **byte-for-byte identical** to today's
`prepareDataToSend` → `saveDefect` → `sendSuggestResponse`.

Grounded in the real modal (`/tmp/rp-design/service-ui/.../makeDecisionModal/**`):

- `makeDecisionModal.jsx` — `decisionType`, `ACTIVE_TAB_MAP`, `prepareDataToSend` (139), `saveDefect`
  (226), `sendSuggestResponse` (195), `applyChanges` (346), `hotKeyAction` (457).
- `tabs/selectDefectManually/selectDefectManually.jsx` — `DefectTypeSelector` (231),
  `handleManualChange` (79), `MarkdownEditor` (259), `highlightedItem` prop (239).
- `executionSection/executionSection.jsx` — current-item logs via `URLS.bulkLastLogs`, matching-TI
  logs via `URLS.logSearch`; `OptionsSection` = the "Apply for" scope control.
- `elements/testItemDetails/testItemDetails.jsx` — the log renderer with the `★ Similar Log`
  marker (`highlightedLogId` / `highlightedMessage`).

---

## 0. The one load-bearing fact: the modal is already a small state machine

The stock modal has exactly **one** "what did the human decide" variable and **one** rule that reads
it:

```
decisionType ∈ { SELECT_DEFECT_MANUALLY, MACHINE_LEARNING_SUGGESTIONS, COPY_FROM_HISTORY_LINE }
ACTIVE_TAB_MAP[decisionType] → one of { selectManualChoice, suggestChoice, historyChoice }
prepareDataToSend() reads modalState[ ACTIVE_TAB_MAP[activeTab] ].issue   (makeDecisionModal.jsx:140)
```

**The Bench keeps this variable and throws away only the tab strip that used to set it.** Every Bench
action is just "set `decisionType`, fill its slot." Because there is only one `decisionType` at a
time, *the last action wins* — which is exactly the "manual overrides the advisor" behaviour we want,
for free, with no new conflict logic.

So this whole lens is: **which Bench gesture sets which slot, what the comment editor shows, how
scope and Apply stay unchanged.** No new commit path is invented.

---

## 1. The verdict bar *is* the manual picker (never buried)

In the radical mockup the verdict bar already carries a defect selector and a "Manual…" button. We
collapse those into **one always-present control**: the real RP `DefectTypeSelector` from
`selectDefectManually.jsx`, sourced from project config — **Product Bug / Automation Bug / System
Issue / No Defect / To Investigate**, plus any custom subtypes. It sits in the verdict bar in **every
state**, S1 through S4.

```
┌ VERDICT BAR ─────────────────────────────────────────────────────────────────┐
│  Defect:  [ ● Product Bug  ▾ ]   from Similarity (0.81)   ✎ edit reasoning     │
│  Apply to: [ only this item ▾ ]                                                │
│  Reasoning ▸ (analyzer's, editable)                                            │
│                                        Apply (Enter) · Manual (M) · Cancel(Esc)│
└────────────────────────────────────────────────────────────────────────────────┘
```

- **The selector is the manual pick.** Opening its dropdown and choosing a type *is* the manual
  action — there is no separate "manual tab" to travel to. Pressing **`M`** just opens this dropdown
  and puts the caret in it. This is the whole answer to "manual must not be buried": the manual
  control is the resting state of the bar and is on screen the entire time.
- **A tiny provenance tag** to the right of the selector says where the current value came from:
  `from Similarity (0.81)`, `from Precedent (exact match)`, `from Rubric (provisional)`,
  `you picked this`, or nothing when empty. It is text only — it never changes the selector's
  behaviour.

### How manual coexists with / overrides an advisor pick

One selector, last-write-wins on `decisionType`:

| The human does… | `decisionType` becomes | slot filled | provenance tag |
|---|---|---|---|
| clicks the **Similarity/Precedent** chip (or `1`/`2` in a split) | `MACHINE_LEARNING_SUGGESTIONS` | `suggestChoice` = that `suggestedItem` | `from Similarity (0.81)` |
| opens the selector and **picks a type by hand** | `SELECT_DEFECT_MANUALLY` | `selectManualChoice.issue.issueType` | `you picked this` |
| picks by hand **after** adopting an advisor | flips back to `SELECT_DEFECT_MANUALLY` | `selectManualChoice` | tag drops the advisor, shows `you picked this` |

Because the read rule is a single `ACTIVE_TAB_MAP[decisionType]` lookup, a manual pick made after an
advisor pick simply changes which slot `prepareDataToSend` reads. No "are you sure you want to
override the analyzer?" dialog — the provenance tag changing to *you picked this* is the entire
feedback. Fast, honest, reversible (pick the chip again to go back).

---

## 2. The three ways in, mapped to real slots

Four gestures start a decision; they collapse to **two** commit slots.

| Gesture | Sets | Why this slot |
|---|---|---|
| **Adopt classical advisor** — Precedent / Similarity chip, or `1`/`2` in a split | `decisionType = MACHINE_LEARNING_SUGGESTIONS`; `suggestChoice = suggestedItem` | it is a **real neighbour** with a real issue; the stock ML path already copies its issue and, on Apply, `sendSuggestResponse` marks that `suggestRs` row `userChoice: 1` (training signal). |
| **Manual pick** — open selector / press `M` | `decisionType = SELECT_DEFECT_MANUALLY`; `selectManualChoice.issue.issueType = <type>` | the human's own call; no suggestion row to credit. |
| **Below-the-bar adopt** — dock "Decide this yourself…" | `decisionType = SELECT_DEFECT_MANUALLY`; `DefectTypeSelector highlightedItem = <declined type>`, **but `issueType` stays empty until the human clicks the type** | the analyzer *declined* this; adopting it must be an explicit human act, so we pre-*highlight* (the existing `highlightedItem` prop, `selectDefectManually.jsx:239`) without pre-*selecting*. It can never look endorsed. |
| **Rubric adopt** — S3 "Accept & edit comment" | `decisionType = SELECT_DEFECT_MANUALLY`; `issueType = <rubric type>`; comment prefilled with why-text | the shipped patch-D flow verbatim: a rubric row self-references, so there is no neighbour issue to copy — its reasoning *is* the payload, and it must be reviewed before Apply. |

Note the deliberate collapse: **manual, below-bar, and rubric all land in `selectManualChoice`.** Only
the classical advisor uses `suggestChoice`. This is why the below-bar adopt can never accidentally
send `userChoice: 1` on an endorsed row, and why the training feedback stays clean.

---

## 3. The comment editor — when it shows, what fills it

The comment is the reasoning that lands on the item (`issue.comment`). It is the real
`MarkdownEditor` from `selectDefectManually.jsx:259`, wired through the same `handleManualChange`
comment path. In the Bench it lives **inside the verdict bar**, collapsed to a one-line
`Reasoning ▸` summary in the fast case and expanded inline by the `✎ edit reasoning` affordance (or
whenever it is prefilled with text worth reading).

Prefill is decided by **how the decision was reached**, not by which defect was chosen:

| Path | Editor on open | Prefill | Checkbox "attach reasoning as comment" |
|---|---|---|---|
| **Adopt advisor, explanation present** | collapsed one-liner, expandable | the analyzer's `explanation` (from `suggestion.explanation`), captioned **"Analyzer's reasoning (LLM)"**, plain text, editable | **on** — Apply lands the explanation as the comment |
| **Adopt advisor, no explanation** | collapsed, empty | empty (we do **not** silently carry the neighbour's own old comment into the human's words) | off |
| **Manual pick** | opens, focused, empty | empty, light placeholder *"Why this defect? (optional)"* — the stock `shouldFocusCommentWhenEmpty` focus jump (`selectDefectManually.jsx:97`) already does this when a type is chosen and the comment is empty | off |
| **Below-bar adopt** | opens, empty | empty. The analyzer's *why-it-declined* text is shown as read context near the dock, but is **never** injected as the human's comment (Lens 1 rule: don't auto-write "overrode analyzer" into the user's note) | off |
| **Rubric adopt** | opens, expanded, focused | the rubric **why-text** prefilled and editable (patch-D) | on |

Rules that hold across all paths:

- **Plain text only**, same parser discipline the shipped rubric block uses — no HTML injection.
- **The modal never waits on the LLM.** If no `explanation` exists yet (the explainer writes
  `suggestion.explanation` asynchronously and the first open usually races it), the editor is simply
  empty — no spinner, no "generating…". The staleness guard from the contract lens applies: an
  explanation is only prefilled when its label and relevant item still match the row.
- **Editing is free** in every path. The prefill is a starting point, not a lock.

---

## 4. Seeing and comparing the failure logs (R1 / R2 touchpoints)

This lens owns the *decision* surface, but the manual path is where "let me actually read the logs"
matters most, so it names the seams (the full log-compare view is the log-compare lens's job):

- **See the item's own failure logs (R1).** The current item's ERROR logs are already loaded on open
  by `executionSection` via `URLS.bulkLastLogs` and rendered by `TestItemDetails`. The Bench keeps
  them reachable from the **item stripe** as a collapsed *"error log ▸"* preview — one click, in
  place, so a manual picker can answer *what failed?* without leaving the modal and without the modal
  turning into the log viewer.
- **Compare a candidate before adopting it (R2).** Clicking a chip or a dock item to *inspect* (as
  opposed to adopt) opens a **two-column log compare**: left = this item's logs, right = the
  candidate's logs, reusing `TestItemDetails` with the existing `highlightedLogId` /
  `★ Similar Log` marker (`testItemDetails.jsx:101-106`, fed today by `suggestRs.relevantLogId`).
  **Compare is a non-committing side-trip:** it does *not* touch `decisionType` or any slot — it is
  the "is this really the same failure?" check that *precedes* an adopt. The adopt action is a
  separate, explicit click. This keeps the state machine below clean: compare is a self-loop from any
  state.

---

## 5. Scope + final Apply — wire-identical

### Scope ("Apply to")

Reuse `OptionsSection` unchanged; only relabel in plain words:

| `optionValue` (unchanged constant) | Plain-English label in the Bench |
|---|---|
| `CURRENT_EXECUTION_ONLY` | **only this item** |
| `CURRENT_LAUNCH` | **this item + matching "To Investigate" items in the launch** |
| `ALL_LOADED_TI_FROM_HISTORY_LINE` / `WITH_FILTER` | **this item + matching items across launches** |

- Default follows stock: a **To Investigate** item with the analyzer available opens on
  `CURRENT_LAUNCH`; otherwise `CURRENT_EXECUTION_ONLY` (`makeDecisionModal.jsx:75`).
- The scope line sits under the defect selector. Expanding it reveals the same matching-item
  checklist (`ItemsList`), whose logs load via `URLS.logSearch` / `URLS.bulkLastLogs` — untouched.
- **One safety cue, no dialog** (Lens 1 §7): when a **below-bar (declined)** choice is applied to
  more than one item, the bar prepends a quiet line
  *"Applying a choice the analyzer declined, to N items."* It is a cue, not a gate.

### Apply

Apply is the stock `applyChanges()` → `saveDefect()` with nothing changed:

1. `prepareDataToSend()` builds `issues` from `modalState[ACTIVE_TAB_MAP[activeTab]].issue` over
   `[...currentTestItems, ...selectedItems]` — the scope list above.
2. `PUT URLS.testItems` writes the defect + comment (`autoAnalyzed: false`).
3. When `suggestedItems.length > 0`, `sendSuggestResponse()` echoes every `suggestRs` row, setting
   `userChoice: 1` on the adopted one — now including a below-bar row a human adopted, which is
   free calibration signal the tabbed modal could not collect.
4. `modalHasChanges` still gates the Apply button (defect differs from `itemData.issue`), so an
   accidental Enter on an unchanged item is a no-op — same as today.

**Keyboard.** `Enter` = Apply (the stock `hotKeyAction.ctrlEnter`, rebound to bare Enter for the
Bench). While the comment editor holds focus, Enter is a newline and **Ctrl/Cmd+Enter** applies —
so the editor never traps the triager. `1` / `2` pick the two verdicts in a split; `M` opens the
manual selector; `Esc` cancels. No confirm dialog anywhere.

---

## 6. The full state machine

```
                              ┌─────────────┐
                              │   OPEN       │  suggest fetch in flight
                              │  (loading)   │  URLS.MLSuggestions / …ByCluster
                              └──────┬───────┘
                                     │ reply resolves → route by Bench state
        ┌────────────────┬──────────┼───────────────┬─────────────────────┐
        ▼                ▼          ▼                ▼                     ▼
   S1 unanimous     S2 split    S3 cold-start    S4 empty bench      degraded/bulk
   verdict bar      bar EMPTY   bar EMPTY        bar EMPTY,          (stock flow,
   PRE-FILLED       (a split    (rubric only)    manual = primary    out of scope)
   (ML slot)        demands                      focus
                    a choice)
        │                │          │                │
        └───────┬────────┴────┬─────┴───────┬────────┘
                │             │             │
       ADOPT ADVISOR    MANUAL PICK   BELOW-BAR ADOPT        RUBRIC ADOPT
       chip / 1 / 2     selector / M  dock "Decide…"         "Accept & edit"
       decisionType=ML  decisionType  decisionType=MANUAL    decisionType=MANUAL
       suggestChoice=   =MANUAL       highlight only,        issueType set +
       row              issueType set human must click type  why-text prefilled
                │             │             │                     │
                │      (compare logs = non-committing self-loop from any state, R2)
                │             │             │                     │
                └─────────────┴──────┬──────┴─────────────────────┘
                                     ▼
                              ┌─────────────┐   edit comment (self-loop)
                              │   REVIEW     │◀──┐ change scope (self-loop,
                              │ comment +    │───┘ may fetch matching-TI logs)
                              │ scope shown  │
                              └──────┬───────┘
                       Enter /       │        Esc
                       Apply btn     │         └────────────▶ CANCEL (hideModalAction)
                       (modalHasChanges)
                                     ▼
                         ┌───────────────────────┐
                         │  APPLY                 │
                         │  prepareDataToSend →   │
                         │  saveDefect (PUT) +    │
                         │  sendSuggestResponse   │
                         │  → hideModalAction     │
                         └───────────────────────┘
```

**Transition table (the contract for implementation/QA):**

| From | Event | Guard | Effect |
|---|---|---|---|
| OPEN | suggest reply | — | route to S1/S2/S3/S4; seed verdict bar (S1 pre-fills ML slot, others empty) |
| any decided/undecided | adopt advisor | row is `suggest`/`auto` band | `decisionType=ML`; `suggestChoice=row`; prefill explanation |
| any | manual pick | — | `decisionType=MANUAL`; set `issueType`; focus empty comment |
| any | below-bar adopt | — | `decisionType=MANUAL`; **highlight** declined type; `issueType` empty until click |
| S3 | rubric adopt | — | `decisionType=MANUAL`; set `issueType`; prefill why-text; open editor |
| any | compare logs | candidate has logs | open 2-column compare; **no** state/slot change |
| REVIEW | edit comment | — | update slot `.issue.comment` (self-loop) |
| REVIEW | change scope | — | update `optionValue` / `selectedItems` (self-loop; may fetch logs) |
| REVIEW | Apply / Enter | `modalHasChanges` | `saveDefect` (+`sendSuggestResponse`), close |
| any | Cancel / Esc | — | `hideModalAction` |

Every transition is a single `setModalState` on the existing shape. **No confirm dialogs, no
blocking, no new commit path** — the Bench only decides *what fills the one slot* the stock
`prepareDataToSend` already reads.

---

## 7. Plain global English (R4) — the words we actually show

Our users are worldwide and many are non-native readers. Short common words; the real analyzer label
kept only where it is the true name, and glossed once.

| We show | Not | Note |
|---|---|---|
| the analyzer is not sure / analyzer declined this | sub-0.45 abstain, below-threshold | |
| matching "To Investigate" items in the launch | co-failure corpus, cluster cohort | |
| how sure the analyzer is | confidence band / calibrated proxy | number still shown, with a plain word |
| Similar Log (marks the matching line) | relevantLogId, anchor span | keep the shipped label |
| you picked this | manual override | provenance tag |
| Analyzer's reasoning (LLM) | rubric why-text, sidecar explanation | one gloss, then plain |
| Apply to: only this item | scope = CURRENT_EXECUTION_ONLY | |
| this item + matching items in the launch | CURRENT_LAUNCH fan-out | |
| Reasoning (editable) | comment payload | |
| Why this defect? (optional) | comment placeholder | |

Rule: never make the reader learn a word to make a decision. If a plain word exists, use it; if the
real term must appear (defect names, "To Investigate", "Similar Log"), keep it and gloss it once.

---

## 8. What this lens deliberately does not change

- No new endpoint, no new payload shape: `prepareDataToSend`, `saveDefect`, `sendSuggestResponse`,
  `URLS.testItems`, `URLS.choiceSuggestedItems` all untouched.
- No second decision variable: still one `decisionType`, still `ACTIVE_TAB_MAP`.
- No dialog, no LLM wait, no auto-injected human comments.
- Log **compare** is named as a seam and reuses `TestItemDetails` + `★ Similar Log`; the full diff
  presentation belongs to the log-compare lens.
- Bulk / cluster and analyzer-off keep the stock flow (the Bench is a single-item instrument), the
  same boundary the stock modal already draws with `isMLSuggestionsAvailable`.
</content>
</invoke>
