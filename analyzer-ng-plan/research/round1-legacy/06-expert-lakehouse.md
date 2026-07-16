# Replacing OpenSearch with Parquet + Iceberg + DuckDB — adversarial architecture review

> Round 1 expert panel. Role: senior data-lakehouse / analytics-engine architect.
> Verdict: **2/5 for online retrieval, 5/5 for training/analytics.**

Verdict up front: this stack is a strong fit for the **cold/training/analytics** half of the workload and a **poor fit for the hot online retrieve-and-rank path**. The mismatch is not a tuning problem; it is OLAP-columnar-immutable machinery being asked to do OLTP-ish incremental writes plus low-latency search.

## Design sketch (explicit hot vs cold split)

**COLD path (lakehouse — genuinely good):** all error-log docs in Iceberg/Parquet (or DuckLake) as durable cheap long-retention store; DuckDB in the existing Python processes for offline training (labeled `issue_history`), batch re-embedding, metrics/analytics; scheduled compaction/expire-snapshots.

**HOT path (online analyze/suggest/search — must NOT be pure Iceberg):** either keep an inverted-index engine for Stage-1, or maintain a **per-project DuckDB database file** (not raw Iceberg) with a materialized hot window + FTS (+HNSW) index, refreshed incrementally, single writer. That is rebuilding a search service on DuckDB primitives.

**Critical realization: DuckDB's FTS and VSS indexes cannot live on Iceberg/Parquet files.** They are stored inside a DuckDB database file; you cannot build an FTS index over external Parquet ([discussion #4820](https://github.com/duckdb/duckdb/discussions/4820), [FTS docs](https://duckdb.org/docs/current/core_extensions/full_text_search)). "Search on Iceberg" always means "copy the searchable subset into a DuckDB table and index that."

## 1. Candidate generation on DuckDB

