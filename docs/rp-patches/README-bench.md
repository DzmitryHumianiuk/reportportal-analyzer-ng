# service-ui 5.15.4 — the "Bench" Make Decision redesign

Reworks ReportPortal's **Make Decision** modal into the **Bench**: a light, plain-English
triage surface driven by analyzer-ng's live data. It supersedes the suggestions-tab
presentation shipped in the earlier `ng1` patch while keeping that patch's analyzer-data
parsing intact. This is an **RP-side** change (the user owns the stand).

> Note: this file references internal design notes (the `consilium-*` and
> `gpos-*` / `lens-*` deliberation docs and HTML mockups). Those are kept out of
> the public repo; only the patch and its evidence ship here.

- Patch: [`service-ui-5.15.4-bench.patch`](./service-ui-5.15.4-bench.patch)
- Built image: `reportportal/service-ui:5.15.4-ng75`

**ng75 — rebased on the upstream 5.15.4 release.** Upstream 5.15.3..5.15.4
brings stored-XSS hardening for user HTML (`sanitizeUserHtml` +
`markdownViewer`, which also covers the comments the Bench renders), an HSTS
header in nginx, CHC widget Owner grouping, and attribute editor fixes. The
merge is conflict-free: none of the bench-patched files changed upstream. The
branch is `analyzer-ng/5.15.4-bench`; the patch applies to a clean `5.15.4`
checkout and the thin overlay now builds on the stock `5.15.4` image.

**ng73 to ng74 — the group offer comes from the representative, not a dead
endpoint.** The by-cluster suggest endpoint is a dead letter against
analyzer-ng: service-api's cluster variant sends `clusterId` and launch
metadata with NO logs, and the analyzer builds its query signature from the
request's own logs, so the reply was always empty — every modal opened from
Unique Errors (single item or bulk) silently lost its suggestions. A cluster
IS the exact error-hash group, so the modal now asks for the representative
member's own suggest instead (same band contract, same failure group). Also:
the bulk story banner now requires the representative to actually carry a
non-TI saved type — a band `auto` suggestion row for a still-To-Investigate
item means the analyzer WOULD apply, not that it did. Verified live on a
fresh TI cluster: Past decision + Similar failures cards render, card arming
fills the defect and the matched test's saved comment, the recap states both.
Analyzer-side debt (not fixed here): the analyzer `suggest` route ignores
`clusterId`, so the stock by-cluster endpoint stays empty for any other
client until the analyzer learns to resolve a cluster to its stored
representative.

**ng71 to ng72 — a one-cluster bulk decision gets the group anatomy.** A Unique
Errors cluster and the analyzer's launch group are the same set (the cluster
route mints its id from the group representative's error hash inside the same
`_group()` the Inspector journey serves), so a selection that sits in one
cluster is a decision about one failure group and now reads that way: a group
cue line under the selection list ("All selected tests share one failure
signature."), the analyzer's own story when it already decided (band `auto`
from the representative's journey, with an Inspector link), and the same three
checks the single Bench shows, classified from the cluster suggest reply's
band contract: Past decision, Similar failures (alike score + the neighbour's
label source, unlabeled stated as "nobody labelled yet"), AI guess with its
rationale. The whole card arms; the matched test's saved comment (or the AI
reason) prefills the reason while the human has not typed one, and an edited
comment is kept and says so. Below-line rows collapse into one
looked-at-and-declined line. A selection spanning several clusters gets an
honest "fail in N different ways" line and the manual bar, no group pretense.
The journey's `launch_group` count is deliberately NOT shown: it is persisted
per analyze batch and can undercount the cluster the page shows (seen live:
`member_count` 1 for a 3-test cluster). Component tests cover the card
classification, arming with prefill, the cross-cluster state, and the
settled-empty state.

