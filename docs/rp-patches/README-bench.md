# service-ui 5.15.3 — the "Bench" Make Decision redesign

Reworks ReportPortal's **Make Decision** modal into the **Bench**: a light, plain-English
triage surface driven by analyzer-ng's live data. It supersedes the suggestions-tab
presentation shipped in the earlier `ng1` patch while keeping that patch's analyzer-data
parsing intact. This is an **RP-side** change (the user owns the stand).

- Patch: [`service-ui-5.15.3-bench.patch`](./service-ui-5.15.3-bench.patch)
- Built image: `reportportal/service-ui:5.15.3-ng3` (full-width; supersedes `ng2`)
- Design source of truth: [`../consilium-make-decision/mockup-bench-rp.html`](../consilium-make-decision/mockup-bench-rp.html)
  (revised in place to full-width) plus the `lens-bench-*` / `gpos-VERDICT` docs.
- Live evidence: [`evidence/bench/`](./evidence/bench)

## ng3 — full-width layout (approved)

`ng2` capped the Bench content at ~1180px centred inside the RP dark modal shell, which
spans the whole screen — leaving large empty dark margins (measured ~495px each side at a
2227px viewport). `ng3` fills that reclaimed width:

- The light surface spans the modal width up to a graceful **1600px cap** (`.modal-scroll`),
  no horizontal scroll. Measured at a **1920** viewport: Bench **1602px** (was ~1180),
  margins shrank to ~188/130; the three advisor cards grew from **224 → ~351px** each
  (`.check { flex: 1 1 240px; max-width: 380px }`), using the width instead of stretching
  whitespace.
- A **left context column** (`.context-col`, ~340px) in the reclaimed space carries the
  **group of identical failures** as first-class context: the populated group card (count
  badge, **Show the tests** member chips, **See the group in Inspector**) when same-signature
  To-Investigate twins exist, or the honest **"This failure is on its own in this run"**
  state when none do. The group ACTION (fan-out) still lives in the bottom scope control per
  the approved HYBRID; the quiet pointer scrolls/pulses it.
- The declined dock is a **full-width horizontal strip** (`.dock { flex-basis: 100% }`, items
  side by side via `repeat(auto-fill, minmax(320px,1fr))`); the "line the analyzer must clear"
  is a **horizontal divider** (`.bar-sep { flex-basis: 100% }`).
