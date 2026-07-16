# SOTA Survey: Automated Test-Failure Triage & Log Intelligence (2024–2026)

> Round 2 greenfield consilium. Role: senior AIOps / log-intelligence researcher.

**Most important framing:** this product's problem is *supervised, per-project, label-scarce classification with continuous human feedback* — NOT what most academic log-analysis literature solves (unsupervised anomaly detection on HDFS/BGL). The closest production analogues — crash dedup (Sentry/Mozilla/WER), Develocity failure classification, RCACopilot — all converge on: **cheap deterministic fingerprinting → similarity retrieval over labeled history → shallow supervised ranker → LLM for explanation only.**

## 1. Test-failure triage: what production systems actually do

Research:
- FlakyLens (OOPSLA 2025) — flaky-category classification. FlaKat (2024) — ML flaky categorization. **Vocabulary/static-token flakiness predictors overfit badly across projects** (FlakeFlagger ICSE 2021 + replication): tokens predictive of flakiness in one dataset predict *non*-flakiness in another. **History/runtime features generalize; static text doesn't.**
- Chromium fault-vs-flaky study (2023, arXiv 2302.10594) — maps directly onto pb-vs-ab/si; failure-history features dominate.
- **Google**: Flake Aware Culprit Finding (ICST 2023) — Bayesian noisy binary search over suspect commits; flakiness modeled probabilistically, not classified from text. ~16% of tests flaky; rerun-based mitigation.
- **Meta**: Probabilistic Flakiness Score — continuous per-test reliability from execution history. Predictive Test Selection — GBDT on change/test metadata, no log text; 99.9% regression catch at ~1/3 tests.
- **Atlassian** Flakinator: Bayesian inference on retry outcomes over 350M executions/day, ownership routing, auto-quarantine.

Products:
- **Develocity**: flaky detection = **retry-based** (fail-then-pass within one execution) + cross-build outcome comparison. Failure Analytics groups failures into categories — *group-then-classify*.
- **Azure DevOps**: rerun-based detection + pluggable API. No deep learning anywhere.
- **Launchable**: historic pass/fail + change metadata, deliberately no code/log content.
- **GitLab Duo RCA**: truncate failed-job log → canned prompt → LLM summarize/propose. An explanation feature, not a triage system.
- **Crash dedup lineage** (most battle-tested prior art): WER signature buckets; Mozilla Socorro curated frame-skip-list fingerprints; JetBrains TraceSim = TF-IDF + alignment + few learned weights. **2024 JetBrains re-evaluation (arXiv 2412.14802): DL models (S3M, DeepCrash) *underperform* TF-IDF-family in realistic settings** — load-bearing negative result.
- **Sentry (closest architectural cousin)**: hash fingerprint first; on new hash, fine-tuned transformer embeds message + in-app frames, **ANN in pgvector (HNSW)**, merge within threshold. 40% fewer issues, 50% fewer wrong merges, sub-100ms. **pgvector at Sentry scale = existence proof.**

**Takeaway:** no production triage system classifies raw log text with a deep model. All use: retries/history for flakiness, fingerprint/similarity grouping for dedup, shallow supervised models on engineered features, LLM at the explanation layer.

## 2. Log representation
- **Template mining**: Drain3 = production workhorse. LILAC (FSE 2024) = credible LLM-era successor: LLM parses *novel* templates, adaptive cache answers repeats — LLM cost paid once per template. Caveat: benchmarks are datacenter logs, not test logs (stacktraces, assertion diffs).
- **Log-pretrained encoders (LogBERT, BERTOps)**: academic; **no public evidence they beat generic sentence encoders on triage/similarity**. Sentry used a generic transformer fine-tuned on their own data — the pattern that works. In-domain adaptation on pairs matters, not log-domain pretraining (arXiv 2508.19449).
- **LogAI (Salesforce)**: reference catalogue, low maintenance velocity — mine for ideas, don't build on.
- Proven recipe: **BM25/TF-IDF strong baseline + small generic embedder fine-tuned later on the product's own confirm/correct pairs** — the feedback loop generates exactly the contrastive training data needed.

## 3. LLM-era log/failure analysis: what actually worked
- **RCACopilot (EuroSys 2024, Microsoft)** — most instructive: incidents route to handler-specific diagnostic collection workflows (the boring proven part), then the LLM **predicts a root-cause category + narrative using retrieved similar past incidents (FastText embeddings) as few-shot context**. 0.766 accuracy on a year of incidents. Pattern ≈ isomorphic to this product.
- **Meta AI-assisted RCA (2024)**: heuristic retrieval → fine-tuned Llama-2-7B ranker → **42% top-5** — a win *because* it's a suggestion surface with feedback loops and confidence gating, not automation.
- **LogPrompt (ICSE 2024)**: LLM explanations rated 4.42/5 by practitioners — evidence explanations are valued; not evidence LLM classification beats supervised baselines when labels exist.
- **Agentic RCA weak**: AIOpsLab (Microsoft 2025); OpenRCA (ICLR 2025) best agent ~11% solve rate; multi-agent frameworks paper-stage. LogSage (2025): LLM CI-failure detection works as assist with heavy preprocessing.
- What failed: free-form LLM root-causing from raw logs (hallucination+cost); autonomous agents. What worked: **LLM as (a) template extractor with caching, (b) category predictor grounded in retrieved labeled neighbors, (c) explanation generator over deterministically-assembled evidence** — all fit on-prem OSS with a 7-8B-class local model, degrading gracefully to no-LLM.