**ng70 — a multi-select decision gets the light surface too.** Editing defects
for several tests at once (step page multi-select, Unique Errors cluster
selection) still fell to the stock dark tabbed modal: the Bench gate excluded
every bulk operation, a deliberate deferral from the silent-state work. The
bulk surface is now its own light component (`bulkBench.jsx`): the dark
identity bar counts the selection and shows the shared saved type (or says the
types differ), the body lists every test the decision will touch with its
first error line, and the same verdict bar arms one type for all of them. The
stock bulk comment rule rides along next to the editor: no text offers
keep-or-clear, typed text offers add-or-replace, and the recap states both
what will be set and what happens to the comments. When the whole selection is
one Unique Errors cluster, the analyzer offer for that failure group renders
as armable rows, fed by the same `MLSuggestionsByCluster` fetch the stock tabs
used. Commit path unchanged and wire-identical (`selectManualChoice` +
`commentOption` + the stock bulk `applyChanges`). Analyzer off or unreachable
on a single item is now the only state left on the stock dark tabs.

**ng66 to ng67 — the AI card names its rule, and the empty state offers a way in.**
The card said the AI "reasons from a set of rules" without saying which. The
analyzer stores each rule's reader-facing name next to its id (analyzer `mk31`),
so the card now states "Rule matched: Could not reach the service". Nothing on
this side keeps a copy of the rubric, and a row written before the name existed
keeps the generic line rather than showing a reader "R6". The empty state, the
one place with nothing to read, gains a link to the Inspector for that item.

**ng65 — the card heading keeps clear of the corner why? link.** The link is
positioned against the card corner and takes no width in the heading row, so the
second chip added below slid underneath it. The heading now reserves the link's
width and wraps.

**ng56 to ng64 — the modal stops claiming support the model never gave.** All of
it found by walking a fresh project (`live-check-01`) through the two-run live
check, and all of it reads data the reply and the journey already carry.

- *The Similar failures card advertised a similarity as if it were a verdict.*
  `matchScore` is a log cosine, and dense cosines sit near 0.9 for any two stack
  traces out of one suite, so a lone neighbour led the offers wearing a LEADING
  badge and the subtitle "from a trained model" while the model that scored it
  had refused to move (calibrated 0.32, band abstain). The card now carries the
  model's own verdict, the discriminants that did NOT match ("no shared
  identifiers, different status codes", stated only when the `*_present`
  companion says there was something to compare), and how thin the pool is
  ("Only 1 earlier failure to compare against", shown only at 5 or fewer). The
  banner stops naming the side the offers lean when nothing but text supports it.
  A stock analyzer whose reply cannot be read renders exactly as before.
- *"No AI guess for this failure" while the guess was printed above it.* The card
  had two sources and both go missing together: the live reply drops its rubric
  row once a human labels a neighbour, and the journey fills `rubric_hypothesis`
  only when a classical row displaced the guess. A third source reads the case
  where the record IS the guess.
- *A bare Enter armed whatever sat on top.* The lead was ranked by similarity, so
  in a cold project the default landed on the card marked "Not backed". A row may
  now lead only where the model agrees; auto decisions are untouched, everywhere
  else no agreement means no lead at all.
- *Arming an unbacked offer looked like arming a backed one.* The armed ring turns
  amber and the note says "The model did not back this offer. You are deciding on
  your own." One 460ms beat on arming, dropped under `prefers-reduced-motion`; no
  looping blink, because in a project still filling up this state is the norm and
  a warning that never stops moving is furniture by the third item.
- *The explanation paragraph was hidden, then wrong.* The quote gate matched line
  by line while the explainer quotes the analyzer's signature, which JOINS the
  lines, so a multi-line quote could never match and the gate failed closed on
  most stack traces. Grounding falls back to the joined log, which still requires
  every character of the quote to appear in the real log in order (a two-way
  containment test would have passed any hallucination embedding one short real
  line). Then Act 1 was showing the newest row's explanation, which is often the
  cold-start guess answering a different question; it now takes the classical
  row's account, keyed on what the row IS rather than on its confidence band.
- *A log comparison outlived the card it belonged to.* Selecting another card
  closes it, so it can never sit under a verdict it says nothing about.

