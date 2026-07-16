# Failure-Triage Architecture — Consilium Deliverable (Chief Architect)

> Round 2 greenfield consilium. Role: chief architect with out-of-the-box mandate + production discipline.
> Three candidate architectures, scored; pick: **B "Knowledge-base-first" (32.5) over A "Postgres-max" (27) and C "Agentic" (21-22.5).**

## Shared physics (constraints every candidate must respect)

1. **Never embed/index all logs.** Analysis unit = failed test item; only a distilled failure document (~1-2 KB) needs ML treatment. Cuts the vector/index problem by 2-3 orders of magnitude — what makes "everything in Postgres" viable.
2. **The interactive 1-3s budget is a red herring if you precompute.** Heavy analysis runs async at ingest; the UI path is a *read* of a stored suggestion. No candidate does model inference on page load.
3. **Launch-level grouping is the single highest-leverage change**: one infra outage → 50 failures → ONE group → ONE diagnosis, fanned out. Cuts compute 10-50× on the most expensive launches and is the correct semantics.
4. **Confidence is a product feature.** A wrong confident label destroys trust permanently. All candidates share a calibrated decision layer with abstain → `to-investigate` + "why we're unsure".

**Shared signature pipeline:** Drain3 (MIT, streaming, CPU-cheap) masks variables → per-project template store. Signature = (exception_fingerprint, ordered ERROR template IDs, top-k user-namespace frames). Embedding: local int8 ONNX bge-small/e5-small (384-dim, ~30MB, 1-5ms/doc CPU).

## Architecture A — "Postgres-max"
Everything in PG; retrieval over raw labeled failures; GBM decides; optional Ollama for prose.
- Storage: all in primary PG — log_template (Drain3 state as rows), failure_signature (halfvec 384, HNSW w/ pgvector ≥0.8 iterative scans; hash-partition by project at scale), label_event (append-only training data), suggestion (precomputed verdicts). DuckDB/Parquet: cold tier + offline mining.
- FTS honesty: PG native FTS is not BM25 and mediocre on log text; ParadeDB pg_search is real BM25 but **AGPL-3.0** — operator-opt-in only, cannot bundle in an Apache-2.0 product. Mitigation: trigram + template-ID exact matching does most lexical work on logs.
- Training: ONE install-wide LightGBM (features are similarity/history *statistics*, project-agnostic), nightly retrain, per-project isotonic calibration.
- Cold start: weak — zero labels → kNN empty → Ollama zero-shot or nothing. A's biggest hole.
- Footprint: tiny = 1 analyzer container (~1 vCPU, 1-2GB); mid = +Ollama CPU; enterprise = pool + partitioned PG + 1 GPU optional.
- Verdict: minimal, shippable, boring — but re-implements the old paradigm with better plumbing. Label soup stays soup; "similar to item #48213" explanations don't earn trust.

## Architecture B — "Knowledge-base-first" ⭐
The unit of knowledge is a curated **failure mode**, not a raw labeled log. Label transfer happens at mode level.

```
FAILURE-MODE KB (per project): centroid emb · representative templates ·
  exception_fp set · human label + purity · SLM title/summary · stats · status
  (candidate | confirmed | retired)

logs → PG → on FAIL: Drain3 → signature → embed
 [1] launch grouping (as in A)
 [2] KB match per group-rep: exception_fp exact fast-path;
     centroid cosine + template Jaccard
 [3] decide (GBM over mode-match + history/launch features, calibrated)
 [4] P≥τ: inherit MODE's label; else to-investigate + spawn/extend CANDIDATE mode
 async SLM sidecar: mode titles/summaries · zero-shot label for NEW modes ·
   judge ambiguous matches
 UI: "matched FM-123 'Payment gateway timeout', seen 47×, confirmed product-bug
   by Alice 3d ago"
 confirm/correct → label_event → purity update → GBM retrain
 user curates KB: merge/split/relabel (retro-applies optionally)
```