**FTS/BM25 — mostly real, with gaps:**
- `match_bm25` is genuine Okapi BM25 with tunable k/b; `create_fts_index` supports multiple fields with field-scoped querying.
- **No per-field boosts** — approximate via per-field `match_bm25` calls weighted in SQL = reimplementing the retriever.
- **Index is a static snapshot**: full rebuild via `overwrite := 1`, or `incremental := true` (trigger-maintained, needs PK, storage ≥ v2.0.0) — far less battle-tested than Lucene ([#3543](https://github.com/duckdb/duckdb/issues/3543)).
- Emulating `more_like_this`: manual salient-term extraction + OR query + minimum_should_match in SQL — a reimplementation whose correctness parity is on you.

**VSS/HNSW — least mature:**
- Persistence **experimental** behind `hnsw_enable_experimental_persistence`; WAL recovery unimplemented; crash can corrupt ([VSS docs](https://duckdb.org/docs/current/core_extensions/vss)).
- Whole index re-serialized on checkpoint; deletes tombstone until `PRAGMA hnsw_compact_index`.
- **Filtered-search recall problem**: WHERE applied *after* HNSW → per-project filters can return <K or degrade recall (ACORN fork exists, not core).

## 2. The hard part — write/update/latency mismatch

Workload = many small incremental writes + frequent row-level DELETEs + in-place UPDATEs (issue_history append, cluster assignment). Near worst-case for Iceberg:
- **Small-file explosion** from streaming/micro-batch commits; mandatory compaction ([AWS](https://docs.aws.amazon.com/prescriptive-guidance/latest/apache-iceberg-on-aws/best-practices-write.html)).
- **Delete/update cost**: CoW rewrites whole files; MoR accumulates delete files readers must merge ([Dremio](https://www.dremio.com/blog/compaction-in-apache-iceberg-fine-tuning-your-iceberg-tables-data-files/)).
- **Metadata/snapshot growth** per commit; expire_snapshots required.
- **DuckDB Iceberg writes are new and MoR**: INSERT in 1.4 LTS (Sept 2025), DELETE/UPDATE in 1.4.2 via positional deletes, REST catalog required ([Iceberg writes](https://duckdb.org/2025/11/28/iceberg-writes-in-duckdb)).
- **Quantified worst case**: [duckdb-iceberg #834](https://github.com/duckdb/duckdb-iceberg/issues/834) — ~7-minute SELECTs after a full-table UPDATE.
- **Cold online latency**: catalog attach + metadata.json + manifests + data/delete files + FTS load will not reliably hit <1s; extension does full scans without partition pruning for large tables ([OLake](https://olake.io/iceberg/query-engine/duckdb/)).

Conclusion: **an OLAP/batch stack asked to do OLTP+search.** Iceberg-direct online serving is not viable at <1s under these write patterns.

## 3. Concurrency

- DuckDB is **single-writer / one-writer-many-readers across processes** ([concurrency docs](https://duckdb.org/docs/current/connect/concurrency)). Multiple ML workers reading a shared file while an indexer writes = exactly what it doesn't natively allow.
- Forces a service boundary or catalog: Iceberg REST catalog (an always-on service — recreating the ops burden), **DuckLake with a PostgreSQL catalog** (concurrent read-writes via central SQL catalog; attractive since PG already runs), or Quack remote protocol (beta as of v1.5.2 — too green).

## 4. Where this stack IS genuinely good

- **Offline model training** (issue_history label pulls) — DuckDB's sweet spot. 5/5.
- **Analytics/metrics** (rp_stats, drift monitoring).
- **Cheap long-term retention** (Parquet on object storage).
- **Batch re-embedding**.
- **DuckLake data inlining** mitigates small-file/streaming pain by storing small changes in the catalog DB ([data inlining](https://ducklake.select/2026/04/02/data-inlining-in-ducklake/), DuckLake 1.0 2026).

Recommended hybrid: lakehouse = cold + training + analytics + re-embedding; hot online retrieval = search-shaped engine.

## 5. Freshness

- Iceberg visibility only on snapshot commit; well-performing only after compaction. No clean "index item, query it 200ms later" semantics.
- Derived FTS/HNSW adds a second freshness lag (ingest-commit + refresh-interval + index-update).
- ES gives NRT (~1s) as first-class — exactly what the analyzer assumes.
- DuckLake inlining is the closest this family gets to NRT ingest.

## Summary table

| Requirement | ES today | Iceberg-direct DuckDB | Materialized DuckDB hot DB |
|---|---|---|---|
| <1s online analyze/suggest | Yes | No | Plausible for small hot set |
| NRT freshness (~1s) | Yes | No | Maybe (incremental FTS + inlining) |
| Frequent row DELETE/UPDATE | Native | Delete-file churn / CoW | Single-writer, OK at low volume |
| Per-field boosted MLT | Native | Manual reimplementation | Manual reimplementation |
| Vector search on hot path | (plugin) | Experimental persistence, filter-recall risk | Same risk |

## Migration effort
Cold/analytics/training: **S–M** (embeds in existing processes; ETL + compaction jobs). Full hot-path replacement: **L** (reimplement boosted MLT, inner-hits, decay, BM25 features, incremental index pipeline, concurrency, freshness — building a search service). DuckLake adoption: **M**.

## Verdict
- **Online retrieval: 2/5.** No per-field boost, static/derived indexes off-Parquet, experimental vector persistence, single-writer concurrency, write/freshness profile conflicts with NRT + small updates/deletes.
- **Training/analytics: 5/5.**

**Bottom line:** don't frame as "replace OpenSearch with Parquet+Iceberg+DuckDB." Frame as "add a DuckDB lakehouse for cold/training/analytics/re-embedding — retiring most of ES's operational footprint — while keeping a search-shaped engine for the hot <1s path." If consolidating anyway, the most credible path is **DuckLake (Postgres catalog + inlining) for ingest + a per-project DuckDB search DB with incremental FTS** — real engineering, still weaker on boosted MLT and NRT than OpenSearch.

Sources: DuckDB FTS/VSS docs & issues (#3543, #4820), VSS blog, hnsw-acorn, AWS Iceberg write/compaction best practices, Dremio compaction, e6data metadata, DuckDB Iceberg writes blog/docs, duckdb-iceberg #834, OLake, DuckDB concurrency, Quack, DuckLake manifesto/inlining/1.0.