**ng55 — an empty answer says why, and stops being final.** Found on a fresh
project during the two-run live check (`demo-data/live_check.py`):

- *The empty state named the wrong cause.* Every empty analyzer reply was
  reported as "No error logs to analyze". That is true for one cause and false
  for the two common ones. The analyzer answers the suggest call from what it
  has already worked out, so the first look at a failure it has not seen before
  gets an empty reply while the ERROR logs are right there and the answer is
  seconds away. A project with no decided failures like this one has nothing to
  point at however good the logs are. Measured on the stand: item 6238 had one
  ERROR log, a signature, an extractor result and a cold-start answer 26 seconds
  later, and still read "this test recorded no ERROR-level logs". The hero now
  picks between three headings — still working, nothing in this project to match
  against, the real silent case — using the analyzer's own `signature` block to
  decide whether it got anything to read, with the fetched ERROR lines as the
  fallback before the journey lands. Variant choice is a pure function
  (`emptyReplyVariant`) with unit tests.
- *The empty state was final.* The answer landing seconds later was only visible
  if the reader closed and reopened the modal. On an empty reply the modal now
  asks the analyzer whether it can still answer (the same health question the
  explanation wait state asks) and, if it can, asks again at 15s and 40s. One
  20-second retry would still be too early: measured, a first answer for a
  never-seen failure lands about 25 seconds after the reply goes out. Fixed
  schedule, then it stops; it never runs once the analyzer has answered.

**ng54 — the AI guess card remembers the cold-start hypothesis.** Paired with
analyzer image `mk28` (quote-field split + auto-disable gate fix):

- *The journey's hypothesis now reaches the modal.* When the live suggest reply
  has no rubric row (newer classical rows displaced it, or the guess feature is
  off for the project), the AI guess card falls back to the journey record's
  `rubric_hypothesis` block: same adoptable card, tagged `hypothesis`, with
  plain copy ("No confident match yet. This is an early guess from rules.
  Check it before you decide."). When the analyzer has turned the guess feature
  off for the project, the card says the guess will not update instead of
  pretending it is current.
- *Quote grounding reads the split quote fields.* Analyzer images after the
  F1b split write `quoted_log_lines` (log quotes only); cached outputs up to
  90 days old still carry the single `quoted_lines` field, and both are read.
  Fact values (`quoted_fact_values`) are never matched against the log, so
  they can no longer poison the grounding check.

**ng52 — the analyzer explanation actually reaches the modal.** Two fixes, paired
with the inspector-side carry-forward (see `docs/OPERATIONS.md`):

- *Quote grounding could never match a line with a number in it.* The explainer
  quotes the analyzer's masked signature (`status code <NUM> but got <NUM>`)
  while the item's log line is raw (`status code 200 but got 400`), and
  `normalizeLine` folded digits to `N`, so the two never met. The grounding gate
  fails closed, so this hid the explanation on most failures. `normalizeLine`
  now mirrors the analyzer's masking vocabulary from `ml/drain.py` (URL, IP,
  UUID, HEX, PATH, NUM). It lives next to the gate it serves, in
  `analyzerSuggestionMeta.js`, and is covered by `analyzerSuggestionMeta.test.js`
  including the measured item-4207 case.
- *A missing explanation now says it is coming.* Opening the modal makes the
  analyzer re-decide and write a fresh row whose explanation is filled in
  asynchronously. When it is not there yet, the story shows a quiet wait line
  instead of nothing, but only when the analyzer reports it can still answer
  (LLM enabled, breaker closed): historically more than half of explainer runs
  ended with the breaker open. It re-reads the journey once after 20 seconds,
  gives up after a minute, and never states an exact time.

Earlier history (image tags `ng1`..`ng51`) follows.

