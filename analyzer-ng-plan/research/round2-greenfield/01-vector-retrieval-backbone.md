# Retrieval/Storage Backbone for a Failure-Triage Platform — Design Consilium Analysis

> Round 2 greenfield consilium. Role: senior vector-search / retrieval-infrastructure architect.

## 0. Honest framing

- The retrieval corpus is **labeled failures per project** — 10⁴–10⁵ vectors per tenant, not 10⁸.
- At that size, *exact* brute-force cosine over a filtered set is single-digit ms on CPU. What matters: **multi-tenant filtering correctness, hybrid quality, transactional consistency with the label store, ops footprint for OSS self-host** — not billion-scale benchmarks.

## 1. Recommended backbone

### Primary: PostgreSQL as the retrieval engine — pgvector (+ VectorChord at scale) + a BM25 extension (VectorChord-bm25 or ParadeDB pg_search)

1. **The data is already there.** Embeddings next to labels give **transactional consistency between the label and the vector** — no dual-write, no sync job, no split-brain. For a continuous human-feedback product this is the single most valuable architectural property.
2. **Multi-tenancy = `WHERE project_id`** (+ partitioning/RLS). Thousands of projects trivially.
3. **Filtered ANN respectable**: pgvector 0.8.0 iterative index scans fixed overfiltering ([release](https://www.postgresql.org/about/news/pgvector-080-released-2952/)); for tenants <50k rows skip ANN entirely — exact scan, 100% recall.
4. **Hybrid in one engine is real**: VectorChord-bm25 (Block-WeakAnd BM25 + pg_tokenizer) or ParadeDB pg_search (Tantivy, more mature, AGPL). RRF = 15-line SQL CTE. Native tsvector = adequate fallback for exact-token matching on exception names.
5. **Headroom**: VectorChord (RaBitQ IVF) ~2x pgvector QPS at >95% recall; pgvectorscale as the other scale-up. Upgrade an extension, not the architecture.
6. **Ops: zero new stateful services** — every extra container is a support-ticket generator for self-hosted OSS.

Caveats: extension packaging is your problem (ship PG image with pgvector+BM25 ext; AGPL legal look for ParadeDB; VectorChord-bm25 younger); keep embeddings in a separate table, only for labeled/analyzable items; 10M+-vector single tenants would strain single-node PG (doesn't exist in this product's reality).

### Alternative: Qdrant (single container)
Sparse+dense native RRF/DBSF in one query API; payload-based tenancy (1.16 tiered); filter-aware HNSW; single Rust binary. Cost: second stateful store, dual-writes, label-sync code = where triage-correctness bugs will live; BM25 is bring-your-own-sparse-encoder client-side.

## 2. Comparison table (2026, THIS use case)

| Criterion | pgvector+BM25-in-PG | Qdrant | Milvus | Weaviate | LanceDB | Chroma |
|---|---|---|---|---|---|---|
| Self-host footprint | **None added** | 1 container | Heavy (etcd+workers) | 1 heavier container | Zero (embedded) | 1 light |
| Tenancy @1000s | Native, unbounded | Payload, good | Partition-key, ops-costly | Per-tenant shards, overhead | File sprawl or no isolation | Weak |
| Hybrid one-engine | Yes (ext + RRF SQL) | Yes native | **Best ergonomics** (server-side BM25) | Yes (alpha blend) | Yes (Tantivy FTS) | Immature |
| Filtered ANN | Good (0.8 iterative; exact option) | Excellent | Good | Good | Adequate | Weak history |
| Upsert/delete/label mutation | **MVCC transactional instant** | Good | Segment machinery | Good | Tombstone/rewrite — weakest | OK |
| Ops maturity small OSS team | 25y of PG knowledge | Safest dedicated | Low ops fit | Medium | Young, embedded=you own durability | Single-node |
| Verdict | **Ship default** | Scale-out alternative | No | No | Cold tier | No |

**pgvector-in-existing-PG is the pragmatic winner — decisively.** Not on ANN benchmarks; because the workload is small-per-tenant, mutation-heavy (relabeling), consistency-critical, shipped to PG-running customers.

## 3. What to embed and how

### 3.1 Don't embed raw logs — embed a failure-signature document
1. Normalize via Drain3-style template mining (mask numbers/uuid/hex/paths/durations).
2. Structure stacktraces: exception_type, top-N *application* frames (framework frames dropped by per-project namespace heuristics), root-cause message.
3. One signature doc per failed test item:
```
EXC: java.lang.IllegalStateException
MSG: Connection pool exhausted after <NUM> retries
TOP_FRAMES: com.acme.db.PoolManager.acquire; com.acme.api.OrderService.create
TEMPLATES: [top-5 error templates by rarity]
TEST: OrderCreationSuite.testBulkOrder
```

### 3.2 Chunking
One **primary embedding per test item** (the labeled unit; cap ~512 tokens). Optional secondary embeddings per distinct error template (≤10) for evidence highlighting. Never per-raw-line. Single concatenated structured doc for dense; **separate BM25 columns** for exception_type/templates/frames for field-boosting. Multi-vector-per-field = 3-4x cost for marginal gain — skip.

### 3.3 Schema (metadata payload)
```sql
CREATE TABLE failure_vectors (
  item_id bigint PRIMARY KEY, project_id bigint NOT NULL,
  launch_id bigint, test_case_hash bigint,
  issue_type text, label_source text, label_ts timestamptz,
  error_hash bigint, exception_type text,
  signature_text text, emb vector(512), emb_model_ver smallint, created_at timestamptz
) PARTITION BY HASH (project_id);
```
`error_hash` matters: a large share of hits are exact recurrences — hash lookup before vector math.

### 3.4 Model & lifecycle
- Default: **nomic-embed-text-v2** (137M, Matryoshka 768→64, best CPU throughput) or **bge-m3** (dense+sparse+multi-vector) via ONNX int8; truncate to 512/384 dims.
- Floor tier: static Model2Vec-class (~30MB, 100x speed) as degraded mode — measure first.
- Stamp `emb_model_ver`; upgrade = background re-embed per project (minutes-hours); never mix versions; BM25 fallback for unmigrated rows. No per-tenant fine-tuning — the BM25 leg's corpus-adaptive IDF handles tenant vocabulary (why hybrid is non-optional).

## 4. Retrieval architecture

```
fail → normalize → error_hash exact? ─yes→ decision layer
                     │no
   ┌ BM25 top-50 (field-boosted)   [filter: project, labeled only, emb_ver]
   ┴ dense ANN top-50
   → RRF (k=60) → top-20 → feature assembly → decision layer
```
- Retrieve **labeled items only** (unlabeled = separate "similar unresolved" feature, not the vote).
- **RRF, not score blending** (incommensurable scores; exception-type tokens are where pure dense underperforms — NPE vs IllegalState embed too close).
- **Time-decay & label-confidence in the decision layer, not the index**: `w = rrf × recency_decay(90d) × source_weight(human=1.0, ai_accepted=0.6)` over 20 rows — free.
- Contract per candidate: `{item_id, dense_rank, sparse_rank, rrf_score, cosine, issue_type, label_source, label_ts, same_test_case, same_error_hash, launch_distance, comment}` + aggregates `{label_histogram, top1_margin, corpus_size}` (abstain when corpus <30 labels).
- Latency: PG hybrid over ≤100k-row partition = tens of ms; budget dominated by query-signature embedding (~50-200ms CPU for a 137M int8 model).

## 5. LanceDB/DuckDB angle

- **DuckDB-vss online: no** — experimental persistence flag, RAM-bound index, deletes tombstone, no filter-composed ANN.
- **LanceDB is interesting**: embedded, Lance columnar on disk/object storage, Tantivy FTS with BM25 + native hybrid RRF; single-logical-writer analyzer sidesteps its weakest spot. But for default OSS ship: (a) tenancy = thousands of Lance tables (sprawl) or one filtered table (no isolation); (b) relabel-as-update fights append+compaction; (c) hybrid FTS is recent, thin ops lore. **Right role: cold/analytics tier (v2)** — nightly export of aged launches to Lance/Parquet on MinIO/S3, DuckDB as analyst engine.

## 6. Anti-recommendations
Milvus (deployment graph kills small self-host adoption); Weaviate (heavier than Qdrant, per-tenant overhead at thousands); Chroma (RAG-prototyping tier); DuckDB-vss online; Elasticsearch/OpenSearch (the ops tax being escaped; kNN+BM25 no longer a differentiator); external embedding APIs (privacy + model-version control); per-tenant fine-tuned models.

## 7. Verdict
**Ship: PostgreSQL backbone** — pgvector (HNSW+iterative or exact for small tenants) + bundled BM25 ext (pg_search for maturity; VectorChord-bm25 if AGPL blocks), hash-partitioned failure_vectors, RRF in SQL, Drain3+stacktrace signature docs, int8-ONNX nomic-v2-class 384-512d. **Escape hatch: Qdrant** behind a thin RetrievalStore interface. **LanceDB/Parquet+DuckDB: cold tier only.** The decisive argument: every architecture splitting labels from vectors buys a synchronization bug factory; every added container buys support tickets from a thousand self-hosted installs.

Sources: pgvector 0.8.0 release/AWS deep-dive, Tiger Data pgvector-vs-Qdrant, VectorChord comparative + bm25/pg_tokenizer, ParadeDB hybrid manual, Qdrant hybrid/1.16, Milvus 2.5 FTS, Weaviate platform, LanceDB hybrid docs + issue #2511, DuckDB VSS docs, Qdrant-vs-Chroma 2026, Drain3, AI-assisted flaky-triage pipeline (2025), Mixpeek/PromptQuorum embedding roundups.
