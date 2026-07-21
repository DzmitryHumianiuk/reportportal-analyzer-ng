# Lens — Bench logs: the failure under test + precedent log compare (R1 + R2)

Refines **variant C ("The Bench")** so the triager can (R1) **see the real failure logs of the
item under test**, and (R2) **compare a candidate's logs against them** ("is this really the
same failure?") — without turning the modal into a log viewer.

Grounded in the real modal code (`/tmp/rp-ui-patch` = patched UI ng1):

- Current item ERROR logs already load in `executionSection.jsx:65` via
  `URLS.bulkLastLogs(project)` `POST {itemIds:[id], logLevel:'ERROR'}` → `response[id]`, an
  array of `{id, level, message}`, kept in `modalState.currentTestItems[0].logs`.
- Each candidate's neighbor logs **already ship** in the first `URLS.MLSuggestions` response
  (`makeDecisionModal.jsx:127`) as `{testItemResource, logs, suggestRs}` — **no extra fetch is
  needed to compare** (`makeDecisionTabs.jsx:62`, `suggestChoice: {...testItemResource, logs,
  suggestRs}`).
- The decisive neighbor line is already marked: `TestItemDetails` takes
  `highlightedLogId={suggestRs.relevantLogId}` + `highlightedMessage="Similar Log"` and draws the
  asterisk marker (`machineLearningSuggestions.jsx:113`, `testItemDetails.jsx:101`). **Reuse this
  marker — do not reinvent it.**
- Logs render through `StackTraceMessageBlock` (dark mode, `maxHeight` with internal scroll),
  capped at `ERROR_LOGS_SIZE = 5` (`constants.js:26`).
- The analyzer computes masked templates / signatures / `error_hash` internally
  (`core/analysis.py`, `core/decision.py`) but **only raw log `message` strings reach the UI**
  today. That single fact decides the diff design (§3).

Plain-English rule (R4): the panel is labeled **"This failure"**, the action is **"Compare
logs"**, the two sides are **"same in both"** and **"only here / only there"**. No "co-failure
corpus", no "sub-threshold", no "signature vector". Where a real analyzer term must appear it is
glossed in one short clause.

---

## 1. Where the failure logs live in the Bench — the "This failure" panel