- Previous built image: `reportportal/service-ui:5.15.3-ng37` (ng37: silent-no-signal empty state - a FAILED item with no ERROR logs, so an empty suggest reply, now renders a light Bench empty state (dark identity header + one calm grey "No error logs to analyze" hero + the manual verdict bar) instead of falling back to the stock dark tabs. Design source is internal. benchEmpty branch in makeDecisionModal.jsx; Bench emptyNoSignal prop. Commit path unchanged; analyzer-off/unreachable/bulk still keep stock tabs.) (needs analyzer mk24+). ng30 the manual defect-type picker chips are re-skinned light (stock DefectTypeSelectorItem ships dark-mode colours); ng33 the picker shows FULL type names in per-group vertical columns; ng34-35 the picker is rebuilt as mockup-faithful RP-light `.pick-pill` pills (colour dot + full name), one VERTICAL COLUMN per defect group (a group's subtypes stack in its column), selected = topaz fill, dock-highlighted = yellow ring, plus a "Pick the defect type that fits this failure." help line; the stock DefectTypeSelector is dropped from the Bench. ng27 Past decision card relaid out per image 24 (person role line, defect pill, "In launch #N" link, shorter strength line); ng28 clean rebuild bundling ng27 + the "Use this answer" fix: adopting a Past decision / Similar failures suggestion now prefills the comment from the SUGGESTED ITEM'S OWN comment (row.res.issue.comment), not the AI summary text, and no longer collapses the summary into the comment. Paired with analyzer mk23 which ROLLED BACK the deterministic S2 ek=match;why= templates on suggest/auto rows (they did not read as useful); the AI decision summary now shows only the LLM decision explanation (S1) or the cold-start rubric, and collapses when neither is available (e.g. suggest-but-decision-abstained items like 6099). ng26 (ng23 the decision-summary text renders defect locators as inline mini pills and "item N" as a link; ng24 that item link points to the item log view, not the Inspector; ng25 the decision-card provenance line names the REAL person + real launch number (fetched from the neighbour's activity log + launch entity), drops the comment note, launch shown as a link to the neighbour log view - unresolved person/number is omitted, never fabricated; ng26 clean rebuild bundling ng23-ng25 after concurrent-fork build races). ng22 (needs analyzer image mk22+). ng19 defect pills instead of "same answer" in agree state; ng20 "Compare logs" toggles closed on a second click; ng21 decision cards show a "A person decided this in an earlier run" provenance line with an Inspector link (real data only, no fabricated user/launch); ng22 the decision-summary block is labelled honestly - "AI decision summary / written by AI" only for the LLM explanation (S1) or the cold-start rubric; a deterministic S2 why (analyzer mk22 gives suggest/auto rows a templated ek=match;why from retrieval facts, no LLM) reads "Decision summary / from analyzer data" instead. ng18 (ng18 PRODUCT CHANGE: the loose fuzzy logSearch scope ("N similar in the launch") is removed. Bulk apply is now driven by the EXACT launch group (same error_hash, the grouping Inspector/Unique Errors use): a single checkbox "Also apply to N more tests with the same exact error" (still-TI siblings only; already-decided members left alone), and solo items read "applies to this test only". The burst "Apply System Issue" fans out over the same exact-group TI members. executionSection stops calling logSearch in benchMode (kept mounted only for the current-item log fetch). Commit mechanism unchanged (selectedItems + stock applyChanges); only the SOURCE of selectedItems changed from fuzzy to exact group. ng17 (ng15 dedup the dock note to "Shown only so you can decide."; ng16 clamp long dock test names to 2 lines + full name on hover; ng17 the Similar failures similarity number is now labelled "Logs 0.92 alike" (benchLogsAlike) so a cosine log-similarity is never read as a model confidence, while the abstain card keeps benchAlike). ng14 (ng14 = clean rebuild bundling two fixes: (a) no flash of the stock dark tabs / similar-items before the Bench: makeDecisionModal.jsx gains a benchPending phase that shows a light Bench shell + spinner until the MLSuggestions fetch resolves, instead of rendering the stock path first; (b) DECISION BOUNDARY label overlays the vertical dotted divider (position:relative bar-sep, absolute centered rule, label chip on top) so it no longer eats a horizontal column. ng12: the declined dock is now the 4th column in the SAME row as the three advisor cards (mockup-bench.html layout), separated by a vertical dotted divider with a rotated "Decision boundary" label; dock rows are stacked multi-line cards again. This replaces the ng11 full-width strip below the cards. ng11: dock rows single-line, divider is one dotted rule with a centered "Decision boundary" chip, burst panel Defect pill becomes an SI-subtype dropdown when the project has more than one System Issue type; ng10: mockup-parity focus ring, the compare-source check card carries a topaz ring while its compare is open; ng8 banner rework: "Analyzer agree:" + defect pill + right-aligned kbd-style Enter hints; ng9 burst band polish: percent line, 40% gate rule with a text-sized inline System Issue mini pill, honesty and pointer lines removed, action is a ghost button "Apply <System Issue mini pill>". Earlier: ng5 + the check-card why? link pinned
  to the card's top right corner with RP's open-in-new-tab icon on every Inspector link;
  ng7 adds the C2 burst apply flow: the burst band shows the share as a percent
  ("47% ... (8 of 17)"), the 40% analyzer gate rule with the System Issue defect pill
  inline, and an "Apply System Issue" pill action bottom right. Clicking it covers the
  bands below and opens a focused panel: System Issue pre-picked ("set from the burst
  context"), comment editor focused, scope preselected to the burst group members found
  in the Apply-to dataset (missing members reported honestly), Back/Esc returns, Apply
  commits through the unchanged stock path)
- ng5 (2026-07-22) is the consilium-2 rework: design authority moved to the
  internal `gpos-VERDICT2` / `lens2-*` design notes. Highlights: dark header is the identity bar (saved
  defect = the before picture), single commit bar (dark footer removed in bench mode),
  the grey stage is now the AI decision summary (journey `decision.explanation` behind a
  fail-closed freshness gate, why= fallback, clickable quoted lines), group band gained
  burst state C2 (si_prior/dominant fraction + honesty line) and solo line C0,
  Use this answer on classical cards (adoptClassical wired), comment collapsed to
  "Add a reason", Apply to all {n} beside the scope control, dedup pass per lens2-dedup.
- Design source of truth: the internal `mockup-bench-rp.html` mockup plus the
  `lens-bench-*` / `gpos-VERDICT` design notes (kept out of the public repo).
- Live evidence: [`evidence/bench/`](./evidence/bench)

## Patch shape — this is the FULL diff over the 5.15.3 tag

`service-ui-5.15.3-bench.patch` is a **single, self-contained `git diff` over the
`5.15.3` tag that INCLUDES the prior `ng1` suggestion-cards patch**. Apply it to a clean
`5.15.3` checkout on its own (do NOT also apply `service-ui-5.15.3-suggestion-cards.patch`
first — its content is already contained here). Verified: `git apply --check` is clean
against a fresh `5.15.3` clone.

The `ng1` suggestion-cards changes are retained because the Bench reuses their
`analyzerSuggestionMeta.js` parsing, and the old suggestions tab is still the graceful
fallback for the cases the Bench does not take over (bulk edits, analyzer unreachable).

## What the Bench is (and how it maps to real data)

The modal's execution/suggestions area becomes one light surface with these bands
(top to bottom). The Bench renders for **single-item** decisions when the analyzer is
reachable; **bulk** edits and analyzer-off keep the stock dark tabs.

| Band | What it shows | Live data it consumes |
|---|---|---|
| Item stripe | test name, `· FAILED ·`, current defect pill, one **Show error log** toggle, **Open full details in Inspector** | `currentTestItems[0]`, `projectInfoIdSelector` |
| R1 "This failure" | first ERROR line once; expand = **Stack trace and context (ERROR level)** without repeating the header line | `currentTestItems[0].logs` (bulkLastLogs) |
| Group (HYBRID) | top context cue "This exact failure shows up in N tests in this run", read-only **Show the tests** reveal (member chips link to each item's log view), Inspector permalink, and a quiet pointer that scrolls+pulses the scope control (arms nothing) | Inspector journey API `/inspector/api/item/{project}/{item}/journey` → `grouping` (`analyzer.launch_group`, exact `error_hash` group). Same number and members as the Inspector's grouping card. NOT the fuzzy `logSearch` list: that one still feeds only the **Apply to** scope control, which stays stock. |
| Agreement banner | one of 4 states: **The checks agree: X** / **The checks do not agree** / **Only the AI has a guess** / **The analyzer is not sure about this one** | derived from the per-row bands |
| Three checks | **Past decision** (auto band / exact match), **Similar failures** (GBM/classical), **AI guess** (rubric) with plain band words and defect pills | `suggestedItems[*].suggestRs` |
| Declined dock | below-0.45 rows: quiet grey dashed inset, struck defect names, "The analyzer said no to these", never pre-selected | `band=below_suggest` rows (see follow-up) |
| Evidence stage | "What the analyzer saw": matched failure line + AI explanation ("written by AI"), or the R2 compare view | `explanation`/`explKind`, candidate logs |
| Verdict bar (R3) | manual `DefectTypeSelector` (always reachable) + comment editor (prefilled by how the decision was reached) + the reused **Apply to** scope control + keys legend | `selectManualChoice` / `suggestChoice`, `OptionsSection` |

The R2 **log compare** opens on a check/candidate/dock click (side-by-side this-failure vs
candidate, passed-green "same" / warning-amber "different", ★ Similar Log twin), commits
nothing, and **Esc closes the compare, not the modal** (a capture-phase interceptor beats
`DarkModalLayout`'s document Esc handler). **Enter applies** the current verdict outside the
comment editor; **Ctrl/Cmd+Enter** applies from inside the editor. Every advisor / compare /
verdict carries a **See details in Inspector** journey permalink (`/inspector/#view=journey&project=<id>&launch=<launchId>&item=<testItem>`), opened in a new window.

The commit path is **wire-identical** to stock: gestures set `selectManualChoice` /
`suggestChoice` + `decisionType` exactly as the stock tabs do, and Apply is the unchanged
`applyChanges → saveDefect → prepareDataToSend` / `sendSuggestResponse(userChoice:1)`.
A declined-dock adoption is **highlight-only** (defect not selected, Apply stays disabled,
Enter can never target it) until the human clicks the type.

## Files changed

New:
- `.../makeDecisionModal/bench/bench.jsx` — the Bench (state machine, checks, compare, verdict wiring).
- `.../makeDecisionModal/bench/bench.scss` — RP Design System 6 light tokens, scoped under `.bench-modal`.
- `.../makeDecisionModal/bench/index.js`
- (`ng1`) `.../makeDecisionModal/analyzerSuggestionMeta.js` — extended with `parseBand`,
  `parseConfidence`, `parseExplanation`, `parseExplKind`, `parseNgVersion`, `BANDS`,
  `getInspectorJourneyUrl*`. All parsers defensive: legacy/stock analyzers yield `null`/`''`.
- (`ng1`) `.../machineLearningSuggestions/machineLearningSuggestions.scss`

Modified:
- `.../makeDecisionModal/makeDecisionModal.jsx` — render `<Bench>` (sideSection dropped) when active; pass `applyChanges` / `acceptSuggestedHypothesis`; scope section reused via `benchMode`.
- `.../makeDecisionModal/executionSection/executionSection.jsx` — `benchMode`: keep the current-item log fetch + scope `OptionsSection`, hide the duplicate current-item panel (the Bench owns R1).
- `.../makeDecisionModal/messages.js` — Bench i18n strings (no em-dashes anywhere).
- (`ng1`) `machineLearningSuggestions.jsx`, `makeDecisionTabs.jsx` (+`.scss`).

## Copy rule

No em-dashes / long dashes in any rendered string (standing project rule,
lens-bench-english §0). Verified live: `innerText.match(/[—–‒―]/)` returns 0 on both the
rubric and classical modals.

## Build & deploy (host build → thin overlay)

```bash
git clone --branch 5.15.3 https://github.com/reportportal/service-ui.git
cd service-ui
git apply /path/to/service-ui-5.15.3-bench.patch      # full patch, applies to clean 5.15.3

cd app
npm ci --legacy-peer-deps
NODE_OPTIONS="--max-old-space-size=4096" npm run build  # Node 22 verified; webpack

# thin overlay over the stock image (preserves buildInfo.json -> footer stays 5.15.3)
eval $(minikube -p minikube docker-env)
docker build -f Dockerfile.ng -t reportportal/service-ui:5.15.3-ng4 .   # Dockerfile.ng in app/
kubectl set image deployment/reportportal-ui ui=reportportal/service-ui:5.15.3-ng4
kubectl rollout status deployment/reportportal-ui
```

`Dockerfile.ng` (context = `app/`):

```dockerfile
FROM reportportal/service-ui:5.15.3
COPY build/ /usr/share/nginx/html/
```

## Live verification (superadmin)

`migrated-project` rubric item **4998** (`.../267/4996/4997/4998/log`):
- Banner **Only the AI has a guess**; **AI guess** card = **Product Bug** hypothesis, **65%**,
  **not confirmed**, **Use this guess**. Past decision + Similar failures show empty states.
- Group cue **This exact failure shows up in N tests in this run** where N is the
  `launch_group.member_count` from the Inspector journey API, so the modal and the
  Inspector always agree (verified on `migrated-project` item **5920**: both say 8, while
  the fuzzy logSearch list behind the scope control holds 20, capped at `TOP_K`).
  **Show the tests** reveals the exact-group members as clickable chips; scope pointer
  carries no count (the Apply-to scope is the wider fuzzy list, a different thing).
- One **Show error log** toggle → **Stack trace and context (ERROR level)** (4998 has a single
  log line, so the expansion honestly shows no further stack rather than repeating the header).
- **Use this guess** → comment prefilled with the rubric why-text, note "Filled from the AI
  reason. Change it if you want.", provenance "from the AI guess (not confirmed)", stripe defect
  flips to Product Bug, **Apply enabled**. (Apply **not** clicked — item left untouched.)
- Inspector links resolve to `/inspector/#view=journey&project=7&launch=267&item=4998`.

`webshop-ui` classical item **2941** (LLM Explainer Showcase, launch 182):
- Banner **The checks agree: Product Bug / Press Enter to set it**; **Similar failures** card
  **0.95 alike. Suggested, please confirm** (suggest band), Product Bug pill, **Compare logs**.
- **Compare logs** → side-by-side **This failure** vs **The one you picked**, passed-green
  "same" highlighting, legend, help line. **Esc closes the compare, modal stays open**, stage
  returns to "What the analyzer saw".
- No declined dock (below-band rows absent — degrades cleanly).

Console: only a pre-existing `401 /api/users?ids=:0` from the RP shell (not Bench-originated).
Rendered em-dashes: 0 on both modals. Screenshots in [`evidence/bench/`](./evidence/bench):
`bench-4998-full.png`, `bench-4998-adopted.png`, `bench-webshop-agree.png`, `bench-webshop-compare.png`.

## Graceful degradation (what happens without below-band data)

The Bench consumes below-band (`band=below_suggest`) rows **when present** and degrades
cleanly when absent. On this stand the analyzer flag is **off**, so:
- 4998 returns rubric-only → S3 "Only the AI has a guess", **no dock** (correct).
- 2941 returns a suggest-band classical row → S1 agree, **no dock** (correct).
- Every new parser returns `null`/`''` on a token-less `modelInfo`; `parseBand` NEVER guesses
  `below_suggest` from a legacy analyzer (legacy fallback is only `rubric` by methodName or
  `suggest`/`null` by matchScore threshold). A stock/mk18 analyzer renders like `ng1` with no
  new below-band chrome.

## Follow-up — light up the declined dock with real data (LANDED, analyzer ≥ mk21)

Landed 2026-07-22 (`analysis.py`, tests in `tests/unit/test_analysis_below_band.py`):

1. **Contract v1 tokens always on**: every suggest row's `modelInfo` now ends with
   `;ng=1;band=<auto|suggest|below_suggest>;src=<provenance>`, the decision's own row adds
   `;conf=<p*>`, and the first dock row of an abstained reply adds
   `;ek=decline;why=<abstain narration>` (why last — free text may contain `;`). Side
   effect worth knowing: the Bench now files an auto-band row under **Past decision**
   instead of the legacy-fallback "Similar failures" slot.
2. **Dock rows behind `ANALYZER_SUGGEST_BELOW_ENABLED`** (default `false`): when on, two
   sources fill the dock, both floored at `SUGGEST_BELOW_FLOOR` (**0.30**) and capped at
   `ANALYZER_SUGGEST_BELOW_MAX` (default 2, hard cap 3):
   - stage-C candidates whose cosine lands in [0.30, 0.45) — the spec-literal window,
     rare in practice (e5 cosines seldom dip under ~0.85 in-domain);
   - the **gbm_below_suggest abstain** (the common case): the declined argmax-group
     hypothesis ships as one dock row anchored to that group's best not-yet-shown
     stage-C candidate, scored by the CALIBRATED p* (`decision.confidence` — NOT the
     raw `decision.probs`, which calibrate down: raw pb 0.64 → p* 0.32), with
     `ek=decline;why=<abstain narration>` on it.
   RP's service-api serves at most ~3 rows, so on an abstained reply the dock takes its
   slots from the shared budget and neighbour rows shrink (e.g. 2 neighbours + 1 dock).
   Below 0.30 stays dropped as noise. A reply with no row at/above τ_suggest still gets
   the cold-start rubric appended (AI guess + dock coexist). A per-reply
   "suggest bands: …" INFO log summarizes label/abstain/probs/real/below for debugging.
   Live-verified on `migrated-project` item **4998**: reply = 2 neighbours (band=suggest)
   + dock row rel 4539 at 32.0 with the decline narration; the Bench renders
   "The analyzer said no to these (1)" with the struck defect and "Too weak to suggest
   (0.32)".
3. Keep the flag off where a band-unaware UI (stock/`ng1`) is live — it would render a
   declined candidate as an endorsed "Analyzer Suggestion NN%" card. Kill switch = flip
   the env var back (no image change). On this stand: `ng4` UI + flag ON.

## Follow-up — how much the model believes THIS label (`plabel=`, analyzer ≥ ng65)

Issue #8's last open item. A suggest row could say what the model decided about the
failure (`conf=`, the calibrated probability of the model's OWN answer) but not how much
the model believed the label that row offers. When the model abstained with argmax `pb`
at 0.63 and the row offers System Issue, 0.63 is not the System Issue number, and
printing it next to a System Issue pill would state something the model never said.

Every suggest row now carries `;plabel=<p>` (4 decimals, same style as `conf=`) with the
calibrated probability of that row's own base group:

- `DecisionResult.probs` is the GBM's RAW softmax distribution; the per-project isotonic
  fit (§6.5) is fitted on `raw max-prob → P(argmax correct)`, so only the argmax has a
  measured calibrated value. `calibrated_label_prob()` maps the raw distribution onto that
  scale: the argmax group takes `p*` and the leftover mass `1 - p*` is split among the
  other groups in their raw ratios. It stays a distribution, and its argmax entry equals
  `p*` exactly, so `plabel=` can never contradict `conf=` on the same row.
- The token is **omitted** whenever the answer is not known: Stage-A hash and KB short
  circuits, the cold rule fallback and any legacy result carry no calibrated distribution
  (their `probs` is a `{label: confidence}` placeholder that says nothing about the other
  groups), and a locator outside the four base groups has no entry at all. A missing token
  means "we do not know" — never 0, never a guess.
- Additive only: `matchScore`, `conf=` and `band=` are untouched.

UI side (`analyzerSuggestionMeta.js` `parseOfferedLabelProbability`, fork branch
`analyzer-ng/offered-label-probability`): unreadable or absent → `null`, and the Similar
failures card prints one short line next to the defect pill only when the value is known.
