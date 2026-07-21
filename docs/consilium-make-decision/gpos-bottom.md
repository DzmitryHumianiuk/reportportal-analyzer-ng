# Position: the group fan-out ACTION belongs at the bottom, next to scope + Apply

**Argued position: BOTTOM.** The "Apply my decision to all N" control belongs at the
decision/commit moment, beside the existing "Apply to:" scope select and the Apply button —
not at the top of the modal before a decision exists. A minimal, *inert* awareness cue stays
at the top so the group is not lost as context. This document makes the case and answers the
context counter.

Grounded in: the triager reading order in `lens-triage.md` §1, the state machine and scope
wiring in `lens-bench-manual.md` §5, and the actual behavior of the current TOP mockup in
`mockup-bench-rp.html` (group band lines 515–537; scope + Apply lines 725–747; the group-apply
handler lines 1205–1209; the scope handler lines 1188–1197).

---

## 1. The load-bearing fact: "apply to all N" *is* a scope decision

Read what the control actually does in the shipped mockup. The scope select is a three-way
cycle built from the group counts (`mockup-bench-rp.html` handler at 1188, `doApply` at 1219):

```
Apply to:  [ Just this test ]  →  [ all N matching tests in this run ]  →  [ all N across 10 runs ]
                index 0                     index 1                            index 2
```

"Apply my decision to all N" is, mechanically, **nothing more than selecting index 1 of that
control.** The current TOP button proves it — its entire handler is:

```js
// mockup-bench-rp.html:1205
groupApplyBtn.addEventListener('click', function(){
  setScope(1);                                   // drive the bottom scope control
  ...
  modal.querySelector('.verdict-bar').scrollIntoView(...);  // then jump the user to the bottom
});
```

The top button does not apply anything. It reaches *down* to the bottom scope control, sets it,
and then **scrolls the user to the bottom** to finish the decision. That is the design telling us
where the action lives: the fan-out is a scope preset, and scope lives at Apply. Putting the button
at the top creates a control whose only honest behavior is "teleport you to the real control."

In `lens-bench-manual.md` §5 this is explicit: fan-out is `OptionsSection` /
`CURRENT_LAUNCH` — "this item + matching 'To Investigate' items in the launch." It is one of the
`optionValue` states of the *existing* scope selector. There is exactly one scope concept in the
commit contract. The group affordance should drive that one control, standing next to it — not be
a second, higher, competing scope control 400px above it.

---

## 2. The triager's reading order puts scope at step 4, right before commit

`lens-triage.md` §1 defines the job as a strict sequence, and states the governing rule:
**"Reading order = salience order."**

| Step | Task | Question in the triager's head |
|---|---|---|
| 1. Orient | Glance context | *What failed, and how?* |
| 2. Scan | Read hypotheses | *Does the machine already know?* |
| 3. Pick / adjust | Choose defect + comment | *Do I agree? What do I write?* |
| 4. **Scope** | **Apply breadth** | ***Just this one, or the 17 similar TI?*** |
| 5. Commit | Apply | — |

Breadth is **step 4** — deliberately *after* the human has oriented, scanned, and picked. The
group fan-out is a breadth choice: "just this one, or the whole group?" is `lens-triage`'s step-4
question almost verbatim. So the fan-out ACTION belongs at step 4's location, which is next to
Apply, not at step 1 (orient).

Placing the fan-out button at the TOP forces a step-4 action into the step-1 slot. The user meets
"Apply my decision to all 12" *before they have a decision to apply* — before they have read the
failure log (R1), before they have scanned the three advisors, before they have picked a defect.
The acceptance test in `lens-triage.md` §9 is: a triager reads *what failed → what the analyzer
thinks → what applying will do (defect + scope recap) → Apply*. Scope recap is bundled with Apply
on purpose. A fan-out control at the top violates that order.

---

## 3. A top "Apply to all N" is a control with nothing to apply

At the moment the top band is read (step 1), `decisionType` is unset — there is no chosen defect.
Per `lens-bench-manual.md` §0, the entire commit reads one slot:
`prepareDataToSend()` → `modalState[ACTIVE_TAB_MAP[decisionType]].issue`. Before a decision,
that slot is empty; `modalHasChanges` is false and Apply is disabled (`mockup-bench-rp.html:741`,
`disabled`).

So a top "Apply my decision to all N" button, taken literally, has two possible states, both bad:

- **Dead / disabled** at the top of the modal — a prominent, primary-looking button that does
  nothing on arrival, which is confusing (why is the biggest action greyed out before I've done
  anything?), or
- **Live but meaningless** — it "applies" a decision that does not exist yet, so it has to secretly
  do something *other* than apply. That is exactly what the current mockup does (arm scope + scroll),
  and it is a lie the label tells: the button says "apply," the code says "set a dropdown and jump."

Either way the top button is not a real Apply. The one place where "Apply my decision to all N"
becomes a *true, immediate, honest* action is the moment a decision exists — the commit bar. There,
it means precisely what it says.

---

## 4. Top placement invites bulk-apply before judgment — the highest-stakes error

The group is a **mass failure**: the same signature in 12 (or, at index 2, N-across-runs) tests.
That is exactly the case where a premature fan-out does the most damage — one hasty verdict does not
mislabel one item, it mislabels the whole group.

