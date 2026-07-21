# service-ui 5.15.3 — Make Decision suggestion-card patch

Makes ReportPortal's **Make Decision** modal informative about analyzer-ng's data —
especially the LLM **cold-start rubric** provisional hypotheses that the analyzer now
emits (analyzer image `mk18`; analyzer commit `94ed6d6`).

This is an **RP-side** change (the user owns the ReportPortal stand). The stock modal
renders only `matchScore` + the relevant item's own DB fields; every other field the
analyzer sends (`methodName`, `modelInfo`, the proposed `issueType`) is dropped. This
patch surfaces them, with strictly defensive parsing so a stock / legacy analyzer is
unaffected.

- Patch: [`service-ui-5.15.3-suggestion-cards.patch`](./service-ui-5.15.3-suggestion-cards.patch)
- Built image: `reportportal/service-ui:5.15.3-ng1`
- Live evidence: [`evidence/`](./evidence)

## What the analyzer sends (the contract this patch reads)

`suggestRs` fields on each suggestion (RP relays the analyzer's `SuggestAnalysisResult`
verbatim; keys are camelCase — see `src/analyzer_ng/amqp/models.py`):

| Row kind | `methodName` | `matchScore` | `issueType` | `modelInfo` |
|---|---|---|---|---|
| Cold-start rubric | `coldstart_rubric` | rubric pseudo-confidence (e.g. `65.0`, **not** a similarity) | rubric locator (proposed defect) | `coldstart_rubric;{model_ver};why={explanation}` |
| Classical | `suggestion` / `auto_analysis` | similarity | neighbor's defect | `analyzer-ng;gbm=..;emb=..;kb_mode=..;src=<provenance>` |

`src=` is one of `human-confirmed`, `auto-analyzed`, `unlabeled`, …
For a rubric row, `relevantItem`/`relevantLogId` are a **self-reference** (RP loads the
relevant item by id to build `testItemResource`, so a fabricated neighbor id would fail
the load).

## What the patch does (design A–D)

- **A. "Suggested defect" row — all cards.** A defect pill built from `suggestRs.issueType`
  resolved through the project's defect-type config (reuses `DefectTypeItem`, the same
  component `itemHeader` uses). Makes explicit what accepting will apply.
- **B. Provenance chip + tab label.** Chip parsed from `modelInfo`:
  `human-confirmed → "human-labeled neighbor"`, `auto-analyzed → "auto-labeled neighbor"`,
  rubric → distinct **"LLM hypothesis (provisional)"** (chip tooltip = model version).
  The central-column tab label reads **"65% · LLM hypothesis"** (vs "Analyzer Suggestion"),
  and the tab header reads **"LLM cold-start hypothesis · 65% rubric confidence"** so the
  pseudo-confidence is never mislabeled as similarity.
- **C. Rubric "why" block.** For `coldstart_rubric`, the `why=` text is parsed from
  `modelInfo` and rendered as plain text (no HTML injection) in the card body, captioned
  "AI hypothesis — why this defect" (`data-provenance="ai_suggested"`).
- **D. Accept behavior (the key UX).** The stock modal would copy the self-referenced
  rubric's own (empty) issue and apply nothing. The new **"Accept & edit comment"** button
  routes into the standard *Select defect manually* flow with the rubric's proposed defect
  **selected** and the `why=` text **prefilled into the editable comment editor**, so the
  user reviews/edits before Apply and the reasoning lands in the item's comment. Classical
  rows keep stock behavior.

Defensive parsing: legacy analyzers' `modelInfo` doesn't match these shapes → helpers
return `null`/`''` and nothing extra renders (only the plain neighbor detail, exactly as
stock). Verified live: a classical `src=unlabeled` row shows the Suggested-defect row but
**no** chip, and its cards keep the stock "Analyzer Suggestion" label.

## Files changed (in the service-ui tree)

New:
- `app/src/pages/inside/stepPage/modals/makeDecisionModal/analyzerSuggestionMeta.js` — defensive `modelInfo`/provenance parsing helpers.
- `.../makeDecisionModal/tabs/machineLearningSuggestions/machineLearningSuggestions.scss` — styles for the new meta block.

Modified:
- `.../makeDecisionModal/makeDecisionModal.jsx` — `acceptSuggestedHypothesis` handler (prefill + tab switch); rubric tab-header title.
- `.../makeDecisionModal/makeDecisionTabs/makeDecisionTabs.jsx` (+`.scss`) — rubric card label + accent.
- `.../makeDecisionModal/tabs/machineLearningSuggestions/machineLearningSuggestions.jsx` — A/B-chip/C/D UI.
- `.../makeDecisionModal/messages.js` — new i18n strings.

## How to apply & build

```bash
# 1. Clone the exact running version and apply the patch
git clone --branch 5.15.3 https://github.com/reportportal/service-ui.git
cd service-ui
git apply /path/to/service-ui-5.15.3-suggestion-cards.patch

# 2. Build the static bundle (host build; Node 20 per the repo Dockerfile,
#    Node 22 also verified to work). Output goes to app/build/.
cd app
npm ci --legacy-peer-deps
NODE_OPTIONS="--max-old-space-size=4096" npm run build
```

### Image (thin overlay over the stock image)

The stock `5.15.3` image serves the UI from `/usr/share/nginx/html` and carries
`buildInfo.json` (the version footer). A thin overlay copies the freshly built `app/build/`
over that docroot **without** overwriting `buildInfo.json`, so the footer still reads
`5.15.3` and the VM avoids a full multi-stage `node_modules` build. `Dockerfile.ng` (build
context = `app/`):

```dockerfile
FROM reportportal/service-ui:5.15.3
COPY build/ /usr/share/nginx/html/
```

```bash
# Build into minikube's docker so the cluster can use the local image
eval $(minikube -p minikube docker-env)
docker build -f Dockerfile.ng -t reportportal/service-ui:5.15.3-ng1 app/

# Roll out (deployment already uses imagePullPolicy: IfNotPresent)
kubectl set image deployment/reportportal-ui ui=reportportal/service-ui:5.15.3-ng1
kubectl rollout status deployment/reportportal-ui
```

To fully rebuild the image (no stock base), use the repo's own multi-stage `Dockerfile`
(`node:20-alpine`, `npm ci --legacy-peer-deps && npm run build`). The thin overlay is
preferred here only because the minikube VM disk is tight.

## Live verification (superadmin, `migrated-project`)

Rubric item **4998** (`.../267/4996/4997/4998/log`):
- Central tab reads **"65% · LLM hypothesis"**; header **"LLM cold-start hypothesis · 65% rubric confidence"**.
- Suggested-defect pill **Product Bug** (from rubric locator `pb001`); chip **"LLM hypothesis (provisional)"** (tooltip `rubric+qwen3:4b-q4_K_M`).
- Why-text rendered in the body.
- **Accept & edit comment** → switches to the manual tab, **Product Bug** selected, comment editor prefilled with the why-text, Apply enabled. (Evidence captured with Apply **not** clicked — item left untouched.)

Classical control item **5012** (`.../4996/5011/5012/log`): cards keep "Analyzer
Suggestion"; the `src=human-confirmed` card shows **"human-labeled neighbor"**, the
`src=unlabeled` card shows **no** chip; the genuine neighbor detail below is unchanged.

Console clean (0 errors) throughout. Screenshots in [`evidence/`](./evidence):
`rubric-card-4998.png`, `rubric-accept-prefilled-4998.png`, `classical-card-5012.png`.
