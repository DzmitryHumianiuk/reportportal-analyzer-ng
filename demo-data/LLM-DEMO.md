# LLM sidecar role showcase — where to look

The LLM sidecar (spec 04) is live on the stand (image **mk7**, `ANALYZER_LLM_ENABLED=true`,
`ANALYZER_LLM_API=openai` → llama.cpp `qwen3:4b-q4_K_M` at service `ollama`).
`demo-data/llm_demo.py` builds four small, NEW launches (it never touches the probe
launches or scorecards) so you can watch each role work end-to-end.

Re-run any case: `python3 demo-data/llm_demo.py --case coldstart|explainer|judge|abstain|all`
then `--verify`. LLM inference is ~8–13 s/item and the circuit breaker trips if you
hammer it — space calls out.

Hosts: RP UI and the Inspector both answer on `http://minikube.local` (ingresses
`reportportal-gateway-ingress` and `analyzer-inspector`). Project ids: `llm-demo`=6,
`webshop-ui`=3, `payments-services`=4.

---

## 1. Cold-start — LLM provides provisional labels in a zero-label project ✅

Fresh project **`llm-demo`** (id 6), launch **`LLM Cold-Start Showcase`** (launch id **181**).
All 8 items abstain classically (fresh project, GBM ~0.17–0.23) → the cold-start rubric
role fires and writes provisional **ai_suggested** labels (suggest-band, `llm_used=true`).

- RP UI: `http://minikube.local/ui/#llm-demo/launches/all/181`
- Inspector journey: `http://minikube.local/#view=journey&project=6&launch=181&item=2933`

What to see: items keep RP issue `To Investigate` (the LLM **suggests, never
auto-confirms**), and `analyzer.suggestion` carries a rubric label with
`model_ver='rubric+qwen3:4b-q4_K_M'`, confidence 0.65. Evidence from this run —
`llm_event role=coldstart` = **12 ok**; provisional suggestions on items 2932-2940
(labels pb/ab/si). Sample `llm_event.output`:
`{"label":"si","reason":"The error 'ENOSPC: no space left on device' indicates a disk-full infrastructure fault"}`.

## 2. Explainer — human-readable rationale on confident suggestions ✅

`webshop-ui` launch **`LLM Explainer Showcase`** (launch id **182**), 3 near-twins of
human-labeled chronic modes. They land auto (hash-inherit, conf 0.95) → the explainer
role fills `suggestion.explanation` (`llm_used=true`).

- RP UI: `http://minikube.local/ui/#webshop-ui/launches/all/182`
- Inspector (Make-Decision view): `http://minikube.local/#view=journey&project=3&launch=182&item=2942`

What to see (this run): item **2942** (si001) explanation *"The failure matches the
'si001' mode because the test 'ShippingProfileTest.saveProfile' …"*; item **2943**
(ab001) *"…contains a 'StaleElementReferenceException' …"*. Item **2941** shows the
guard working — the LLM output failed schema validation (`llm_event outcome=validation_fail`)
so **no** explanation was persisted (never a hallucinated one). `llm_event role=explainer`
= 21 ok across the demo (+ a few timeout/validation_fail, all handled).

## 3. Judge — mid-band re-ranking ⚠️ WIRED & ENQUEUE-CORRECT, not observed firing

`webshop-ui` launch **`LLM Judge Showcase`** (launch id **195**): a labeled pb anchor +
labeled ab anchor (the JAVA-SEL-09 / S18.B locator pair) + a novel-selector **probe**
(item **2958**) that shares their exception class.

Firing contract (verified in code, `core/analysis.py:803-812`): the judge is enqueued
**only on the suggest route** when the decision confidence is in **[0.45, 0.75)** with
**≥2 candidates**. It is enabled and the queue works — coldstart/explainer/extractor all
fire, and the circuit breaker recovers.

I could **not** drive an item into that window on the suggest route (so `llm_event
role=judge` is still **0**). Root cause, with evidence:

- The **analyze** route scores the probe mid-band (e.g. earlier item 2955:
  `confidence=0.571`, `same_error_hash_top1=1.0`, `matched_item_id=1456`), but the
  **suggest** route re-decides the *same* item at **0.167** with `matched_item_id=NULL`
  — it does not resolve the labeled neighbour analyze found (suggest applies
  `_scope_query_info` launch-scoping; analyze ran `analyzerMode=ALL`).
- Probe 2958: `same_error_hash_top1=1.0`, `top1_cosine=0.99`, yet abstains at 0.167 —
  the **mk5 discriminant gate correctly blocks** the selector mismatch, then the GBM
  abstains. mk5's own discrimination fixes have squeezed out the mid-band-with-mixed-
  candidates population the judge targets, so on this mature stand the suggest route
  lands items either auto (≥0.75, e.g. items 844/87 at 0.778/0.95 → explainer only) or
  abstain (<0.45), rarely in between.

This reads as an **analyze-vs-suggest decision divergence** worth the analyzer team's
eyes (suggest yields `matched_item_id=NULL` where analyze resolves the labeled
candidate), rather than a data problem I could bend past. To retry once a genuine
suggest-band item exists: `--case judge --judge-blend 0` (in-launch anchors) or point
`_suggest_order` at any item whose *suggest*-path row is in [0.45,0.75).

## 4. Honest abstain — no LLM on the label path ✅

`webshop-ui` launch **`LLM Honest-Abstain Showcase`** (launch id **196**), one novel
failure `QuantumLedgerDesyncError` (item **2959**).

- RP UI: `http://minikube.local/ui/#webshop-ui/launches/all/196`
- Inspector: `http://minikube.local/#view=journey&project=3&launch=196&item=2959`

What to see: the item abstains classically (`ti`, GBM v4 conf 0.375). The **only**
`llm_event` on it is `role=extractor` (feature extraction) — no coldstart (project isn't
cold), no explainer (below suggest band), no judge. The label came from the GBM abstain;
the LLM never touched it. This is the safety contrast to case 1.

---

## psql one-liners (`kubectl exec deploy/analyzer-pg -- psql -U analyzer -d analyzer -c "…"`)

```sql
-- every LLM role that fired, by outcome
select role, outcome, count(*) from llm_event group by role, outcome order by role;

-- cache re-use (per-project dedup of Ollama calls)
select role, count(*) entries, sum(hits) hits from llm_cache group by role order by role;

-- cold-start provisional (ai_suggested) labels in llm-demo
select item_id, predicted_label, round(confidence::numeric,2) conf, model_ver
from suggestion where model_ver like 'rubric+%' order by created_at desc;

-- explainer: the persisted rationale (case 2)
select item_id, left(explanation,90) from suggestion
where explanation is not null and explanation <> '' order by created_at desc limit 6;

-- judge events (currently empty — see case 3)
select project_id, item_id, outcome, latency_ms from llm_event where role='judge';
```

Observed on the run that produced this file: `coldstart|ok|12`, `explainer|ok|21`,
`extractor|ok|105`, `judge` = 0. Cold-start suggestions `rubric+qwen3:4b-q4_K_M @0.65`;
explanations present on items 2942/2943/844/87/1057.
