# Small Local LLMs in Test-Failure Triage — Design Analysis

> Round 2 greenfield consilium. Role: senior applied-LLM engineer (on-prem/local inference).

**TL;DR:** Keep the retrieval + GBM core. The SLM earns its keep in exactly four places: **cold-start triage, explanation generation, structured extraction, low-confidence adjudication**. Everything else is wasted watts. Reasoning modes are mostly wasted tokens — the evidence now exists to say so concretely.

## 1. Model landscape 2026 (local-deployment lens)

| Family | Sizes | License | Notes |
|---|---|---|---|
| **Qwen3** | 0.6/1.7/4/8B | Apache 2.0 | Best small family; dual-mode thinking switch; 128K ctx ≥4B; Qwen3-4B ≈2.5GB GGUF Q4 — the sweet spot |
| **IBM Granite 4.0/4.1** | Nano 350M–1B, Micro 3B, Tiny 7B MoE, Small 32B | Apache 2.0, ISO 42001, signed | Hybrid Mamba-2: >70% lower memory, ~2x faster long-context; enterprise/RAG-trained; governance story sells to enterprises |
| **Phi-4-mini** | 3.8B | MIT | Best per-param benchmarks; 128K ctx; but Phi-4-Mini-Reasoning was the WORST model in the log-classification benchmark |
| **Gemma 3** | 1/4B | Gemma ToU (not Apache) | User-pulled option, not shipped default |
| **Llama 3.2/3.3** | 1/3/8B | Community license | Support, don't bundle |
| **Mistral Small** | 24B+ | Apache 2.0 | GPU-tier only (~2 tok/s on old Xeons) |
| **DeepSeek-R1-Distill** | 1.5/7B | MIT | Reasoning-distilled — actively bad for this workload (§4) |

### Realistic CPU throughput (llama.cpp/Ollama, Q4_K_M)
| Size | Old server CPU | Modern desktop/EPYC | Xeon AMX |
|---|---|---|---|
| 0.6-1.7B | 15-25 tok/s | 30-60 | 80+ |
| 3-4B | 5-10 | 15-30 | ~50 |
| 7-8B | 2-8 | 10-15 | ~45 |
| 24B+ | unusable | 3-6 marginal | GPU territory |

**Critical: prefill dominates on CPU.** A 3K-token triage prompt into a 4B model = 20-60s to first token on a modest 4-core VM. Fits async queue; kills interactive suggest → **interactive path stays embeddings-based; LLM writes async, UI reads back.**

Minimum hardware: (a) classification-grade (grammar-constrained enum, ~50 tok out): 1.7-3B Q4, 4 cores + 4-6GB. (b) short explanations: 4B Q4, 8 cores + 8GB, tens of seconds, async-fine. (c) reasoning-mode triage: 500-3000 thinking tokens = 1-5 min/failure on CPU → **should not exist as a CPU configuration**; needs 8-12GB GPU (Qwen3-8B Q4 at 40-70 tok/s).

## 2. Role-by-role verdicts

### a) LLM-as-classifier — **NO.**
- Per-project GBM: retrains in seconds on CPU, calibrated, μs inference, improves with feedback. Zero/few-shot SLM: no access to team-convention label semantics; can't learn without fine-tuning; 4-6 orders of magnitude more expensive.
- Cited LLM-beats-XGBoost results (F1 0.928 vs 0.555) are settings with weak features and no per-domain labels — not this setting.
- [SLM log-severity benchmark (arXiv 2601.07790)]: zero-shot SLMs at **<10-34% accuracy** on 5-way log classification; only with retrieval did Qwen3-4B hit 95.6% — the *retrieval* did the work.
- Legitimate only as tiebreaker on the GBM low-confidence band → role (b).

### b) LLM-as-reranker/judge (gated) — **YES.**
- Given query + top-K labeled neighbors: "same root cause or coincidental similarity?" Where embeddings/BM25 genuinely fail (90% shared boilerplate ≠ same cause; no shared tokens = same cause).
- Benchmark: RAG-augmented Qwen3-4B 56%→95.6%. Highest accuracy-per-token of any role. Gate to GBM max-prob <0.6 (~10-20% of failures). Output constrained to enum → hallucination surface = one enum.

### c) LLM-as-explainer — **YES, ship first.**
- "Classified as automation bug because it matches failure #4812 (labeled by J. Kim 2026-05-02): same StaleElementReferenceException in LoginPage.submit..." Decision already made classically; worst case = clumsy sentence.
- Facts injected from template; LLM verbalizes; verbatim quotes validated by substring check. Transforms "score: 0.87" into "here's why" → drives confirmation rates → feeds the classical flywheel.

### d) LLM-as-extractor — **YES, quietly highest-leverage.**
- Structured extraction: root exception vs wrapper chain, failing layer (test/app/infra), assertion vs timeout vs connection-refused, env markers → JSON (grammar-constrained) → columns → GBM features + UI facets.
- Regex does 70%; LLM covers the long tail without per-framework parser maintenance. Once per failure at ingest, cached by template-hash. Graceful failure: one noisy feature, not a wrong verdict.

### e) LLM-for-cold-start — **YES, biggest genuine capability gap filled.**
- Zero labels → similarity useless → product proves no value exactly when it must. Zero-shot rubric + extractor signals → provisional labels flagged "AI-suggested". Honest accuracy: 50-70%, not 90% — still infinitely better than empty, and every confirm becomes a training label. LLM is scaffolding the GBM grows over.