A large, primary "Apply my decision to all 12" sitting at the top, *before the failure and advisors
have been studied*, is an accelerator pointed the wrong way. It rewards the fast reflex ("12 of them,
one click, done") at the precise moment the human has the least information. `lens-triage.md` is
emphatic that the modal must not teach click-through: it refuses confirm dialogs specifically because
they "teach users to click through" (§4). The same discipline forbids a top-of-modal bulk button that
lets a user fan a verdict to a dozen items before forming judgment.

Putting the fan-out at the bottom structurally prevents this: to reach it, the user has scrolled past
the failure, the advisors, and the defect pick, and the button only becomes meaningful once a defect
is chosen. Judgment first, breadth second — enforced by layout, not by a nag.

---

## 5. Muscle memory and consistency: "decide, then choose how far it applies"

Every existing decision tool in this modal already teaches one motion: **pick the verdict, then set
the breadth.** The scope select ("Just this test" vs "matching TI items") lives at the bottom next to
Apply and is used on *every* decision, group or not. The group fan-out is the same gesture applied to
a named set. Housing it beside the scope control means the user learns **one** rule —
*decide → scope → apply* — and the group is just a richer scope option, not a separate ritual.

Split the fan-out to the top and you fracture that model into two:

- one path where breadth is chosen at the bottom (the normal scope select), and
- one path where breadth is chosen at the top (the group button),

for the *same underlying `optionValue`.* Two controls, one state = drift and two sources of truth.
If the top button sets `CURRENT_LAUNCH` and the user then touches the bottom scope select, which
wins? (In the mockup the last click wins, but now the user has two places showing/2 setting the same
thing and must reconcile them.) Consistency argues for one scope surface, at the commit bar, with the
group fan-out as a labeled shortcut *into that same control.*

---

## 6. The counter: "if it's only at the bottom, do we lose the group as CONTEXT?"

This is the real objection, and it is legitimate. The group is genuinely **two things at once**
(as the prompt states):

1. **Context** — *is this a one-off or a mass failure?* That changes what the failure likely is
   (a signature hitting 12 tests at once reads more like an environment/System Issue or a shared
   Product Bug than a lone flake). This belongs at **orient (step 1)** — the human wants it *before*
   they judge.
2. **Action** — *apply to all N.* This is scope, and belongs at **step 4 / commit.**

The mistake the current TOP mockup makes is conflating the two: it puts the *action* at the top
because the *context* deserves to be there. Separate them.

### Minimum top-of-modal awareness (the concession)

Keep at the top only an **inert awareness cue** — enough to answer "one-off or mass failure?" and
to let the curious look, with **no fan-out verb and no scope-touching behavior**:

- A quiet, non-primary line with the count and the "is this a mass failure" framing, e.g.
  *"This exact failure is in 12 tests in this run"* — the `group-head`/`group-sub` copy from
  `mockup-bench-rp.html:522–523`, kept as text, styled as info (topaz-100 badge), **not** as a button.
- The **"Show the tests"** reveal (`data-group-toggle`, lines 526/1200) stays at the top. Revealing
  the member list is *investigation/context* — "let me see which tests" — not a commit. It touches no
  scope, no `decisionType`, changes nothing. It is the top band earning its place as context.
- The Inspector group permalink (line 534) stays — also pure context.

What is **removed** from the top: the `data-group-apply` "Apply my decision to all N" button
(line 527) and its `setScope(1)` + scroll handler. That control moves to the commit bar.

### Where the action lands at the bottom

Beside the existing scope select, once a defect is chosen, surface the fan-out as either:

- the group made a **first-class named option of the existing scope control** —
  `Apply to: [ all 12 matching tests in this run ]` is already scope index 1 today; label it with
  the group so the user sees the group *as a scope*, or
- a one-click **"Apply my decision to all 12"** button *in the verdict bar*, right of Apply, that
  sets scope index 1 and applies — now a true, immediate action because the decision exists.

This keeps context where context is read (top, step 1) and the action where breadth is committed
(bottom, step 4/5), with no duplicated scope control and no premature bulk button.

### Why this is BOTTOM and not HYBRID

The named HYBRID option puts "a quiet awareness cue at top + the actionable fan-out control at the
bottom" — which sounds identical, but HYBRID typically still dresses the top cue as a designed
*affordance* that hints at the action (the slippery slope back to a top button that "arms" scope,
i.e. the current anti-pattern). The BOTTOM position is stricter and cleaner: **the top cue must be
truly inert** — a sentence, a badge, a reveal, a permalink, and *nothing that touches scope or
commit.* The single actionable fan-out lives only at the commit bar, as part of the one scope
control the modal already has. If HYBRID's "top cue" is held to genuine inertness, it collapses into
this BOTTOM design; where they differ, BOTTOM refuses the top-of-modal action affordance that risks
premature bulk-apply.

---

## 7. Summary of the case for BOTTOM

- **"Apply to all N" is a scope decision**, and the modal already owns exactly one scope control,
  at the commit bar. The fan-out should drive *that* control, next to it — not stand as a second,
  higher scope control (`lens-bench-manual.md` §5; scope cycle at `mockup-bench-rp.html:1188`).
- **Reading order = salience order.** Breadth is step 4, immediately before commit
  (`lens-triage.md` §1). A step-4 action does not belong in the step-1 orient slot.
- **A top "apply all" has nothing to apply yet.** Before a decision, the commit slot is empty and
  Apply is disabled; a top button is either dead or dishonest. The current mockup's own handler
  proves it — it only arms scope and scrolls to the bottom (`mockup-bench-rp.html:1205`).
- **Top placement invites bulk-apply before judgment** on the exact case where that is most
  damaging — a mass failure fanning to N items — against `lens-triage`'s no-click-through discipline.
- **One motion, one model:** *decide → scope → apply.* Housing the fan-out with scope preserves
  muscle memory and one source of truth; splitting it fractures the scope concept across two places.
- **Context is not lost:** keep an inert top cue (count + "mass failure?" framing + "Show the tests"
  reveal + Inspector link) for orient; move only the *verb* to the commit bar.