## 4. Flakiness & non-text signals — the biggest out-of-the-box lever

| Signal | Evidence | Maps to |
|---|---|---|
| Retry outcome (fail→pass) | Develocity's entire detection; Azure DevOps | nd/flaky |
| Per-test failure rate / PFS | Meta PFS; Atlassian Bayesian | ab vs pb prior |
| Transition history without code change | Google TAP; flake-aware culprit finding | flaky vs regression |
| Co-failure with other tests in launch | Systemic Flakiness 2025: 75% of flaky failures in clusters, mean 13.5 tests | si |
| Change proximity | Launchable, Meta PTS — their ONLY features | pb |
| Environment/agent id, infra taxonomy | networking/external deps dominate systemic clusters | si |
| Test age / recent test-code edits | FlakeFlagger's surviving features | ab |
| History beats static text | Chromium study; FlakeFlagger | all |

All are **cheap SQL aggregates over data already in PostgreSQL**. GBM over ~30-50 such features + retrieval scores = highest-value, lowest-risk upgrade in the whole survey.

## 5. Launch-level reasoning (group-then-classify)
- Systemic Flakiness (EASE 2025): co-occurring failures share root causes; cluster by co-occurrence + shared stack features.
- Develocity Failure Analytics does exactly this in production. Crash-dedup is the same operation at different granularity. RCACopilot handler routing = incident-scope triage.
- **Recommended**: within a launch: (1) fingerprint (exception type + top-k normalized in-app frames + first error template, Socorro-style skip-list); (2) cluster identical/near fingerprints; (3) burst features (N same-fingerprint failures, same agent/window ⇒ strong si prior); (4) classify **clusters**, propagate label+confidence. Human feedback becomes ~10× more efficient. No GPU needed. Highest precision-per-engineering-dollar after §4.

## 6. Ranked transferable techniques
1. **Non-text feature layer + GBDT** (retry, PFS-style flakiness, co-failure size, change proximity, env/agent, test age + retrieval scores). Production-proven; CPU; works at 50 labels.
2. **Launch-level fingerprint clustering before classification** (Socorro/Sentry/Develocity + Systemic Flakiness).
3. **Hybrid retrieval over labeled history**: PG FTS BM25-ish + pgvector generic small encoder, kNN label vote weighted by feature model (Sentry existence proof; sub-100ms).
4. **Drain3 + LILAC-style LLM-on-cache-miss** as optional enhancement.
5. **RCACopilot-pattern LLM layer** (optional, async, confidence-gated, abstains) — never primary classifier.
6. **Fine-tune the embedder later** from confirm/correct contrastive pairs — the one justified "custom encoder", only after organic data.
7. **Flake-aware Bayesian statistics** (per-test reliability posterior) rather than binary flags.

## Overhyped — do-not-build
- **Deep unsupervised log anomaly detection** (DeepLog/LogAnomaly/LogBERT-as-detector): "How Far Are We?" (ICSE 2022) — F>0.9 collapses under realistic selection/grouping/imbalance; also the wrong problem (labels exist).
- **Deep stack-trace similarity** (S3M/DeepCrash): beaten by TF-IDF-family in realistic eval (arXiv 2412.14802).
- **Vocabulary/static-token flakiness predictors**: cross-project overfitting demonstrated.
- **Autonomous multi-agent RCA**: OpenRCA ~11%. Assist, don't automate.
- **Log-domain pretrained encoders as differentiator**: no production evidence.
- **Per-log-line LLM processing**: cost/latency untenable (LILAC's cache exists precisely because).
- **Zero-shot LLM classification as primary engine**: nobody shows accuracy competitive with supervised-over-history when labels exist.

Sources: FlakyLens OOPSLA25 · FlaKat · FlakeFlagger ICSE21 + replication · Chromium fault-vs-flaky (2302.10594) · Google flake-aware culprit finding / De-Flake / testing blog · Meta PFS / Predictive Test Selection / AI-assisted RCA · Atlassian via testdino benchmark · Develocity flaky guide + Failure Analytics · Azure DevOps flaky mgmt · Launchable PTS · GitLab Duo RCA · Mozilla crash signatures · TraceSim · Stack-trace dedup re-eval (2412.14802) · Sentry AI grouping blogs · LILAC FSE24 · LogPPT/LLMParser · LogBERT/BERTOps/LogAI · RCACopilot EuroSys24 · LogPrompt ICSE24 · AIOpsLab (2501.06706) · OpenRCA ICLR25 · mABC · LogSage (2506.03691) · How Far Are We (2202.04301) · Systemic Flakiness (2504.16777 / EASE25) · ReportPortal auto-analyzer.
