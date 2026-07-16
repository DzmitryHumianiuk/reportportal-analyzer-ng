# Replacing OpenSearch with PostgreSQL-native retrieval — an adversarial evaluation

> Round 1 expert panel. Role: senior PostgreSQL + information-retrieval engineer. Verdict: **4/5**.

## 0. The single most important framing fact

Two things dominate every conclusion:

1. **The indexed corpus is small.** Only error-level logs, ≤20 per test item, near-dups dropped, per-project index. This is *not* the 10M+ row regime where native Postgres FTS collapses. The classic "Postgres FTS doesn't scale like Elasticsearch" argument — real above ~2M rows ([Neon 10M-row benchmark](https://neon.com/blog/postgres-full-text-search-vs-elasticsearch)) — **mostly does not apply to a single RP project.** It re-emerges only in the aggregate multi-tenant dimension (§5).

2. **The ES BM25 `_score` is not the matcher — the Python reranker is.** BM25 feeds only ~9 of ~40 features; the 15 sklearn TF-IDF cosine features are the real fine-grained matching; time-decay is already reimplemented in Python. Stage 1's actual job is **recall into top-K**, not perfect ranking. Anything with comparable recall@K is viable; exact BM25 fidelity is a nice-to-have.

## 1. Design sketch

**Storage** — keep the analyzer's *own* Postgres (do **not** reach into RP's main OLTP DB; see §3):
- `test_item` table + `log` table (nested logs[] flattened), FK to test_item.
- **Generated `tsvector` columns** per searchable text field, GIN-indexed. The ~20 derived fields computed by the same Python preprocessing, written as plain columns — logic moves unchanged; only the sink changes.

**Stage 1 candidate generation** — replace MLT + function_score:
- Per query log: extract salient terms in Python, build weighted OR `tsquery` per field, one SQL query with UNION/OR across fields, per-field weights via `setweight`/score multipliers, exp time-decay as SQL arithmetic on `start_time`, order by combined score, LIMIT K, `DISTINCT ON (test_item_id)`.

**Stage 2** — unchanged (featurizer, TF-IDF cosine, DefectTypeModel, XGBoost). The ~9 "ES-score" features fed the PG relevance score. **Models must be retrained.**

**Painless → SQL is a simplification** (issue_history append = jsonb concat or child-table insert).

### Three implementation tiers
- **Tier A — native tsvector/tsquery + ts_rank_cd + pg_trgm.** Zero new extensions. Weakness: not BM25.
- **Tier B — ParadeDB pg_search (Tantivy/BM25).** Closest to ES semantics. Weakness: AGPL + not on managed PG.
- **Tier C — Tier A + pgvector hybrid.** Semantic recall for "better than parity".

## 2. Nested logs[] + inner_hits in relational PG

- **Separate `log` table with FK (recommended).** Query matches at log granularity; `DISTINCT ON (test_item_id)` ordered by score = the inner_hits best-match extraction; window-rank keeps top-5 logs per item. Cleaner and faster than JSONB.
- JSONB array mirrors ES 1:1 but forces lateral joins and loses per-log FTS attribution. Relational child table wins. (pg_search can index JSONB directly if Tier B.)

## 3. "Logs are already in Postgres" — query RP's main DB directly? **Reject.**

- **Derived fields don't exist there** — computed at index time in the analyzer. Recomputing at read time kills latency; adding columns to tables the analyzer doesn't own = schema-ownership liability.
- **OLTP contention** on the busiest tables in the product.
- **Coupling** breaks the AMQP decoupling; inherits the main DB's tenancy model.
- Main DB has ALL levels; analyzer wants a tiny error-only slice.
- Middle path if insisted: logical-replication subscriber / read replica — but that rebuilds a separate index store anyway. "Logs already in PG" argues for PG *technology*, not a shared physical store.

## 4. BM25 fidelity — the four Stage-1 options

### (a) Native tsvector + ts_rank/ts_rank_cd
- **Not BM25** — no IDF ([Tiger Data](https://www.tigerdata.com/blog/introducing-pg_textsearch-true-bm25-ranking-hybrid-retrieval-postgres)); rare tokens (exception class, status code) under-weighted; `ts_rank_cd` poor on OR queries (RUM docs). But given §0.2 (reranker is the matcher), ts_rank only orders candidates for top-K cutoff. **Lowest-risk, most-portable; default recommendation given the small corpus.**

### (b) RUM extension
- Positions/timestamps in posting list; faster ranking and `ORDER BY timestamp` ([RUM](https://github.com/postgrespro/rum)). Still not BM25; niche single-vendor extension; not on managed PG; worse write performance. **Skip unless time-decay ordering is a measured bottleneck.**

### (c) pg_trgm
- Excellent fuzzy/near-dup on short strings; not a relevance ranker; trigram explosion on long stacktraces. **Auxiliary signal only.**

### (d) ParadeDB pg_search — the real contender
- True **BM25** (Tantivy), `@@@` operator, multi-field, query-time boosting, fuzzy, **a native `more_like_this` query type**, JSON query syntax, JSONB indexing ([intro](https://www.paradedb.com/blog/introducing-search), [v2 API](https://www.paradedb.com/blog/v2api)). Only option reproducing ES MLT semantics natively.
- Production-vetted: packaged for PG 15+, [PGXN ~0.22.x](https://pgxn.org/dist/pg_search/), on Neon. Benchmarks: 10M rows top-N **81ms vs 38,797ms native FTS**; "matches or exceeds Elasticsearch" ([Neon benchmark](https://neon.com/blog/postgres-full-text-search-vs-elasticsearch)).
- **Cons:** **AGPLv3** + commercial dual license — governance decision for OSS distribution. **Not on AWS RDS/Aurora** or most managed PG. **Tantivy segment write-amplification** (merge spikes, dead-segment VACUUM; 0.20+ LSM mitigates) — a Lucene-like ops profile *inside* Postgres.
- License-clean alternatives to watch: Tiger Data **pg_textsearch** (Apache, BM25 + Block-Max WAND), **VectorChord-BM25**.

### (e) Emulating more_like_this without pg_search
Reproduce in Python: term weights from precomputed per-project IDF (`ts_stat` or reuse sklearn IDF), top-N terms per field, `to_tsquery('t1 | t2 | …')`, `minimum_should_match "5<80%"` = require ≥ceil(0.8·n) matched lexemes. More code you own; deterministic.

## 5. pgvector hybrid — the "better than parity" upside

- Embed `detected_message`/`stacktrace` at index time, `vector` column, HNSW; cosine distance becomes new reranker features. Semantic recall catches paraphrased/refactored errors.
- pgvector 0.8.0 **iterative index scans** fix filtered-HNSW overfiltering (up to 100x completeness on selective filters — [AWS](https://aws.amazon.com/blogs/database/supercharging-vector-search-performance-and-relevance-with-pgvector-0-8-0-on-amazon-aurora-postgresql/)); HNSW ~5-8ms; pgvectorscale competitive with Qdrant.
- Cons: embedding inference on both paths (~90ms/query reported); model packaging/versioning for an OSS product; HNSW build single-threaded per connection, `maintenance_work_mem` sensitivity ([#969](https://github.com/pgvector/pgvector/issues/969)) — non-issue per-project, matters in aggregate.
- **Phase 2** after lexical parity is proven.

## 6. Multi-tenancy — one-index-per-project mapped to PG

- **Option 1 (recommended): single wide tables, LIST/HASH partitioned by project_id.** Per-partition indexes = ES's small-index-per-project isolation; partition pruning = per-project scoping. Risk at tens of thousands of partitions (planning/catalog) — sub-partition or archive dormant projects.
- Option 2 table-per-project: catalog bloat at thousands of projects. Not recommended.
- Option 3 one table + RLS: global indexes lose the small-index property. Weakest.
- **Where PG FTS falls over vs ES**: aggregate only — (a) very large single projects (ts_rank scoring-all-matches degrades to seconds at millions of rows); (b) thousands of partitions straining catalog/autovacuum; (c) concurrent HNSW rebuilds. pg_search/pg_textsearch Block-Max WAND closes (a).

## 7. Operational reality

- **Online latency**: GIN/BM25 top-K over a few thousand rows = single-digit-to-low-tens ms; HNSW 5-8ms; the 600s cap is irrelevant; <1s/item easily met. Dominant cost stays the Python reranker.
- **Write throughput**: GIN `fastupdate`/pending-list; capped write volume — modest. pg_search: heavier write amplification (0.20+ background merges). **Native FTS = safer write profile.**
- **Bloat/VACUUM**: autovacuum tuning on hot partitions; pg_search dead segments; HNSW bloat on heavy updates.
- **Pooling**: PgBouncer transaction pooling; trivial vs ES HTTP.

## Pros
- One system instead of two — eliminates the JVM cluster (heap, shards, upgrades, monitoring).
- Small corpus makes PG natural; reranker relaxes candidate-gen fidelity → native FTS viable.
- Painless → SQL simplification; transactional consistency (no ES refresh gap).
- Upside path: pgvector hybrid could beat lexical-only recall.

## Cons / Risks
- **ML revalidation mandatory**: retrain XGBoost (global + per-project) and DefectTypeModel; re-validate 0.4 threshold. Biggest hidden cost — offline eval (recall@K, precision, defect-type accuracy) vs ES baseline required.
- Native FTS not BM25 — quantify, don't assume away.
- MLT hand-rolled in the native path.
- pg_search strings: AGPL, no managed-PG, Tantivy write profile.
- Multi-tenancy at thousands of projects needs deliberate partitioning.
- pgvector hybrid adds an embedding model to package/version/serve.

## Migration effort
Schema + ingest sink swap: **M**. Stage-1 retriever rewrite: **M** (native) / **S–M** (pg_search). ML retraining + offline eval + threshold recalibration: **M–L** (critical path). Multi-tenancy/ops: **M**. pgvector hybrid: **M** on top. **Overall: M–L.**

## Verdict — Fit score: 4/5

Good fit precisely because the corpus is small and the reranker is the real matcher. Lowest-risk: **Tier A native tsvector + pg_trgm, MLT emulated in Python, decay/boosts in SQL, per-project partitioning, reranker retrained and validated against the ES baseline**. Reserve pg_search for self-hosted installs wanting true BM25 + native MLT (watch pg_textsearch as license-clean BM25). Layer pgvector as phase-2. Don't undersell ML revalidation. Firmly reject querying RP's main OLTP DB directly.

### Sources
ParadeDB GitHub/PGXN/blogs (introducing-search, v2api, write-performance, index_size) · Neon pg_search docs + benchmark · Tiger Data pg_textsearch · VectorChord-BM25 · postgrespro RUM · pgvector 0.8.0 (AWS), #969, pgvector-vs-Qdrant · AWS RDS extension allow-list · PG FTS vs ES at scale writeups.
