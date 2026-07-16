# Removing OpenSearch from ReportPortal auto-analyzer — the adversarial architecture view

> Round 1 expert panel. Role: pragmatic staff-level systems architect / MLOps lead, devil's advocate.

## 1. Is it worth it? — **Mostly no, as stated. Yes to a narrow, real subset.**

- Single shard, per-project index, ERROR-only, ≤20 deduped logs/item = a tiny inverted index by ES standards. No ILM, aggregations, dense_vector, percolator.
- Heavy ML already in Python; ES is a **BM25 candidate generator + 9 score features** — a small, well-bounded surface.
- The honest cost: **shipping a JVM datastore in the default bundle** (memory floor, backups/upgrades/CVEs, "why is my RAM at 2GB idle").

| Install size | Does ES hurt? | Right move |
|---|---|---|
| Tiny single-team | Yes — JVM RAM floor dominates | Embedded/light retriever worth it here |
| Mid | Marginal | Parity-or-bust; don't destabilize |
| Enterprise | No | **Keep OpenSearch** |

**The trap:** "eliminate operational burden" and "get BETTER results" are in tension. Realistic outcome of replacing tuned BM25+MLT is fighting to parity. "Better" comes from features/embeddings — addable WITHOUT removing ES. **Strongest option not foregrounded: keep OpenSearch but optional and slim** — the incumbent to beat.

## 2. The Retriever abstraction — the one unambiguously correct move

Build the interface first; valuable even if ES stays forever.

```
Retriever (per-project scoped)
  index_items(project_id, items[])
  delete_items(project_id, item_ids | query)
  update_issue_types(project_id, updates[])
  update_clusters(project_id, updates[])
  search_candidates(project_id, query_spec) -> [Candidate]
     # query_spec: ~20 derived fields, per-field boosts, msm policy "5<80%",
     # time-decay params, top-K, filters
     # Candidate: item_id, per-LOG hit payloads (inner_hits equivalent), relevance_score
  get_relevance_scores(candidate, query) -> scores   # the ~9 features
  query_issue_history(project_id, filter) -> labels
```

ES-specifics that leak and must be abstracted: (1) MLT query construction (express intent, not DSL); (2) inner_hits per-log extraction; (3) function_score time-decay (likely moves to Python post-scoring — score semantics change!); (4) **BM25 _score as a feature — a numeric distribution baked into trained model weights**; (5) derived-field schema/analyzer coupling (ES analyzers ≠ sklearn ≠ Tantivy tokens); (6) index-per-project tenancy encoding.

## 3. The score-feature trap — the real project

- ~9 features are normalized BM25 scores; XGBoost split thresholds learned on that distribution. Change retriever → distributions shift → silent degradation, not a crash.
- **Candidate sets also change** — different top-K, different negatives → even non-score features computed over a different population.
- Hits the global model AND every F1-gated per-project custom; some customs fail to regenerate and silently fall back to global — invisible per-tenant regression.

De-risking (non-negotiable): (1) feature-parity harness on frozen labeled replay corpus (golden candidate sets + all 40 features); (2) shadow/dual-write indexing, measure top-K overlap (recall@K, Jaccard) and score correlation (Spearman); (3) offline replay + retrain, compare on the same held-out labels; (4) score re-normalization = bridge only; (5) per-project rollout with automatic fallback, ES kept warm.

**Cost estimate: retriever swap = ~30% of work; retrain/re-validate/replay + per-tenant rollout safety = ~70%.**

## 4. Ranked recommendation + phased plan

| Option | Op simplicity | Maintainability | Footprint | Latency | Multi-tenancy | Results risk | Verdict |
|---|---|---|---|---|---|---|---|
| 0. Keep ES, optional/slim | High | High | Med (JVM) | Proven | Native | None | **Default; incumbent to beat** |
| A1. Native PG FTS | High | High | Tiny | Good small | Partitions | High (not BM25) | Reject for the 9 features |
| A2. PG + ParadeDB pg_search | Med | Med-low (churn) | Small | Good | Table/proj | Med | **Best "leave ES" candidate, with caveats** |
| A3. pgvector/embeddings | Med | Med | Small-Med | Good | Table/proj | High | Additive "better", not a BM25 replacement |
| B. Parquet+Iceberg+DuckDB | Low | Low-Med | Small binary | Batch good, writes bad | Files | High | **Reject for online serving** |
| C. Embedded lib (tantivy/FAISS) | Med | Low (own the lifecycle) | Tiny | Good | Dir/proj | Med-High | Tiny-install niche only |

Plan: **#1** retriever abstraction + parity harness (do regardless). **#2** ship "OpenSearch optional + slim single-node default". **#3** if ES must go: pg_search behind the abstraction as an *alternative* backend (AGPL churn noted — Neon dropped it for new projects, pg_textsearch emerged; support it, don't mandate it). Reject native PG FTS (not real BM25 — verified: no IDF, multi-field concat destroys per-field relevance) and lakehouse for serving (verified: DuckDB FTS rebuild-only on insert).

Phases with kill criteria:
- **P0**: interface + golden harness + ES-optional. Kill: coupling can't be cleanly abstracted → stay on ES.
- **P1 (shadow)**: pg_search dual-write, zero traffic; top-K overlap + Spearman. Kill: overlap < ~0.8.
- **P2**: retrain global + per-project; compare frozen-eval F1. Kill: below baseline, or too many customs fail the F1 gate.
- **P3 (canary)**: opt-in projects, ES warm, per-tenant dashboards, one-click rollback. Kill: any per-tenant regression above noise.
- **P4**: default flips only for tiny installs; enterprise keeps ES.

## 5. Failure modes & evidence to demand

- **pg_search**: extension availability/churn (managed-PG allow-lists; abandonment contingency); PG as shared bottleneck (p99 under concurrent write+search; autovacuum on BM25 tables); tokenizer mismatch (term-level diff, not just score correlation); catalog bloat at thousands of tables.
- **Native PG FTS**: cannot faithfully produce the 9 BM25 features. Reject unless the feature set is redesigned.
- **Lakehouse**: FTS rebuild-only, freshness — don't try for online.
- **Embedded lib**: you own persistence/recovery/corruption/cold-start/concurrency — a recurring maintainability tax; ANN recall on exact-match-heavy corpora can *underperform* BM25.
- **Cross-cutting**: silent model degradation is risk #1 — demand before/after F1 on frozen labels per project as a merge gate; per-tenant scaling variance; migration reindex time for the largest install.

## One-paragraph answer

Removing OpenSearch is sold as "drop a datastore"; it is actually "invalidate and retrain every model in the fleet against changed score distributions and candidate sets." Given ES here is a small, single-shard, already-tuned BM25 candidate generator whose real cost is a JVM in the default bundle: **(1) build the retriever abstraction and parity harness regardless, (2) make OpenSearch optional/slim to kill the footprint pain, (3) only then offer pg_search as an alternative backend — never a forced replacement.** Reject native PG FTS and the lakehouse for online serving. Chase "better results" with a semantic reranker on top of existing candidates, not by rewriting lexical retrieval you can't easily beat.

Sources: pg_search PGXN, ParadeDB ES-vs-PG, Neon pg_search deprecation, PG-FTS-vs-ES comparisons, DuckDB FTS docs + #3543, DuckDB Iceberg writes, Iceberg practical limitations 2025.
