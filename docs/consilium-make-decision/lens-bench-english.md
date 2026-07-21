# Lens — Plain Global English for the Bench (R4)

The exact on-screen words for the Bench redesign (variant C). Our users are worldwide and
many read English as a second language. Every string here follows the same rules and is
ready to drop into `localization/messages/**` as the source `en` message.

Grounded in: the Bench concept (`lens-radical.md`), the wire contract (`lens-contract.md`),
the workflow spec (`lens-triage.md`), the current mockup (`mockup.html`, variant C section),
and the stock modal strings in
`/tmp/rp-design/service-ui/app/src/pages/inside/stepPage/modals/makeDecisionModal/**`.

---

## 0. Voice rules (applied to every string below)

1. **Short common words.** "said no", not "declined the candidate". "looks the same", not
   "high lexical overlap".
2. **Active voice, short sentences.** "The checks agree." Not "Agreement has been reached."
3. **No idioms, no metaphors.** No "raise the bar", no "bench of advisors", no "cold start"
   in the visible UI. These stay as internal/design names only.
4. **≤ ~12 words** per primary (always-visible) string. Longer text is allowed only inside a
   tooltip or an expandable block.
5. **Say the number's meaning, not its jargon.** "right about 80% of the time", not
   "calibrated p*".
6. **Keep a real analyzer/RP label only when it is the true name of a thing** (launch,
   Inspector, defect type, To Investigate). Gloss it once, in a tooltip.
7. **One word per idea, everywhere.** A thing the analyzer would not pick is always
   **"said no"** — never also "declined", "rejected", "below bar", "abstained".

---

## 1. The three checks — names + one-line role (the core decision)

The concept calls them Precedent / Similarity / Rubric. Those are engineer words. The user
brief proposed "Past decision / Similar failures / AI guess" — we adopt that family, with
small edits, and justify each.

| Internal name | **On-screen name** | One-line role (shown under the name / as tooltip) |
|---|---|---|
| Precedent (Stage-A exact match) | **Past decision** | "A person already decided this exact same failure." |
| Similarity (GBM) | **Similar failures** | "Failures that look like this one, from a trained model." |
| Rubric (LLM cold-start) | **AI guess** | "No earlier match — the AI reasons from a set of rules." |

We also need a plain word for the three as a group. **"check"** — the analyzer *checked*
this failure three ways. We never say "advisor" or "bench" on screen.

Group heading (replaces "The bench — advisors, left→right = decisive power"):

> **The analyzer checked this three ways** — strongest first

**Why these names (justification):**

- **"Past decision"** over "Precedent": *precedent* is a legal/formal word many non-native
  readers must translate. "Past decision" says exactly what Stage-A does — it found the
  identical failure and reuses the label a **person** already chose. The word *person*
  (in the role line) is the trust signal: this is the strongest check because a human, not a
  model, made the call.
- **"Similar failures"** over "Similarity": a plural noun ("failures") reads as *things you
  can go look at*, which is true — they are real neighbor items. "Similarity" is an abstract
  quality; "Similar failures" is concrete.
- **"AI guess"** over "Rubric" / "hypothesis" / "AI suggestion": three reasons.
  (a) *Rubric* and *hypothesis* are jargon. (b) **"guess" is deliberately humble** — it must
  never read as strong as the two evidence-backed checks, and "guess" caps its authority in
  one plain word, which is exactly the "provisional" framing the concept demands.
  (c) We must **not** use the word *suggest/suggestion* here, because "Suggested" is the
  reserved band word (see §2) — reusing it would blur "the AI has a hunch" with "the
  analyzer suggests, please confirm".

---

## 2. Band words — how strong each check is

The band is the analyzer's own confidence level. One phrase per band, used on the check
card, in the compare header, and in telemetry labels.

| Band (confidence) | **Primary phrase** | **Short chip** (tight spaces) |
|---|---|---|
| **auto** (0.75 and up) | **Will apply on its own** | Strong match |
| **suggest** (0.45–0.75) | **Suggested — please confirm** | Please confirm |
| **abstain** (below 0.45) | **Not sure — your call** | Your call |
| **rubric** (AI guess) | **A guess — check it first** | Not confirmed |

