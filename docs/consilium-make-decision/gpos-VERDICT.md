# Verdict: where the identical-failure group affordance lives in the Make Decision modal

**Recommendation: HYBRID.** Split the group by its two natures and place each where the triager
actually consumes it. Keep a quiet, non-actionable awareness cue at the TOP (the fact and the
count, feeding the hypothesis before the advisors are read), and put the actionable fan-out at the
BOTTOM, at the verdict bar, where breadth is chosen and a decision is being committed. Remove the
current top "Apply my decision to all N" button, which is a commit verb sitting at a pre-decision
moment.

This is not a split-the-difference compromise. It is the only placement that survives both of the
failure modes at once, and it happens to retire a redundant control the current mockup carries.

---

## 1. The recommendation and the decisive reasons

The triager's reading order is fixed and load-bearing (`lens-triage.md` section 1, "reading order
= salience order"): orient (what failed, and how) then scan the advisors, then pick a defect, then
set scope, then apply. The group speaks to two of those steps, and they are far apart in time:

- As **context** it belongs to orient. "This exact failure is in 12 tests at once" is evidence the
  triager needs before forming a hypothesis. A lone `TimeoutException` reads as a Product or
  Automation bug. The same signature across 12 of the run's tests reads as infrastructure, a
  System Issue, a bad deploy. Same error text, opposite verdict, and the only thing that flipped it
  is the count. That reframe has to land before the advisor cards are weighed, not after.
- As **action** it belongs to scope, which is step 4, right before commit. "Fan this out to all 12"
  is mechanically nothing more than choosing the middle option of the scope control the modal
  already owns.

Two things make this decisive rather than a matter of taste:

**The analyzer itself treats group size as a prior on the answer.** In `core/grouping.py` the burst
rule raises a system-issue prior that scales with the share of the launch a novel failure hits
(roughly: five or more tests and more than forty percent of the run lifts `si_prior` toward its
cap). The machine consumes burst size *before* it classifies. If we hide that same fact from the
human until the commit bar, we have built a modal where the model reasons from a signal the person
is denied at reasoning time. The size fact has to be on screen before the advisors. That kills any
"the count only needs to live in the scope dropdown" reading.

**The two failure modes live at opposite ends of the reading order, so no single placement covers
both.**

- Failure mode A, analyzing in a vacuum: picking the defect without knowing it is a mass failure.
  Defeated only by the fact being at the top, before the advisors. A bottom-only cue delivers the
  count at step 4, after the hypothesis is already locked, so it informs nothing. A closed scope
  dropdown option labelled "all 12 matching tests" is an apply-breadth choice, not a diagnostic
  reframe.
- Failure mode B, premature bulk apply: fanning a verdict to a dozen items before understanding the
  failure. Defeated only by removing the commit verb from the top and gating fan-out behind a
  chosen defect at the commit bar. The current top button, labelled "Apply my decision to all 12",
  sits before the log is even read; its whole handler is "set the scope dropdown and scroll down".
  That is a commit verb at a pre-decision moment, and it trains bulk as the expected path.

HYBRID is the only option that defeats both: the fact defeats A at the top, the empty top plus the
gated bottom control defeats B.

### Which counter-arguments I found weak

**The TOP paper's claim that the top button is "already defused" because it only arms scope and
scrolls.** This concedes the case. A control whose only honest behavior is "teleport you to the real
control" should not wear a commit verb ("Apply my decision to all 12") at the top of the modal.
The label says apply; the code sets a dropdown and jumps. Read the TOP paper to its own section 4
guardrail and it retreats to "keep awareness and Show the tests at the top, move the fan-out verb
to the verdict bar". That guardrail *is* the hybrid. The TOP paper argues itself into this verdict;
where it still resists, it is defending a mislabelled button.

**The BOTTOM paper's claim that hybrid collapses into bottom once the top cue is held to genuine
inertness.** Partly true, and I adopt almost all of BOTTOM's discipline: the top cue touches no
scope and no defect, changes no state. But a purely inert cue with no pointer under-serves
discoverability. The triager who registered "12 tests" at orient is left to rediscover, unaided,
that those same 12 are fannable at step 4. The soft pointer (a link that scrolls to the scope
control and briefly highlights it, and does nothing else) is the low-cost thread that connects the
two moments. It arms nothing, so it costs nothing in safety. That thread is exactly what separates
this verdict from bottom-only, and it is worth keeping.