- Long test names wrap with **zero clip** (`word-break: break-word`). Everything else is
  unchanged from `ng2` (advisors, agreement banner, AI why, verdict bar with the reused manual
  `DefectTypeSelector`, single log toggle, R2 compare overlay + Esc-closes-compare, Inspector
  permalinks, humanized copy, no em-dashes).

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
| Group (HYBRID, ng3 = LEFT context column) | "In this run": either the populated group card ("This exact failure shows up in N tests in this run", **Show the tests** member chips, **See the group in Inspector**, quiet pointer that scrolls+pulses the scope control) or the honest **"This failure is on its own in this run"** state | `modalState.testItems` (similar TI in the launch) |
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
- `.../makeDecisionModal/bench/bench.jsx` — the Bench (state machine, checks, compare, verdict wiring; `ng3` adds `bench-split` + left `context-col` `renderGroup`, full-width dock body).
- `.../makeDecisionModal/bench/bench.scss` — RP Design System 6 light tokens, scoped under `.bench-modal`; `ng3` adds `.modal-scroll` (1600 cap), `.bench-split`/`.context-col`/`.bench-main`, flexible `.check`, horizontal `.bar-sep`/`.dock`.
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
docker build -f Dockerfile.ng -t reportportal/service-ui:5.15.3-ng3 .   # Dockerfile.ng in app/
kubectl set image deployment/reportportal-ui ui=reportportal/service-ui:5.15.3-ng3
kubectl rollout status deployment/reportportal-ui
```

`Dockerfile.ng` (context = `app/`):

```dockerfile
FROM reportportal/service-ui:5.15.3
COPY build/ /usr/share/nginx/html/
```

## Live verification (superadmin)

### ng3 full-width measurements (Playwright, 1920 viewport)

| | ng2 (centred, capped) | ng3 (full-width) |
|---|---|---|
| Bench content width | ~1180px | **1602px** (1600 cap + border) |
| Left / right margin | ~342px each (centred 1180) | **188 / 130px** (margins shrank) |
| Advisor card width | 224px fixed | **~351px** flexible (240–380) |
| Left context column | none | **present, 381px** |
| Horizontal scroll | n/a | **none** (`documentElement.scrollWidth == clientWidth`) |

- Item **4998** (wide): left **"In this run"** column shows the **populated** group card
  (badge **18**, bolded **18 tests**, **Show the tests**, fan-out pointer); three advisor cards
  spread to ~351px; console **0 errors**; em-dashes **0**.
- Item **2941** (wide): left column shows the honest **"This failure is on its own in this run"**
  group-alone state (no twins); long name *"Checkout. Tax & totals. Recalculate VAT on mixed cart
  [llm-explainer-demo]"* wraps with **zero clip** (`scrollWidth == clientWidth`); **Compare logs**
  opens side-by-side and **Esc closes the compare, modal stays open**; console **0 errors**;
  em-dashes **0**.

### Scenario detail

`migrated-project` rubric item **4998** (`.../267/4996/4997/4998/log`):
- Banner **Only the AI has a guess**; **AI guess** card = **Product Bug** hypothesis, **65%**,
  **not confirmed**, **Use this guess**. Past decision + Similar failures show empty states.
- Group cue **This exact failure shows up in 18 tests in this run** (17 similar TI + this one),
  **Show the tests**, scope pointer.
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

Console: clean (0 errors) on the ng3 runs; any `401 /api/users?ids=:0` seen earlier is a
pre-existing RP-shell request, not Bench-originated. Rendered em-dashes: 0 on every modal.
Screenshots in [`evidence/bench/`](./evidence/bench):
- ng3 full-width: `bench-ng3-4998-fullwidth.png` (populated group column, wide cards),
  `bench-ng3-webshop-agree-alone.png` ("on its own" group state, agree banner).
- ng2 (interaction detail, layout aside): `bench-4998-full.png`, `bench-4998-adopted.png`
  (adopt → prefill → Apply enabled), `bench-webshop-agree.png`, `bench-webshop-compare.png`
  (R2 compare).

## Graceful degradation (what happens without below-band data)

The Bench consumes below-band (`band=below_suggest`) rows **when present** and degrades
cleanly when absent. On this stand the analyzer flag is **off**, so:
- 4998 returns rubric-only → S3 "Only the AI has a guess", **no dock** (correct).
- 2941 returns a suggest-band classical row → S1 agree, **no dock** (correct).
- Every new parser returns `null`/`''` on a token-less `modelInfo`; `parseBand` NEVER guesses
  `below_suggest` from a legacy analyzer (legacy fallback is only `rubric` by methodName or
  `suggest`/`null` by matchScore threshold). A stock/mk18 analyzer renders like `ng1` with no
  new below-band chrome.

## Follow-up — light up the declined dock with real data

The declined dock is fully implemented UI-side but currently receives no rows because the
analyzer does not yet emit below-band candidates. To turn it on (a **separate** analyzer-side
change, not in this patch):

1. Land the analyzer contract v1 (per `lens-contract.md §2–3`): emit `modelInfo` with
   `ng=1;band=<auto|suggest|below_suggest|rubric>;conf=<p*>[;ek=<match|decline>;why=<text>]`
   tokens (they ride inside `modelInfo` because stock service-api strips unknown first-class
   `SuggestAnalysisResult` keys before they reach the UI).
2. Replace the early `[]` return in `analysis.py` (~L1027) with the abstain path: when
   `proxy < TAU_SUGGEST` (0.45), render up to `ANALYZER_SUGGEST_BELOW_MAX` (default 2, cap 3)
   stage-C candidates with `band=below_suggest`, subject to the `ANALYZER_SUGGEST_BELOW_FLOOR`
   (0.30 cosine) noise floor; attach the abstain narration to the first below-band row as
   `ek=decline;why=...`.
3. **Gate it behind `ANALYZER_SUGGEST_BELOW_ENABLED` (default `false`).** Keep it off until
   `ng2` is live everywhere: a stock / `ng1` UI would render a below-band row as a normal
   "Analyzer Suggestion NN%" card (it has no band parser), making a declined candidate look
   endorsed. Kill switch = flip the env var back (no image change).

Rollout order: (1) analyzer contract v1 with the flag OFF (canary), (2) UI `ng2` (this patch),
(3) flip `ANALYZER_SUGGEST_BELOW_ENABLED=true`. Once on, the dock fills automatically — no
further UI change is needed.