Rules:

- The word **"Suggested"** is only for 0.45 and up. It never appears on a below-line item or
  on the AI guess.
- The word **"guess"** is only for the AI guess row. Real neighbors are never a "guess".
- Always show the band phrase next to any number, so a bare "42%" one row under a bare "81%"
  never reads as a ranking. (See §3 for the exact number wording.)

---

## 3. The check cards (the three chips)

Per card, top to bottom. Values shown are the mockup's real stand data.

**Past decision card (auto band):**

- Name: **Past decision**
- Defect pill: `● Product Bug` *(keep RP defect colors + names — the user's learned words)*
- Strength line: **Exact same failure — strong enough to apply on its own**
- Who decided: **d.gumeniuk decided this in launch #266**  *(launch = tooltip, §7)*

**Similar failures card (suggest band):**

- Name: **Similar failures**
- Defect pill: `● Product Bug`
- Strength line: **0.81 alike — suggested, please confirm**
- Who decided: **d.gumeniuk decided this in launch #266**

**AI guess card (rubric):**

- Name: **AI guess**  ·  small tag: **not confirmed**
- Defect pill: `● Automation Bug`
- Strength line: **The AI's own guess (65%)** — *not a real match* (tooltip §7)
- Origin: **No earlier match to lean on**

---

## 4. Agreement state — the headline the modal leads with

The Bench leads with whether the checks agree. Three states, three headlines.

| State | Concept term | **On-screen headline** | Helper line under it |
|---|---|---|---|
| S1 | unanimous | **The checks agree — Product Bug** | Press **Enter** to set it. |
| S2 | split | **The checks do not agree** | Read the one line below, then pick. |
| S3 | cold start | **Only the AI has a guess** | No past match and nothing similar. |
| S4 | empty bench | **The analyzer is not sure about this one** | Nothing was a strong match. You decide. |

Notes:

- **"do not agree"** over "split bench" / "disagreement view" — "split" is idiomatic here.
- **"The analyzer is not sure"** is the exact phrasing the brief asked for, and it is the
  honest frame for S4: the machine looked and could not decide. It never says "no results"
  (there *are* below-line items) and never says "failed".

---

## 5. The evidence area

Region heading (replaces "Evidence stage"):

> **What the analyzer saw**

### 5.1 The one deciding line (S2 split — the "wow" line)

Replaces "Tiebreaker — the one span that separates the verdicts":

- Label: **The one line that splits the two answers**
- The line itself: rendered as-is (real log span), the deciding token highlighted.
- Helper sentence (plain rewrite of the mockup's tension note):

  > The past match read `undefined` as a **product bug**. The AI read the same
  > `undefined` as a **test-code fault**. Decide which reading is right.

If the analyzer cannot produce this line, hide the whole block — show only the compare view
(§6). Never invent a "tension" sentence with no real line behind it.

### 5.2 The two-answer columns (S2)

- Left column heading: **Product Bug — from the past match + similar failures**
- Right column heading: **Automation Bug — from the AI guess (not confirmed)**
- Left footer (who): **d.gumeniuk decided this in launch #266**  ·  link **why? ↗**
- Right footer (origin): **A guess — no earlier match**

### 5.3 S1 unanimous evidence

- Verdict line (replaces "precedent (exact match) and similarity (0.81) agree"):

  > **Product Bug** — the exact match and the similar failures both point here.

- Proof caption: **The failure this matched**
- Who: **This match was decided by d.gumeniuk in launch #266**
- LLM block caption (replaces "Analyzer's reasoning (LLM)"):

  > **Why the analyzer thinks this** (written by AI)

- Link at its end: **See the full reason in Inspector ↗**

### 5.4 S3 cold-start evidence

- Verdict line (replaces the mockup's S3 line):

  > No past match and nothing similar. Only the AI's guess is left.

- Guess caption (replaces "AI hypothesis — why this defect"):

  > **The AI's guess — why**

- The rule line (replaces "R1 fired on:"): **The rule matched this line:**
- Review note (replaces the "Provisional never gets the one-keystroke path" line):

  > A guess is never applied for you. Read it, edit the comment, then apply.

### 5.5 S4 empty-bench evidence

- Big line: **The analyzer is not sure about this one.**
- Sub line: **Nothing was a strong match. The list it said no to is open below. Pick a
  defect type yourself.**
- Link: **Open full details in Inspector ↗**

---

## 6. See the real failure logs, and compare them (R1 + R2)

This is the part the Bench under-served. Two jobs: **(R1)** see this item's own failure log
without leaving the modal, and **(R2)** compare a candidate's log against it to judge "is
this really the same failure?".

### 6.1 See this failure's log (R1)

- On the item stripe, a quiet toggle (reuses stock `Show Error Logs`):
  - closed: **Show error log**
  - open: **Hide error log**
- Section title when open: **This failure's error log**
- Reuse the existing log renderer and the **★ Similar Log** marker as-is; only the label
  wording below is new.

### 6.2 Compare with a candidate (R2)

When the triager clicks a check card or a below-line item they might pick, offer a compare:

- Button on the card: **Compare logs**  (tooltip: "See this failure next to that one, side
  by side.")
- Compare header: **Is this the same failure?**
- Left panel title: **This failure** *(the item under analysis)*
- Right panel title: **The one you picked** *(the candidate)*
- The two legend labels the brief asked for, used to color the diff:
  - **Same in both** — lines that match, shown dimmed.
  - **Different** — lines that differ, highlighted.
- One-line helper under the header: **Green lines match. Highlighted lines differ. If the
  key error line matches, it is likely the same failure.**
- Close: **Close compare**

This stays a small two-panel diff — never the full log viewer. "See every log line" is an
Inspector link, not an in-modal panel.

---

## 7. Below the line — the items the analyzer said no to (the dock)

Replaces the mockup's "2 below the bar (analyzer declined)" dock. This must **never** read as
endorsed.

- The line marker (replaces "the bar · 0.45"):
  **the line the analyzer must clear** *(tooltip: see §8, "the line")*
- Dock toggle (collapsed) — replaces "2 below the bar (analyzer declined)":
  **The analyzer said no to these (2)**
- Dock note (replaces "The analyzer declined these. Shown for your judgment only."):
  **The analyzer looked at these and said no. Shown only so you can decide.**
- Per item:
  - Name: `EPMRPP-86731. attributes check` *(kept as-is)*
  - Defect + why weak (replaces "Product Bug — declined at 0.42"):
    **~~Product Bug~~ — too weak to suggest (0.42)**
  - The one action (replaces "Decide this yourself…"):
    **Choose this myself…**
  - Link: **why? ↗**
- Hard rule kept in copy: nothing here is ever pre-picked, and **Enter never applies a
  below-line item**. If the user opens `Choose this myself…`, the defect is highlighted but
  **not** selected — they must click it, so the choice is clearly theirs.

---

## 8. Tooltips that gloss a real analyzer term (define jargon once)

Each real term keeps its label on screen and carries **one** plain tooltip. Written once,
reused everywhere the term shows.

| Term (stays on screen) | **Tooltip (plain, one definition)** |
|---|---|
| `error_hash` / "exact same failure" | "A fingerprint of the error text. The same fingerprint means the same failure." |
| "confidence" / the % number | "Confidence is checked against real cases: 80% means it is right about 80 out of 100 times." |
| "AI guess" / cold start | "No earlier example to learn from, so the AI reasons from a set of rules instead of real matches." |
| "0.81 alike" (similarity score) | "How alike the two failures are, from 0 (nothing alike) to 1 (identical)." |
| "the AI's own guess (65%)" (rubric confidence) | "This is the AI's own guess about a rule, not how alike two real failures are. Treat it with care." |
| the line the analyzer must clear (0.45) | "Below this line the analyzer will not suggest an answer. It only shows these for you to judge." |
| "Will apply on its own" (auto, 0.75) | "At this level the analyzer is sure enough to set the defect without asking." |
| launch (#266) | "A launch is one test run in ReportPortal." |
| Inspector | "Inspector is the companion tool that shows the full history and reasoning for an item." |
| To Investigate | *(keep RP's own tooltip — it is a defect type users already know.)* |
| "not confirmed" / provisional | "Not checked yet. Read it before you use it." |

---

## 9. Manual, comment, scope, apply (R3)

### 9.1 The manual path — no check is right, pick by hand (R3a)

- Verdict-bar button (replaces "Manual…"): **Choose myself**  (key: `M`)
- Manual pane heading (replaces stock "Select defect manually" / "Select the defect type
  manually"): **Pick a defect type**
- Helper under the pills: **Pick the defect type that fits this failure.**
- The path is frictionless: the four defect pills are already there; clicking one fills the
  verdict bar. No extra dialog.

### 9.2 The comment — the reason that lands on the item (R3b)

- Verdict-bar checkbox (replaces "attach reasoning as comment"):
  **Add the reason as a comment**  (on by default when the analyzer wrote one)
- Edit affordance next to it: **Edit comment first**
- Comment editor label (when it opens): **Comment** *(tooltip: "This text is saved on the
  item for the next person.")*
- Prefill note when the AI text is dropped in (builds on the patched manual-tab prefill):
  **Filled from the AI's reason. Change it if you want.**
- S3 apply button (replaces "Accept & edit comment"):
  **Use this and edit the comment**

The rule: a normal endorsed pick applies in one step; the AI guess always opens the comment
for a quick read first, because its reason *is* the payload.

### 9.3 Scope — how many items this applies to

- Label (stock "Apply for:"): keep **Apply to:**
- Options (plain rewrites of the stock scope strings; keep the RP filter names):
  - `Current item only` → **Just this item**
  - `Similar "To Investigate" in the launch & current item` →
    **This item + similar "To Investigate" ones in this launch**
  - `Similar "To Investigate" in 10 launches & current item` →
    **This item + similar ones across 10 launches**
- Count line (stock "{selected}/{total} items selected"): **{selected} of {total} items
  chosen**

### 9.4 Apply + confirm

- Apply button: **Apply** *(unchanged — already plain)*
- Keyboard legend (replaces "Enter apply · 1/2 pick verdict · M manual · Esc cancel"):
  **Enter** apply · **1**/**2** pick answer · **M** choose myself · **Esc** close
- Verdict-bar select, empty in a split (replaces "Pick a verdict"): **Pick your answer**
- Verdict-bar select, empty in S4 (replaces "Select defect (manual)"): **Choose a defect
  type**
- Footer recap, normal (stock): **This will be applied to the item.**
- Footer recap when the user overrules a below-line pick at fan-out (replaces the
  "Declined-band choice → N items" cue):
  **Your own choice (the analyzer said no) → {count} items**

---

## 10. Before → after (vs the current mockup / lens wording)

| Where | Current wording | **New plain wording** |
|---|---|---|
| Group heading | "The bench — advisors, left→right = decisive power" | **The analyzer checked this three ways — strongest first** |
| Check 1 name | Precedent | **Past decision** |
| Check 1 role | "labeled by d.gumeniuk, launch #266" (only) | **A person already decided this exact same failure** + who |
| Check 2 name | Similarity | **Similar failures** |
| Check 2 strength | "0.81 · suggest band" | **0.81 alike — suggested, please confirm** |
| Check 3 name | Rubric | **AI guess** |
| Check 3 strength | "65% rubric confidence" | **The AI's own guess (65%) — not a real match** |
| Check 3 origin | "cold-start hypothesis · no precedent" | **No earlier match to lean on** |
| Band auto | "would auto-apply" | **Will apply on its own** |
| Band suggest | "suggest band" | **Suggested — please confirm** |
| Band abstain | "declined at 0.42" / "below bar" | **Not sure — your call** / **too weak to suggest (0.42)** |
| Tag | "provisional" | **not confirmed** |
| S1 headline | (implicit) | **The checks agree — Product Bug** |
| S1 verdict line | "precedent (exact match) and similarity (0.81) agree" | **The exact match and the similar failures both point here** |
| S1 LLM caption | "Analyzer's reasoning (LLM)" | **Why the analyzer thinks this (written by AI)** |
| S2 heading | "Split bench" | **The checks do not agree** |
| S2 tiebreaker | "Tiebreaker — the one span that separates the verdicts" | **The one line that splits the two answers** |
| S2 left col | "Product Bug — Precedent + Similarity" | **Product Bug — from the past match + similar failures** |
| S2 right col | "Automation Bug — Rubric (provisional)" | **Automation Bug — from the AI guess (not confirmed)** |
| S3 line | "No precedent, no neighbour above the bar — only the rubric hypothesis" | **No past match and nothing similar. Only the AI's guess is left** |
| S3 caption | "AI hypothesis — why this defect" | **The AI's guess — why** |
| S3 review note | "Provisional never gets the one-keystroke path…" | **A guess is never applied for you. Read it, edit the comment, then apply** |
| S4 big line | "The analyzer declined to advise on this item" | **The analyzer is not sure about this one** |
| S4 sub | "Nothing cleared the 0.45 bar…" | **Nothing was a strong match. The list it said no to is open below. Pick a defect type yourself** |
| Evidence region | "Evidence stage" | **What the analyzer saw** |
| Error log (R1) | *(missing in Bench)* / stock "Show Error Logs" | **Show error log** / **This failure's error log** |
| Compare (R2) | *(missing)* | **Compare logs** · **Is this the same failure?** · **Same in both** / **Different** |
| Compare sides | *(missing)* | **This failure** / **The one you picked** |
| The line marker | "the bar · 0.45" | **the line the analyzer must clear** |
| Dock toggle | "2 below the bar (analyzer declined)" | **The analyzer said no to these (2)** |
| Dock note | "The analyzer declined these. Shown for your judgment only." | **The analyzer looked at these and said no. Shown only so you can decide.** |
| Dock item action | "Decide this yourself…" | **Choose this myself…** |
| Inspector link (item) | "Open journey in Inspector ↗" | **Open full details in Inspector ↗** |
| Inspector link (reason) | "See why in Inspector ↗" | **See the full reason in Inspector ↗** |
| Per-row link | "why ↗" | **why? ↗** |
| Manual button | "Manual…" | **Choose myself** |
| Manual heading | "Select the defect type manually" | **Pick a defect type** |
| Comment checkbox | "attach reasoning as comment" | **Add the reason as a comment** |
| Comment edit | (none) | **Edit comment first** |
| Prefill note | (none) | **Filled from the AI's reason. Change it if you want.** |
| S3 apply | "Accept & edit comment" | **Use this and edit the comment** |
| Scope label | "Apply for:" | **Apply to:** |
| Scope option | "Current item only" | **Just this item** |
| Empty select (split) | "Pick a verdict" | **Pick your answer** |
| Empty select (S4) | "Select defect (manual)" | **Choose a defect type** |
| Keys legend | "Enter apply · 1/2 pick verdict · M manual · Esc cancel" | **Enter apply · 1/2 pick answer · M choose myself · Esc close** |
| Footer overrule cue | "Declined-band choice → N items" | **Your own choice (the analyzer said no) → {count} items** |

---

## 11. Localization notes

- Every string above is a candidate `defineMessages` entry; keep the `id` keyed to the
  region (e.g. `bench.check.pastDecision.name`) so translators see context.
- Keep **defect-type names and colors** out of translation — they come from project config
  and are the user's learned words (Product Bug, Automation Bug, System Issue, No Defect).
- Numbers stay as digits; only the *words* around them translate ("alike", "right about … of
  the time").
- The one-definition tooltips (§8) are the only place jargon lives, so a translator localizes
  each real term exactly once.
- Avoid the reserved word collision in other languages too: whatever translates "Suggested"
  (the band) must differ from whatever translates "guess" (the AI row).
</content>
</invoke>
