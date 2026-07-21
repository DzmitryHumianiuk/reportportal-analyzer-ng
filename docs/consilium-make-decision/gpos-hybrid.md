# Group follow-up placement — the case for HYBRID

**Position: HYBRID.** Split the identical-failure group by its two natures and place each where the
triager actually needs it. A **quiet, non-actionable awareness cue at the TOP** (the fact + count,
feeding the hypothesis), and the **actionable fan-out folded into the scope control at the BOTTOM**,
at the commit moment. This is not a compromise — it is the only placement that matches the triager's
real reading order, and it happens to *remove* a redundant control the current mockup carries.

Grounded in: `mockup-bench-rp.html` (the group band, today at TOP, lines 515–537; scope-select in the
verdict bar, lines 734–737; `groupApplyBtn` handler, 1204–1209; `currentScopes` / `setScope` /
`recapText`, 935–950), `lens-triage.md` §1 (the triager's 5-step job and salience tiers), and
`lens-bench-manual.md` §5 (scope = the real RP `OptionsSection` / `ItemsList`, "Apply to" already at
the bottom).

---

## 1. The one fact that decides this: the group is consumed at two different steps

`lens-triage.md` §1 fixes the triager's reading order, and reading order *is* salience order:

| Step | Task | Question in the head | Region |
|---|---|---|---|
| 1. Orient | glance context | *What failed, and how?* | R1 "This failure" |
| 2. Scan | read hypotheses | *Does the machine already know?* | the Bench advisors |
| 3. Pick | choose defect + comment | *Do I agree? What do I write?* | verdict bar |
| 4. **Scope** | apply breadth | *Just this one, or the N similar TI?* | "Apply to:" |
| 5. Commit | Apply | — | Apply button |

The group speaks to **two** of these steps, and they are far apart in time:

- As **context**, it belongs to **step 1→2**. "This exact failure is in 12 tests at once" is evidence
  the triager needs *before* forming a hypothesis. A 12-test simultaneous burst is not a flaky
  one-off; a mass identical failure smells like a shared root cause — one broken build, one infra
  blip, one product regression fanning out. That reading changes what the failure *likely is*, so it
  must land before the advisors are read, not after.
- As **action**, it belongs to **step 4**. "Fan this decision out to all 12" is a *scope* decision.
  Scope decisions happen once, at commit, after a defect is chosen — and the modal already owns a
  scope control that lives exactly there (`data-scope`, verdict bar).

TOP-only serves the context and forces the action too early. BOTTOM-only serves the action and starves
the context. Because the two natures are genuinely consumed at two separated moments, **one widget in
one place must sacrifice one of them.** HYBRID is the placement that refuses the sacrifice.

---

## 2. Half one — the TOP awareness cue (non-actionable)

**Where.** Same slot as today's band: directly under the R1 "This failure" panel, above the Bench
(replacing lines 515–537). It stays in the orient→scan reading path so the count reaches the eye
before the advisors do.

**Salience: P2, not P1.** Per `lens-triage.md` §1, P1 (accent color, reads without a click) is
reserved for the band verdict — *what the analyzer thinks*. The group is context, so it is P2: visible,
one glance to parse, no loud accent. It is a sentence, not a banner with a commit button.

**What it shows — the fact + count only:**

> `◇ 12 tests failed the same way in this run.` &nbsp; *Same error signature, this test included.*
> &nbsp;[ Show the tests ]

- The count badge (`12`) and the one-line fact. No verb, no "Apply", nothing that commits.
- A single quiet reveal, **"Show the tests"**, opening the read-only member list (the existing
  `gm-chip` list, lines 530–536) — chips naming the sibling tests, `(this test)` marked, plus the
  "See the group in Inspector ↗" permalink. This is *awareness* reading: "which tests are these?"
  It carries no checkboxes and touches no scope.
- **Burst tint, encoding scale — never a defect.** When the group is large enough to read as a burst,
  the cue may carry a quiet attention tint (RP topaz-info for a normal group; a soft amber for a big
  simultaneous burst) with a plain gloss: *"A burst of identical failures — likely one shared cause."*
  The tint encodes **how many / how bursty**, and nothing else. It must never borrow a defect-type
  color (the System-Issue blue `--si`, Product-Bug red, etc.), because `lens-triage.md` §3/§4 forbids
  the UI from endorsing a verdict the analyzer has not made. The cue nudges *"treat these as one
  event"*, not *"call it a System Issue."*

**What is NOT here:** the "Apply my decision to all 12" button (today lines 526–528) is **deleted from
the top.** No commit verb appears above the evidence.

**The cross-reference (this is what makes the split cohere).** The cue names where the action lives and
points to it, without being it:

> *One decision can cover all 12 — choose the breadth when you apply. ↓ Set "Apply to"*

The `↓ Set "Apply to"` is a soft pointer: clicking it scrolls to and briefly highlights the scope
control at the bottom (reusing today's `scrollIntoView` on the verdict bar, `groupApplyBtn` line 1208)
— **but it does not set the scope.** It moves the eye, not the decision. The user still lands on step 4
and chooses. Contrast today, where the top button both arms scope (`setScope(1)`) *and* scrolls
(1205–1208): HYBRID keeps the scroll, drops the arming.

---

## 3. Half two — the BOTTOM fan-out (actionable), folded into scope

**Where.** The existing "Apply to:" scope-select in the verdict bar (lines 734–737). **The fan-out is
already this control.** `currentScopes()` (935–940) already builds exactly the group-aware options:

```
Just this test
All 12 matching tests in this run          ← the fan-out, already here
All 23 matching tests across 10 runs
```

So the actionable half needs **no new widget.** It is the scope option the triager reaches naturally at
step 4, after a defect is chosen. The middle option *is* "apply my decision to all 12."

**What it shows — the same count, now as a choice:**

- The scope selector with the group option carrying the same `12` the top cue named — closing the
  loop so the user recognizes "the 12 I saw at the top" as "the 12 I can fan out to."
- The recap line (`recapText`, 945–950) echoes it on commit: *"This will apply to all 12 matching
  tests in this run."* Same 12, said back at the moment it matters.

**"Show the tests" reachable here too — but as an actionable checklist.** Per `lens-bench-manual.md`
§5, choosing the group scope reveals the real RP `ItemsList` — the same member tests, now with
checkboxes so the triager can *review and deselect* before applying. Two affordances over one member
set, matched to the two moments: **read-only chips at the top** (awareness: "which tests?"),
**checklist at the bottom** (action: "all of them, or these ten?"). Same underlying members; one shared
list component rendered with or without checkboxes.

**Gated behind a verdict.** The fan-out only means anything once a defect is picked (Apply stays
disabled until then, lines 983–985). Bulk breadth is therefore structurally impossible to trigger
before a decision exists — the guard is free, it is the modal's existing Apply gate.

---

## 4. Proof: HYBRID defeats *both* failure modes

**Failure mode A — analyzing in a vacuum** (picking the defect without knowing it is a mass failure).
*Defeated by the TOP half.* The fact + count sits between R1 (orient) and the Bench (scan), so the
one-off-vs-burst signal reaches the triager *before* the hypothesis forms. The burst tint actively
feeds step 2: "12 at once → shared cause" reshapes how the advisors are read. This is precisely what
BOTTOM-only cannot do — there, the context arrives at step 4, after the decision is already essentially
made, so it informs nothing.

**Failure mode B — premature bulk apply** (fanning out before understanding the failure).
*Defeated by the BOTTOM half + the empty top.* There is no commit-verbed control above the evidence to
click reflexively. The fan-out lives at step 4, gated behind a chosen verdict, next to a member
checklist that invites review before breadth. This is precisely what TOP-only (the current mockup)
risks: a button labeled **"Apply my decision to all 12"** sitting *before the log is even read* is a
commit verb at a pre-decision moment. It anchors bulk as the expected path and — because today it
scrolls the user straight to the verdict bar (1208) — it pulls toward commit before scan even happens.
`lens-triage.md` §6 and `lens-bench-manual.md` §5 both hold that the commit zone is the footer and an
action-near-Apply is where breadth belongs; a fan-out verb at the top violates that. HYBRID moves the
verb to where the lenses already put commit.

Neither single-placement option can defeat both modes, because the modes live at opposite ends of the
reading order. Only splitting the widget covers both ends.

---

## 5. Complexity cost — and why it is actually negative

The honest cost of a split: two surfaces reference one group, the count `12` is spoken in two places,
and there is a cross-reference scroll to wire.

But weigh it against what the current TOP-only mockup already carries: **two controls that do the same
thing.** The top "Apply my decision to all 12" button (526–528) and the bottom scope option "All 12
matching tests in this run" (`currentScopes`[1]) both drive scope to the group — the top button's whole
handler is `setScope(1)` + scroll (1205–1209). That is a dual source of truth for one scope state, and
a redundant control. HYBRID **removes** the top button and keeps the one control that has to exist
anyway (scope). What it adds is one quiet line and a soft pointer.

Net: HYBRID is roughly complexity-neutral, and arguably *simpler* than today —

- **−1 control** (the top fan-out button is gone; the scope-select is the single source of truth for
  breadth).
- **+1 cue** (a P2 sentence — the fact the band should have carried all along).
- **+1 pointer** (reuses the existing `scrollIntoView`, minus the `setScope` side effect).
- **member list**: one component, two renders (chips / checklist) — a prop, not a new surface.

The duplicated `12` is a feature, not a cost: the same number at both ends is the thread that lets the
triager recognize the fan-out at step 4 as the burst they registered at step 1.

**Verdict:** the split pays for itself. The two natures of the group are consumed at two separated
steps; a single-placement widget must starve one of them; HYBRID starves neither, and does it while
retiring a redundant control. The added wiring is a sentence and a scroll — cheap for the payoff of
defeating both the vacuum and the premature-bulk failure modes at once.

---

## 6. Copy + interaction summary (implementable)

**TOP cue (replaces the actionable band):**
- Badge `12` + `12 tests failed the same way in this run.` · sub `Same error signature, this test included.`
- Optional burst tint (scale only, never a defect color) + gloss for large groups.
- `[ Show the tests ]` → read-only member chips + `See the group in Inspector ↗`.
- Pointer: `One decision can cover all 12 — set the breadth when you apply ↓` → scroll+highlight the
  scope control (no scope change).

**BOTTOM fan-out (the scope-select, already present):**
- `Apply to: [ Just this test ▾ | All 12 matching tests in this run | All 23 across 10 runs ]`.
- Choosing the group option reveals the actionable member **checklist** (RP `ItemsList`, deselectable).
- Recap on commit: `This will apply to all 12 matching tests in this run.`
- Enabled only after a defect is chosen (existing Apply gate).