The stock modal put the failure log in a left column the Bench dropped. We put it back as **one
always-present line + one collapsed strip**, sitting **between the item stripe and the bench**, so
it is glanceable but never outweighs the verdict.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ITEM STRIPE   attributes check · FAILED · To Investigate    [Inspector ↗] │
├──────────────────────────────────────────────────────────────────────────┤
│ THIS FAILURE   ✦ expected undefined to deeply equal { Object }   [Compare │  ← always one line
│                AssertionError · attributes.spec.js:41         full log ▾]  │    (the top ERROR)
├──────────────────────────────────────────────────────────────────────────┤
│ THE BENCH    Precedent · Similarity · Rubric      ┆ 2 below the bar ┆      │
├──────────────────────────────────────────────────────────────────────────┤
│ EVIDENCE STAGE   (verdict evidence, or the log compare when a chip is open)│
├──────────────────────────────────────────────────────────────────────────┤
│ VERDICT BAR   [ Product Bug ▾ ]  ☑ attach reasoning as comment   Apply…    │
└──────────────────────────────────────────────────────────────────────────┘
```

**The always-visible line.** One row, never collapsed: the **first ERROR message** (the line the
analyzer keys on — its "failure fingerprint"), clamped to one line, with a small second line for
the exception class + failing frame when the log carries them. This is `currentTestItems[0].logs`
filtered to the top ERROR entry. It answers step 1 of triage ("what failed, and how?") in one
glance and costs zero clicks.

**The collapsed strip — "full log ▾".** One click expands the full failure log **in place**,
directly under the line, using the existing `TestItemDetails` render (ERROR logs first,
`ERROR_LOGS_SIZE = 5`, each block `maxHeight ~110px` with internal scroll). Rules that keep it
from dominating:

- **ERROR first, always.** Same `logLevel:'ERROR'` fetch already in `executionSection`; no
  WARN/INFO noise in the modal (deeper levels are an Inspector job).
- **Hard height cap** on the whole strip (~40% of the modal body); it scrolls inside itself, it
  never pushes the bench or the verdict bar below the fold at 1440×900.
- **Collapsed by default when the bench has a confident verdict** (S1 unanimous, S2 split): the
  verdict evidence reads first; the raw log is one keystroke away for the doubter.
- **Auto-expanded only in S4 (empty bench)**: when nobody advised, the raw failure log *is* the
  primary content the human works from, so it opens itself — the same "auto-expand only when it is
  the only content" rule the dock already follows.
- Sticky per-user preference (localStorage), same pattern as the dock toggle, so a heavy reader
  is not taxed with a click per item.

**Why a strip, not a left rail.** The Bench is full-height and single-column by design; a
persistent left log rail would re-import the two-column stock layout the radical variant
deliberately abandoned, and would fight the evidence stage for the same horizontal room the
**compare** needs (§2). A top strip stacks cleanly and hands the full width to the compare view
when it opens.

---

## 2. The compare interaction — "same failure?" in one screen

**Trigger.** Clicking a candidate — a **bench chip** (Precedent / Similarity) or a **dock item**
(below-the-bar) — opens the compare in the **evidence stage**. Clicking **still selects nothing
and applies nothing** (the lens-radical rule: chips focus evidence, the verdict bar commits). The
compare is *inspection*; the decision stays a separate, explicit act. A **"Compare logs"** button
also sits on the always-visible "This failure" line and on each chip, so the affordance is named,
not just discovered.

**Layout — side-by-side, this failure vs the candidate's failure:**

```
┌ EVIDENCE STAGE · Compare logs ─────────────────────────────────────────────┐
│  THIS FAILURE                      │  SIMILARITY · Product Bug · 0.81        │
│  attributes check (under test)     │  attributes check · launch #266  [why↗] │
│  ────────────────────────────────  │  ─────────────────────────────────────  │
│  ✦ expected undefined to deeply    │  ✦ Similar Log                          │  ← relevantLogId,
│    equal { Object (key, value) }   │    expected undefined to deeply equal   │    the pinned twin
│    at attributes.spec.js:41        │    { Object (key, value) }              │
│    at attributes.spec.js:41        │  · at runner.js:88   ← only there       │  ← differs → hot
│  · product returned no payload     │  · gateway timeout after 30s ← only there│
│    ← only here                     │                                         │
│  [ open full log ↓ ]               │  [ open full log ↓ ]                     │
│  same in both: assertion + frame · differs: downstream cause                 │  ← one-line verdict
│  Not the same failure?  →  Decide this yourself…      Deeper → Inspector ↗   │
└─────────────────────────────────────────────────────────────────────────────┘
```

- **Left = "This failure"** = `currentTestItems[0].logs`. **Right = the candidate's failure** =
  `suggestChoice.logs`, both already in `modalState` — the compare opens instantly, no spinner,
  no network.
- **The decisive line is pinned to the top of the right column** using the existing
  `relevantLogId` + "Similar Log" asterisk marker, and its **twin on the left is aligned to the
  same row** so the eye reads the match without scrolling. This is the "why they might share a
  cause" anchor.
- **Diff coloring (the honest part):** lines that appear in **both** are dimmed (the common trunk —
  *why they might be the same*); lines that appear in **only one side** are hot (*the reason to
  doubt*), labeled **"only here"** / **"only there"** in plain words rather than red/green alone
  (color-blind safe, and R4-plain).
- **One-line compare verdict** under the columns, built from the diff, e.g. *"same in both:
  assertion + failing frame · differs: downstream cause"* — the single sentence a triager needs to
  answer "same failure?".
- **Stacked, not side-by-side, on narrow width** (< ~980px): "This failure" on top, candidate
  below, the pinned twin still aligned by a connecting marker. The bench-modal is `min-width
  1080px` today, so side-by-side is the normal case; stacking is the graceful fallback.
- **Hard height cap; each column scrolls inside itself.** The stage never grows the modal. Full,
  unclamped logs and the *other* candidates live one click deeper in the Inspector journey
  (`why ↗` / `Deeper → Inspector ↗`, new window) — the anti-second-log-viewer valve.
- **Rubric has no compare.** The rubric advisor's `relevantItem` is a self-reference (no real
  neighbor), so clicking the rubric chip shows its why-text plus the **one failing line it fired
  on**, marked inside the left "This failure" column — a single-column highlight, not a two-column
  diff. Comparing a failure against itself would be a lie; we do not draw it.

---

## 3. Which logs, and raw-line vs template diff — the decision

**Decision: v1 is a client-side raw-log line diff with light token-masking; the precise
template diff is a v2 contract upgrade. Ship v1 now.**

Why: the analyzer computes masked templates (the ERROR line with its changing bits — numbers,
ids, ports, timestamps — blanked out so two runs of the same failure match) and keeps
`error_hash` / `jaccard_templates` internally (`core/analysis.py`, `core/decision.py:274,490`),
**but none of that reaches the UI** — `suggestRs` carries only raw `message` strings (plus
opaque `modelFeatureNames/Values`). So a *semantically correct* template diff is not buildable in
the browser today from real data, and inventing one would violate "real data only".

- **v1 (ships with ng2, no new wire field):** diff the raw ERROR `message` lines of the two sides
  in the browser. Before comparing, run a **small masking pass** that blanks the volatile tokens
  (integers, hex/uuids, timestamps, file paths' line numbers, ports) so `HTTP 500` and `HTTP 503`
  read as **same line, one differing token** instead of two unrelated lines — this mirrors what
  the analyzer's masking does, just approximately, and only for *alignment*; the **raw** text is
  always what's shown. Deterministic, offline, honest about being approximate.
- **v2 (the clean upgrade):** the analyzer already knows the exact shared vs differing masked
  templates. Add `evidenceSpans: [{logId, lineStart, lineEnd, role: "match" | "tiebreak"}]` to the
  contract (the field lens-radical already proposed, carried via the `modelInfo` microformat per
  the transport rules in lens-contract §2.2). Then the compare highlights the **authoritative**
  shared span (`role:"match"`) and the **decisive** differing span (`role:"tiebreak"`), and the
  client-side masking becomes a fallback for legacy analyzers. Same UI, better inputs — a
  transport swap, not a redesign.

Either way the compare renders through the existing `StackTraceMessageBlock` + the "Similar Log"
marker; the only new UI is the two-column frame and the dim/hot line styling.

---

## 4. How the tiebreaker line relates to the compare

They are the **same evidence at two depths** — summary and detail:

| | Tiebreaker line (S2 split) | Compare (this lens) |
|---|---|---|
| **What** | The one span that most separates the two verdicts | The full failure log of both sides, diffed |
| **Source** | `evidenceSpans` `role:"tiebreak"` (v2); in v1, the single top differing line the client diff finds | `currentTestItems[0].logs` vs `suggestChoice.logs`, already loaded |
| **Cost** | 0 clicks — it crowns the split view | 1 click — open a chip / "Compare logs" |
| **Job** | *"Here is the one line of tension — which reading holds?"* | *"I don't trust that one line; show me everything and let me judge."* |

The tiebreaker is the analyzer's **auto-picked** one-line answer. The compare is the human's
**manual verification** of that claim. So **"Compare full logs" is offered directly on the
tiebreaker line**: clicking it opens the two-column compare with the **tiebreak span already
aligned and marked** in both columns — the human lands exactly on the line the analyzer flagged,
then scrolls the rest to confirm or reject. Deepest of all (every candidate, the feature vector,
the journey) stays the Inspector permalink, new window. Three depths, one story:
**tiebreaker line → compare → Inspector.**

---

## 5. Compare → decide (handoff to the next-step flows, R3)

The compare never commits — but it must hand off cleanly to the two decision paths (detailed in
the sibling lens):

- **"Yes, same failure" → adopt the candidate's verdict.** From an above-the-bar chip's compare,
  the verdict bar is already pre-set to that verdict (S1) or selectable by `1`/`2` (S2); `Enter`
  applies. The apply wire is unchanged — `prepareDataToSend` copies the neighbor's issue,
  `sendSuggestResponse` marks `userChoice:1` on that row.
- **"No, not the same failure" → decide it yourself.** The compare's footer offers **"Not the same
  failure? → Decide this yourself…"**, which jumps focus to the verdict bar's manual defect
  selector (defect *pre-highlighted but not selected* when the compared candidate was a below-the-
  bar dock item, so adoption stays an explicit human act). This is the same manual path the empty
  bench (S4) makes primary.
- **Reasoning into the comment.** Whichever way it goes, the "attach reasoning as comment" flow is
  untouched by the compare; when the candidate carried an explanation it prefills as today. The
  compare only *informs* the comment — it never writes to it.

Keyboard inside the compare: `Esc` closes the compare back to the verdict evidence (does **not**
cancel the modal); `Enter` still applies the current verdict-bar selection; `1`/`2` still pick a
side in a split. The compare adds no new commit keys — it is a lens, not a decision.

---

## 6. Build notes / feasibility

- **No new fetch** for either R1 or R2: current-item logs come from the `bulkLastLogs` call that
  already runs on modal open; candidate logs are already in the `MLSuggestions` payload. The
  compare is a pure render over `modalState`.
- **Reuse** `TestItemDetails`, `StackTraceMessageBlock`, the `relevantLogId` / "Similar Log"
  asterisk marker, the dark `getLogLevelStyles` coloring, the dock/collapse localStorage pattern.
  New code is: the "This failure" strip, the two-column compare frame, the line-diff + masking
  helper, and the dim/hot line styles.
- **Degraded states:** if a candidate's `logs` array is empty (neighbor had no ERROR log — the
  analyzer's empty-signature case, `analysis.py:289`), the compare shows the left "This failure"
  column plus an honest *"This candidate has no error log to compare."* on the right — never a
  fabricated diff. Bulk mode keeps the stock flow (the Bench, and this compare, are single-item
  instruments, same boundary `isMLSuggestionsAvailable` already draws).
- **Guardrail:** the compare's max-height and internal scroll are the contract that keeps the
  modal a *decision accelerator*, not a log viewer. The moment a user wants full logs, more levels,
  or other candidates, the honest answer is the Inspector link — not a taller modal.
</content>
</invoke>