**The worry that a split means two sources of truth for scope.** In the current mockup the top
button and the bottom scope option both drive the same scope state, which is the real duplication.
The verdict removes the top button, so scope has one home: the verdict bar. The one-click fan-out at
the bottom is a labelled shortcut that sits immediately beside the scope control it drives, not a
second control 400 pixels above it. The "two sources of truth" objection applies to the current
design, and the verdict fixes it.

---

## 2. The exact resulting design

### At the TOP (context, non-actionable)

Directly under the "This failure" panel, above the Bench of advisors, same slot as today:

- The count badge showing the number, for example `12`.
- The fact, plain: "This exact failure shows up in 12 tests in this run."
- The sub-line: "Same error signature, this test included. They are all waiting to be investigated,
  so one decision can cover the whole group."
- **"Show the tests"** stays here. It reveals the read-only list of member tests (the sibling tests
  that failed the same way, with this test marked) plus the "See the group in Inspector" link. This
  is investigation, not commit. It touches no scope and changes nothing.
- A quiet pointer link: **"Apply one decision to all 12 when you commit"** with a down arrow.
  Clicking it scrolls to the verdict bar and briefly highlights the "Apply to" scope control. It
  does **not** set the scope, does not pick a defect, and changes no state. It moves the eye, not
  the decision.

What is removed from the top: the "Apply my decision to all N" button and its scope-arming
behavior. No commit verb appears above the evidence.

### At the BOTTOM (action, at the commit moment)

In the verdict bar, beside the existing "Apply to:" control:

- The **scope control stays the single source of truth for breadth** and is already group-aware:
  "Just this test / All 12 matching tests in this run / All 23 matching tests across 10 runs".
- A **one-click fan-out shortcut** sits right next to it: "Apply to all 12". One click sets the
  scope to the whole group. It lives at the commit moment, exactly where breadth is chosen, and it
  is a recognizable first-class action rather than a value buried in a dropdown cycle.
- The recap line echoes the breadth once a defect is chosen: "This will apply to all 12 matching
  tests in this run."
- Apply stays gated on a chosen defect, so no bulk write can happen before a verdict exists. Judgment
  first, breadth second, enforced by the existing Apply gate rather than by a nag dialog.

### What is clickable where

- **Top:** "Show the tests" (reveal, read-only), the Inspector group link, and the pointer link
  (scroll and highlight only, no state change).
- **Bottom:** the scope control (cycles the breadth), the "Apply to all 12" one-click shortcut
  (sets the breadth), and Apply (commits, gated on a chosen defect).

### The copy (plain global English, no em-dashes)

- Badge: `12`
- Head: "This exact failure shows up in **12 tests** in this run."
- Sub: "Same error signature, this test included. They are all waiting to be investigated, so one
  decision can cover the whole group."
- Reveal button: "Show the tests" (toggles to "Hide the tests")
- Pointer link: "Apply one decision to all 12 when you commit"
- Member note: "These 12 tests share the same error signature in this run."
- Inspector link: "See the group in Inspector"
- Bottom shortcut: "Apply to all 12"
- Recap: "This will apply to all 12 matching tests in this run."

---

## 3. When this recommendation would flip

- **If analytics show most groups are tiny.** If the vast majority of groups are one or two tests, a
  two-test group barely reframes anything, and the top awareness band earns little. In that world
  the whole affordance should shrink: drop the top band to a single quiet line or nothing, and let
  the fan-out live only as the scope option at the bottom. Bottom-only wins.
- **If bulk-apply errors are common.** If telemetry shows people fanning out wrong verdicts and then
  reverting, even the one-click bottom shortcut is too eager. Demote it to a plain scope option that
  first reveals a member checklist to review and deselect before Apply, and consider a soft confirm
  on large fan-outs. That pushes toward strict bottom.
- **If the size fact strongly and safely changes verdicts.** If people reliably flip to System Issue
  when they see a large burst, and rarely mis-apply, you could promote the top cue's salience (for
  example a quiet burst tint that encodes scale only, never a defect color). That leans harder into
  the top-context half, but it stays hybrid, just louder at the top.

The recommendation holds for the expected middle: groups are often large enough to be diagnostic,
and bulk-apply is useful but must not be reflexive. In that regime, context at the top and action at
the bottom is the placement that serves the triager's real reading order and defeats both failure
modes.
