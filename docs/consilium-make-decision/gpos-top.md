# Group affordance belongs at the TOP — group size is diagnostic context, not a checkout upsell

**Position: TOP (with the action deferred).** The "This exact failure shows up in N tests"
band stays where the current mockup puts it — a white band directly under the failure panel
(`mockup-bench-rp.html:515-537`), *before* the Bench of advisors. The reason is not layout
tidiness; it is that **group size changes the hypothesis the human is about to form**, and a
hypothesis-shaping fact has to arrive before the hypothesis, not after it.

The core objection to TOP — "an actionable fan-out button up top invites a reckless bulk apply
before the user has judged correctness" — is real, and it is already answered by the mockup's
own wiring. The top button does **not** apply. It arms scope and walks the user down to the
commit zone. That distinction is the whole design, and it lets TOP keep the one thing only TOP
can deliver: the size number in the eye-path before the advisors are weighed.

---

## 1. The load-bearing fact: the analyzer itself turns group size into a decision signal

This is not a UX intuition. It is in the analyzer's own math. `core/grouping.py:189-195`:

```python
if (
    total > 0
    and is_new(rep.error_hash)
    and len(group.members) >= burst_n            # BURST_N = 5
    and len(group.members) / total > burst_x     # BURST_X = 0.40
):
    group.si_prior = min(SI_PRIOR_CAP, SI_PRIOR_BASE + len(group.members) / total)
```

Read that plainly: when a novel failure hits **≥ 5 tests and more than 40% of the launch**, the
analyzer raises a **system-issue prior** (`si_prior`) that **scales with group size**
(`SI_PRIOR_BASE 0.5 → SI_PRIOR_CAP 0.9`). The bigger the burst, the harder the model itself
leans toward System Issue. `service.py:197` surfaces `burst_si_share` as a tuned config knob;
`analysis.py:214-220` persists the group with `dominant = si_prior > 0.0`. Group size is,
literally, a **prior on the answer** inside the pipeline.

So the question "where should group size live in the modal?" has an objective anchor: **the
analyzer already treats it as evidence that precedes the classification.** If the model consumes
burst size before it decides, and we hide that same fact from the human until the commit bar, we
have built a modal where the machine reasons from a signal the human is denied at reasoning time.
The human then weighs the three advisor cards — Past decision, Similar failures, AI guess — in a
vacuum, missing the one contextual fact the analyzer's own code says is decision-relevant.

**A lone `TimeoutException` reads as a Product/Automation bug. The same `TimeoutException`
across 23 of the launch's tests reads as infrastructure — a System Issue, an environment
outage, a bad deploy.** Same error text, opposite verdict, and the *only* thing that flipped it
is the count. That count has to be on screen before the human commits to a reading of the log.

---

## 2. Reading order: the triager forms the hypothesis at step 1-2, not step 5

`lens-triage.md:12-27` is explicit about the sequence and calls it load-bearing —
"**Reading order = salience order**":

| Step | Task | Question in the triager's head |
|---|---|---|
| 1. Orient | Glance context | *What failed, and how?* |
| 2. Scan | Read hypotheses | *Does the machine already know?* |
| 3. Pick | Choose defect | *Do I agree?* |
| 4. Scope | Apply breadth | *Just this one, or the N similar?* |
| 5. Commit | Apply | — |

The hypothesis is built at steps 1-2 and is essentially **locked by step 3** ("Do I agree?").
Group size is a step-1 fact: it answers *"what failed, and how?"* — specifically the *how big*
half of that question, which the triager is already asking. "Is this a one-off or a mass
failure?" is orientation, not commitment. Placing the size cue at the commit bar (step 4/5)
delivers it **two to three steps after the reader has already decided what the failure is.** By
then, anchoring has happened: the human has read "Product Bug 65%" on the AI card, half-agreed,
and the "12 tests" number arrives as a scoping detail rather than as the reframe it should have
been. You cannot un-ring the single-failure interpretation once the advisors have been read
under it.