- Same substrate as A **plus** failure_mode + mode_membership. Hot vector search = **modes, not items** (hundreds-low-thousands of centroids per project) — multi-tenant vector search becomes trivial; raw item embeddings age out to Parquet.
- Non-text signals enter twice: GBM features (flake score, fail-rate, co-failure count, launch fail-fraction, env/agent, changed-files proximity) and a systemic-launch detector (>X% of launch fails with one dominant new signature → si prior for the whole group).
- **Cold start — B's superpower**: ship a **seed KB of ~30-50 generic modes** with prior labels (OOM→si, StaleElement→ab, Connection refused/DNS/5xx burst→si, AssertionError→pb-leaning, timeouts, disk-full…). Useful, *explained* triage on day one with zero labels and zero LLM. SLM zero-shot is the second line for novel modes.
- Feedback: confirm/correct updates mode purity (confirmed at purity ≥ threshold with ≥N events); centroids drift via EWMA; TTL retirement; nightly offline job proposes merges/splits for human review. The KB becomes a visible product surface — a per-project failure catalog with trends — the explainability jackpot.
- Honest weaknesses: incremental clustering can fragment or collapse on chatty templates — mitigated by human merge/split as first-class UI and matching on exception_fp + Jaccard, not embeddings alone.
- Footprint: same as A + 0 (KB is rows in PG; SLM optional). Tiny installs run B without any LLM.

## Architecture C — "Agentic triage"
Local LLM agent with tools (get_failure_logs, get_test_history, get_launch_siblings, search_kb, search_past_failures, diff_env), ≤8 tool calls, ≤120s, structured verdict.
- Storage identical to B (an agent without the signature/KB substrate is an agent grepping soup).
- **The autopsy**: 5-8 tool calls × 7-14B = 30-120s per group on a modest GPU; CPU-only = minutes and dumber → excludes the majority of installs. Nondeterminism (same failure, two runs, two labels) is poison for a trust-critical feature. A small OSS team cannot maintain an eval harness for a multi-step agent; GBM+calibration has a number, an agent has vibes.
- Genuine win: novel failures needing cross-referencing — the abstain bucket (10-20%). **Right as an optional escalation tier over B, enterprise-with-GPU, badged "AI investigation".**

## Scoring matrix (1-5)

| Criterion | A | B | C |
|---|---|---|---|
| Result-quality potential | 3 | 4 | 4.5 ceiling / 3 realized CPU |
| Cold start | 2 | **5** | 3 |
| Explainability/trust | 3 | **5** | 3.5 |
| Footprint & ops OSS | **5** | 4.5 | 2 |
| Maintainability small team | 4 | 4 | 2 |
| Privacy | 5 | 5 | 4.5 |
| Incremental adoptability | 5 | 4.5 | 2 |
| **Total** | **27** | **32.5** | **21-22.5** |

## Final pick: B on A's substrate, with C as optional enterprise escalation

B changes the *knowledge representation* (labeled raw logs → curated failure modes), which simultaneously solves cold start (seed modes), explainability (named modes with provenance), multi-tenant vector scale (centroids), and the 50-identical-failures problem. Degrades gracefully: strip the SLM — works; strip the GBM — seed KB still works.

### Phased rollout
- **P1 Substrate**: Drain3, signatures, launch grouping, seed KB with priors, pgvector + embeddings at ingest, abstain-by-default suggestions. Zero models beyond a 30MB embedder.
- **P2 Learning loop**: per-project mode clustering (candidate→confirmed lifecycle), mode-level label transfer, install-wide LightGBM + per-project calibration, KB curation UI, label_event canonical.
- **P3 SLM sidecar** (feature-flagged, Ollama API): mode titles/summaries, zero-shot novel modes, judge, human-readable "why". Async only.
- **P4 Enterprise tier**: agentic investigation of abstained groups (GPU, opt-in), Parquet/DuckDB cold tier + offline mining, optional pg_search AGPL add-on.

### Explicitly rejected fashionable components
| Rejected | Why |
|---|---|
| Elasticsearch/OpenSearch | the ops tax being escaped |
| Dedicated vector DB | another stateful service; centroid-level search makes pgvector overkill-proof |
| pg_search as DEFAULT | AGPL vs Apache-2.0 product; operator add-on only |
| LLM as primary classifier | nondeterministic, slow on CPU fleets, unevaluatable |
| Per-project fine-tuned transformers | thousands of projects × pipelines × small team = no |
| Agentic backbone | see C autopsy; Phase-4 escalation only |
| Kafka/Flink | PG queue (FOR UPDATE SKIP LOCKED) handles the volume |
| Knowledge graph / GraphRAG | the "graph" is one table; no retrieval gain |
| Cross-encoder everywhere | CPU cost for marginal gain over fp+Jaccard+cosine |
| Federated/cross-install learning | hard privacy line; ship seed KB + default GBM on public corpora instead |

Sources: pgvector 0.8.0 release · pgEdge filtering docs · ParadeDB pg_search (AGPL) · ParadeDB BM25 intro.