### f) Per-install LoRA — **NO for v1.**
- QLoRA on 4-8B needs ~8GB VRAM + 1-2h (Unsloth) — GPU precisely at installs; per-tenant adapter versioning/eval/rollback breaks the "no harder than ElasticSearch" promise; same labeled data retrains embeddings+GBM in seconds. Only plausible: one product-level LoRA shipped as "the triage model" — vendor R&D, not customer ops.

| Role | Accuracy value | Cost | Risk | Verdict |
|---|---|---|---|---|
| Classifier | Negative vs GBM | High | Unlearnable wrong labels | **Reject** |
| Judge (gated) | High on hard 10-20% | Medium | Low (enum) | **Adopt** |
| Explainer | UX only | Medium async | Minimal | **Adopt first** |
| Extractor | Medium, compounding | Low (cached) | Graceful | **Adopt** |
| Cold-start | Fills real hole | Low | Medium; flagged | **Adopt** |
| Per-install LoRA | Marginal | Very high ops | High | **Reject v1** |

## 3. RAG / integration design
- **Context**: never raw logs. Template/mask volatiles; stacktrace middle-out truncation (root exception + first/last N frames + project-namespace frames); budget ~3K tokens (1.5K query, 400×3 candidates, 300 instructions). Granite's Mamba wins if lazier truncation wanted.
- **Structured output non-negotiable**: llama.cpp JSON-Schema→GBNF token masking — invalid output impossible; Ollama `format: <schema>`; vLLM guided decoding on GPU. Judge schema: `{match: candidate_1..K|none, confidence: low|med|high, evidence_lines: [string], abstain_reason?}`.
- **Hallucination control**: enum-constrained decisions; verbatim-quote substring validation (discard on failure); explicit abstain → `ti`; LLM never moves an item out of `ti` alone.
- **Evaluation for free**: confirm/correct stream = continuously refreshed test set. Log every suggestion with prompt hash + model version; nightly acceptance/precision/lift-over-GBM per gated band; auto-disable per install when no lift.

## 4. Reasoning models: mostly no
- Direct evidence (same task shape): reasoning models did NOT consistently beat non-reasoning peers; several degraded WITH RAG (DeepSeek-R1-Distill-1.5B → 3.17% accuracy with retrieval; Qwen3-1.7B 43%→29%); Phi-4-Mini-Reasoning burned 228 s/log for <10%. Retrieval quality, not chain-of-thought, drove the gains.
- Triage vs labeled neighbors is evidence comparison, not multi-step derivation. Keep Qwen3's switchable thinking as hedge: default `/no_think`; optional short budget for judge on GPU installs only if own eval shows lift.

## 5. Ops reality
- **Licensing**: bundle Apache-2.0/MIT only — Qwen3, Granite 4.x (signed, ISO 42001 — enterprise procurement answer), Phi-4-mini. Llama/Gemma user-pulled. Default: Granite Micro-3B for extractor/explainer + Qwen3-4B for judge, or standardize on Qwen3-4B for all.
- **Runtime**: Ollama-as-optional-sidecar; models pulled by digest on explicit admin action; degrades to today's behavior if absent; same OpenAI-compatible surface for a vLLM GPU tier and an explicit opt-in external-API escape hatch.
- **Sizing**: tiny (4 cores +6GB): 1.7B/Nano — extractor+coldstart only, templated explanations. Standard (8-16 cores +8-12GB): 4B Q4 — all async roles. Enterprise (1× 12-24GB GPU): 8B/Tiny-7B, wider judge band, near-interactive.
- **Multi-tenant isolation**: shared model process; retrieval strictly project-scoped; **KV/prompt caches keyed per project or disabled** (shared-prefix KV cache = cross-tenant side channel); no cross-project few-shots; per-tenant rate limits.
- **Prompt injection — logs are attacker-influenceable** (HTTP bodies, user agents, DB values; documented log-substrate injection class, arXiv 2605.24421; OWASP LLM01). Defenses: (1) grammar-constrained output — injection can at worst flip an enum; (2) delimited data blocks; (3) verbatim-quote validation kills fabricated evidence; (4) LLM suggests, never auto-confirms; (5) strip prompt-shaped sequences in preprocessing. Worst case must remain "one wrong suggestion a human corrects."

## 6. Integration sketch
```
fail → preprocess (mask, template-hash)
  ├─ embeddings+BM25 retrieval → GBM (per-project)
  │     conf ≥ τ → label + LLM-explainer (async, UX)
  │     conf < τ → LLM-judge over top-K (grammar JSON) → suggest
  │     no labels (cold) → LLM rubric → provisional label
  ├─ LLM-extractor (per template hash, cached) → columns → GBM features
  └─ confirm/correct → labels DB → GBM retrain (seconds) + LLM eval dashboard
```
**LLM genuinely beats classical ML at**: cold start, semantic judgment on boilerplate-similar/paraphrase-different logs, long-tail extraction, natural-language explanation. **Not at**: steady-state per-project classification with hundreds of labels — there the GBM is more accurate, ~10⁵× cheaper, and actually learns from users.

Sources: Qwen3 lineup/blog, IBM Granite 4.0 announcement/InfoQ, Ollama CPU benchmarks (Markaicode), llama.cpp CPU paper (CEUR-4164), SLM/SRLM log-classification benchmark arXiv 2601.07790, MDPI cyber-log LLM-vs-XGBoost, ReportPortal docs, llama.cpp grammars, Ollama structured outputs, constrained-decoding guide, log-substrate injection arXiv 2605.24421, OWASP LLM01, Unsloth requirements, Phi-4-mini guide, license comparisons, Mistral Small 4, DeepSeek-R1 local guide.