`lens-triage.md:237` sets the acceptance test as the reading chain *"what failed → what the
analyzer thinks → what applying will do → Apply."* Group size is part of the **first** link
("what failed"), not the third ("what applying will do"). The mass-failure fact is context that
colors "what the analyzer thinks" — it must precede it in the eye-path, which means top.

---

## 3. Eye-path and the miss-risk of a bottom-only cue

The modal is a tall, scrolling surface: stripe → failure panel → group → Bench of advisor
cards → evidence stage → log compare → verdict bar. The advisor cards and the evidence stage
are the gravity well; a triager working an endorsed suggestion in the ≤ 20 s budget
(`lens-triage.md:14`) reads the headline, reads the top advisor, and reaches for Apply. A cue
that lives **only** at the verdict bar is below the evidence stage and the (sometimes expanded)
log-compare view — it is the thing most likely to be scrolled past or parsed last, exactly in
the fast path where re-framing matters most.

The asymmetry of the miss is the point:

- **Miss the size cue at top → you learn "one-off" is wrong before you commit.** You see "23
  tests," you reconsider System Issue, you are *slower and more correct.*
- **Miss the size cue at bottom → you have already picked Product Bug and are one Enter from
  applying it.** The fact that would have changed your mind arrives after your mind is made up.

For a *diagnostic* signal, "seen late or missed" is a correctness failure, not a convenience
one. The safe default for context that changes the hypothesis is **early and unmissable**, i.e.
top. (Bottom-only is the correct home for the scope *mechanism* — see §5 — but not for the size
*fact*.)

---

## 4. The over-eager-mass-apply risk, and why TOP does not cause it

The strongest argument against TOP is behavioral: a big blue-ish "Apply my decision to all 12"
button up top, seen before the user has judged anything, could train a reflex to fan out on
autopilot — mass-applying a wrong verdict to 23 tests in one click. That risk is real and it is
worth designing against. Three things already neutralize it, and one small guardrail closes the
rest.

**(a) The top button is not an apply. It arms scope and defers the commit.** The mockup wiring
is unambiguous (`mockup-bench-rp.html:1204-1209`):

```javascript
groupApplyBtn.addEventListener('click', function(){
    setScope(1);                                   // set "Apply to" = all N in this run
    if(recap...) recapTx.textContent=recapText();  // update the recap line
    modal.querySelector('.verdict-bar').scrollIntoView({behavior:'smooth'});
});
```

Clicking "Apply my decision to all 12" writes nothing. It sets the **`Apply to:` scope
control** (the same `OptionsSection` / `CURRENT_LAUNCH` machinery, `lens-bench-manual.md:155-168`)
to the group, updates the recap sentence, and **scrolls the user to the verdict bar** where they
still have to choose a defect and press Apply. The top control is a *route into the commit zone
with scope pre-armed*, not a commit. There is no one-click path from the top of the modal to a
written mass verdict. The `modalHasChanges` gate (`lens-bench-manual.md:184`) still means an
Apply with no chosen defect is a no-op.

**(b) The button is styled as a quiet ghost, not a primary.** It is `btn btn-ghost btn-sm`
(topaz outline), the same weight as "Show the tests" beside it
(`mockup-bench-rp.html:526-527`). RP reserves the filled primary for the actual Apply
(`btn-teal`, `mockup-bench-rp.html:741`). The visual hierarchy already says "this arms, that
commits." Nothing at the top out-shouts the real verdict button at the bottom.

**(c) Fan-out only reaches other *To Investigate* twins.** The group is same-`error_hash`,
still-undecided items (`mockup-bench-rp.html:33-34`, header map); it cannot silently overwrite
teammates' existing decisions. The blast radius is bounded to items that were going to need the
same call anyway.

**Guardrail to add — keep TOP informative, defer the verb one notch further.** If even the
armed-scope shortcut feels too eager, split the top band cleanly along the context/action seam
the prompt names:

- **Top band = awareness + reveal only.** Keep the count sentence, the size badge, and
  "**Show the tests**" (the non-committing reveal, `mockup-bench-rp.html:526`). These are pure
  orientation. Demote or drop the fan-out *verb* from the top: the top's job is to make the
  human *know* it is a mass failure, not to let them act on a verdict they have not formed yet.
- **The fan-out control proper lives at the verdict bar**, next to `Apply to:`, where scope
  already lives and where a decision is actually being committed. The size number earns its
  place at the top; the *action* earns its place where the commit happens.

This is the honest reconciliation: the prompt's HYBRID instinct is right that the *action* is
safest near Apply — but the *fact* is only useful at the top. The disagreement with HYBRID is
one of emphasis: the top is not a "quiet awareness cue" to be tucked away; it is a **first-class
diagnostic** deserving the size badge and full count sentence, because it is doing the same job
the analyzer's `si_prior` does — reframing the classification before it is made. A whisper at the
top under-serves a signal the pipeline itself treats as a prior on the answer.

---

## 5. Why the scope control at the bottom does not make the top redundant

The scope selector already sits by Apply (`Apply to: [Just this test ▾]`,
`mockup-bench-rp.html:734-737`), and it is *group-aware* — its options are built from the same
counts (`All 12 matching tests in this run`, `currentScopes()`, `mockup-bench-rp.html:935-940`).
So the group's *action* is genuinely covered at the bottom. That is precisely why the top does
not need to carry the verb — and precisely why it must still carry the **number.**

Context and action are two different jobs with two different natural homes:

- **Action** (fan out the decision to N) belongs where decisions are committed — the verdict
  bar. Agreed. Keep it there.
- **Context** (this is a mass failure, so it is probably infrastructure) belongs where the
  hypothesis is formed — the top, before the advisor cards.

Folding the context into the bottom scope dropdown fails the context job twice: it hides the
size behind a closed `▾` (the count is not even visible until the dropdown is opened,
`mockup-bench-rp.html:736`), and it delivers it at step 4/5 after the reading is done. A dropdown
option labeled "All 12 matching tests" is an *apply-breadth* choice; it is not, and cannot be, a
*diagnostic reframe.* The reframe has to be a statement the eye lands on early — "This exact
failure shows up in 12 tests in this run" (`mockup-bench-rp.html:522`) — not a menu item behind
a click at the end.

---

## 6. Summary of the case for TOP

1. **The analyzer treats group size as a prior on the verdict** (`grouping.py:189-195`, burst
   `si_prior` scaling with member share). Denying the human that same fact at reasoning time
   makes them analyze with less context than the machine used.
2. **Size flips the hypothesis** — one `TimeoutException` = product/automation bug; the same
   across the launch = System Issue. Same text, opposite verdict, count is the only difference.
3. **The hypothesis is formed at reading step 1-2 and locked by step 3** (`lens-triage.md`).
   A step-1 fact must arrive at step 1. Bottom placement delivers it after the mind is made up.
4. **Bottom-only risks the miss** in the fast path, and the miss is a correctness failure, not
   a convenience one — the asymmetry favors early-and-unmissable.
5. **The premature-mass-apply fear is already defused**: the top control arms scope and scrolls
   to the commit bar (`mockup-bench-rp.html:1204-1209`); it writes nothing, is a quiet ghost,
   and reaches only undecided twins. If still worried, keep **awareness + "Show the tests" at
   top and the fan-out verb at the verdict bar** — top informs, bottom commits.
6. **The bottom scope control covers the action but cannot cover the context**: a closed
   dropdown option is apply-breadth, not a diagnostic reframe. The number has to be a top-level
   statement, seen before the advisors.

Keep the size at the top. Let the verb commit at the bottom. The human should know it is a mass
failure before they weigh the advisors — because the analyzer already did.
