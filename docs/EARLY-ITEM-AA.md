# Early per-item auto-analysis (analyzer side)

Analyze a failed test item seconds after it finishes, while its launch is still
running, instead of waiting for the launch to end. This document is the analyzer
side of the feature: the AMQP route, the decision policy, the data tagging, and
the offline replay harness. The ReportPortal side (the trigger that publishes a
per-item message after a debounce) ships separately as a service-api patch and
is out of scope here.

Design authority: the 2026-07-25 consilium verdict. Key ruling: the early pass
may auto-label only decisions backed by deterministic evidence; everything the
GBM decides is demoted to a suggestion until the launch-finish pass re-analyzes
the complete launch.

## Why the early pass is restricted

A mid-launch item is analyzed as a group of one. Three GBM features then stop
carrying information, deterministically, not noisily:

| Feature | Mid-launch value | Why |
|---|---|---|
| `group_dominance` | always `1.0` | group of one over one failure |
| `si_prior` | always `0.0` | the burst rule needs 5 or more group members |
| `launch_fail_fraction` | always `0.0` | known wire gap, dead today on every route |

The shipped GBM was trained on launch-finish context, so its 0.75 auto-label
threshold has no demonstrated validity at this corner, and the System Issue
signal is structurally unreachable before the launch ends. Stage A (exact hash
inherit) and the KB short-circuit use none of these features, so for them early
versus late makes no correctness difference at all.

## Behavior

New AMQP routing key **`analyze_item_early`**. Same body as `analyze` (a list of
`Launch` objects; the trigger sends one launch holding one test item, logs
inline), same reply shape (a list of `AnalysisResult`). The exchange is a
fanout, so no broker changes are needed.

For every item in the payload the handler runs the same singleton pipeline the
`suggest` route already uses (signature, singleton group, `_decide`), then
applies the policy gate:

| Decision | Early behavior |
|---|---|
| Stage A inherit (`method='hash'`) at auto band | auto-label: reply row + mirror update, exactly like `analyze` |
| KB short-circuit (`method='kb'`) at auto band | auto-label, same as above |
| Any GBM decision, any confidence | demoted: NOT in the reply, so nothing is applied; the suggestion row keeps the full snapshot |
| Suggest band / abstain | unchanged: suggestion row only |

Every decision writes a suggestion row with **`source='early'`** (migration
0009), and LLM enrichment is enqueued exactly as on the other routes, so the
extractor cache and the explanations are warm long before the launch finishes.

The launch-finish `analyze` pass is untouched and remains the correctness
backstop: it re-groups the whole launch, sees real burst context and any logs
that arrived after the trigger's debounce window, and can relabel auto-labeled
items through the existing ReportPortal mechanism.

## Flags

| Env var | Default | Meaning |
|---|---|---|
| `ANALYZER_EARLY_ITEM_ANALYSIS` | `false` | master switch; off means the route answers an empty list and touches nothing |
| `ANALYZER_EARLY_AA_LABEL_POLICY` | `kb_inherit_only` | `kb_inherit_only`: deterministic decisions auto-label, GBM is demoted. `suggest_only`: nothing auto-labels, every decision is stored and enriched only |

Both are read once at startup like every other `ANALYZER_*` knob.

## Data: the `source` column

Migration `0009_suggestion_source.sql` adds a nullable `source text` column to
`analyzer.suggestion`. The early route writes `'early'`; every other writer
leaves it NULL. No backfill: NULL means "written by a launch-scoped or suggest
route".

Two consumers:

1. **Training guard.** `fetch_training_frame` picks each label event's most
   recent suggestion snapshot. Early rows are excluded inside that lookup, so a
   degenerate singleton snapshot can never train the launch-finish GBM. If an
   item's only snapshot before its label was an early one, that event simply
   contributes no feature row until a launch-scoped row exists.
2. **Flip-rate metric.** Early and final rows for the same item pair up by
   `item_id`, giving the disagreement rate between early and finish decisions
   for free. That number gates any future widening of the auto-label policy.

## Replay harness (gates v2)

`tools/replay-early-aa/` answers "what would early AA have decided?" without
running a launch: it loads stored launch-finish suggestion rows, rewrites the
launch-context features of each snapshot to their singleton constants
(`group_dominance=1.0`, `co_failure_group_size=log1p(1)/log1p(200)`,
`launch_fail_fraction=0.0`, `si_prior=0.0`), scores both vectors with the
active GBM artifact, and reports the flip rate by predicted class. Pure
offline: reads the analyzer database, writes nothing.

This isolates exactly the context effect (same history, same model, different
grouping context), which is the quantity the consilium requires before any
GBM-band auto-labeling is allowed early.

## Explicitly out of scope (v1)

- The ReportPortal trigger (service-api patch: finish-item hook, per-project
  flag, debounce before publish).
- Any GBM-band early auto-labeling, including "pb only above a stricter
  threshold" — v2, gated on the replay numbers.
- Early System Issue labels: impossible before launch finish by construction.
- Waiting for LLM features: the decision path never blocks on the sidecar; the
  19 one-hot columns read their honest `unknown` level until the cache warms.
- Notification dedup and the "early result" UI affordance (RP-side work).

## Test plan

Unit (all TDD, red first):

- config: both flags parse, defaults off/`kb_inherit_only`, bad values rejected.
- dispatcher: `analyze_item_early` routes to the handler, replies a model list,
  unknown keys still raise.
- engine, flag off: empty reply, no suggestion row, no LLM enqueue.
- engine, Stage A auto: reply row + mirror update + suggestion row tagged
  `source='early'`.
- engine, KB short-circuit auto: same as Stage A.
- engine, GBM auto-band decision: empty reply, no mirror update, row written
  with full snapshot and `source='early'` (the demotion).
- engine, abstain: empty reply, row written, LLM enqueued.
- engine, `suggest_only` policy: Stage A auto is also demoted.
- training frame: the lateral snapshot lookup skips `source='early'` rows
  (integration, real database).
- replay: the singletonization rewrite produces exactly the constant corner and
  leaves every other feature untouched.

## Rollout

Ships dark: the flag defaults off and nothing publishes the routing key until
the service-api patch lands. Enabling order on a stand: analyzer image with the
flag on, then the RP-side flag on one project, then watch the flip-rate pairs.
