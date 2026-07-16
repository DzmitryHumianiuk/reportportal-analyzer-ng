# OpenSearch/ElasticSearch Data & Query Layer — service-auto-analyzer

> Round 1 code analysis. Agent: ES/OpenSearch layer. Source: cloned repo (develop, July 2026).

Note on naming: the module `app/utils/os_migration.py` is misleadingly named. Despite "migration," it holds the *active* shared query/adapter layer (`construct_analysis_query`, `extract_inner_hit_logs`, `bucket_sort_logs_by_similarity`, `convert_test_item_log`) imported by both `auto_analyzer_service.py:42` and `suggest_service.py:43`. The "migration" refers to the architectural move from a **log-centric** index to a **Test Item-centric** index.

---

## 1. What EXACTLY is stored in OpenSearch

### Document granularity: one document per Test Item (not per log line)

The index stores **one document per Test Item**, with all its log lines held as a `nested` array. See `TestItemIndexData` (`app/commons/model/test_item_index.py:119-149`) and the doc `_id` = `test_item_id` (`app/commons/os_client.py:327`). Log lines are the nested `LogData` objects (`test_item_index.py:21-70`), stored under the `logs` field (`test_item_index_mappings.json:43-159`). This is a deliberate change from the older log-centric `LogItemIndexData` model (`app/commons/model/log_item_index.py`), which now only survives as an in-memory adapter type for the ML featurizers.

### Index mapping fields (`res/test_item_index_mappings.json`)

Top-level (Test Item) fields:
- `keyword`: `test_item_id` (:3), `unique_id` (:10), `test_case_hash` (:13 — keyword, not numeric), `launch_id` (:16), `launch_number` (:23), `issue_type` (:37)
- `text` + `standard_english_analyzer`: `test_item_name` (:6), `launch_name` (:19)
- `date` (format `yyyy-MM-dd HH:mm:ss||yyyy-MM-dd`): `launch_start_time` (:26), `start_time` (:30)
- `integer`: `log_count` (:34)
- `boolean`: `is_auto_analyzed` (:40)
- `nested`: `logs` (:43), `issue_history` (:160)

Nested `logs.*` fields (:45-158):
- `keyword`: `log_id`, `cluster_id`, `potential_status_codes`
- `integer`: `log_order`, `log_level`, `message_lines`, `message_words_number`
- `date`: `log_time`
- `boolean`: `cluster_with_numbers`
- `text` + `standard_english_analyzer` (analyzed, searchable): `cluster_message`, `message`, `message_extended`, `message_without_params_extended`, `message_without_params_and_brackets`, `detected_message`, `detected_message_extended`, `detected_message_without_params_extended`, `detected_message_without_params_and_brackets`, `stacktrace`, `stacktrace_extended`, `only_numbers`, `found_exceptions`, `found_exceptions_extended`, `found_tests_and_methods`, `urls`, `paths`, `message_params`, `whole_message`
- `text` with `"index": false` (stored, NOT searchable): `original_message` (:69-72), `detected_message_with_numbers` (:99-102)

Nested `issue_history.*` (:160-178): `is_auto_analyzed` (boolean), `issue_type` (keyword), `timestamp` (date), `issue_comment` (text, `index:false`).

### No dense_vector / kNN

There are **no `dense_vector`, `knn`, or vector fields** in the mapping. Vectorization (TF-IDF, cosine) happens **outside ES** in Python (`app/commons/clustering.py`, `sklearn.metrics.pairwise.cosine_similarity` at clustering.py:23,74; `text_processing.calculate_text_similarity`). OpenSearch stores only inverted-index text/keyword data.

### Why "only PART of logs" go to ES

Confirmed. Filtering happens at prepare time in `request_factory.prepare_test_items` (`app/commons/request_factory.py:151-207`):
1. **Log-level filter** (`:179`): `logs = [log for log in test_item.logs if log.logLevel >= minimal_log_level]` — default `minimal_log_level = ERROR_LOGGING_LEVEL` = 40000 (ERROR). So DEBUG/INFO/WARN logs are dropped entirely. Default confirmed at `request_factory.py:154` and `launch_objects.py:44`.
2. **Near-duplicate dropping** (`:182-183`): `text_processing.find_last_unique_texts(similarity_threshold_to_drop, ...)` drops textually-similar log lines (default `similarityThresholdToDrop = 0.95`, `launch_objects.py:45`).
3. **Cap on count** (`:185`): `logs_to_take[-number_of_logs_to_index:]` — keeps at most `numberOfLogsToIndex` (default 20, `launch_objects.py:43`) most-recent lines.
4. Per-line the message itself is also truncated to `numberOfLogLines` lines during `_prepare_log_data` (`:186`; default `numberOfLogLines = -1` meaning all lines).

So ES holds: error-level, de-duplicated, count-capped, preprocessed logs plus derived/enriched fields — not raw full logs. Raw text is kept only in the non-indexed `original_message`. The bulk of raw log volume lives in ReportPortal's primary store, not ES.

---

## 2. OpenSearch query features relied upon

### more_like_this (MLT) — the core retrieval primitive
`app/utils/utils.py:239-257`:
```python
{"more_like_this": {
    "fields": [field_name], "like": log_message,
    "min_doc_freq": 1, "min_term_freq": 1,
    "minimum_should_match": override_min_should_match or "5<" + min_should_match,
    "max_query_terms": max_query_terms, "boost": boost}}
```
Used everywhere: search (`search_service.py:76`), auto-analysis (`auto_analyzer_service.py:171,184,196,210,224`), suggestions (`suggest_service.py:280,293,307,322`), clustering (`cluster_service.py:317,328,341`), and test-item field boost (`os_migration.py:266`). The `"5<" + min_should_match` syntax means "if >5 terms, require min_should_match%".

### bool / should / must / filter / must_not
Pervasive. Representative from `search_service.py:86-118`:
```python
"query": {"bool": {
    "filter": [{"exists": {"field": "issue_type"}},
               {"terms": {"launch_id": search_req.filteredLaunchIds}}],
    "must_not": [{"term": {"test_item_id": str(search_req.itemId)}}],
    "must": [{"bool": {"should": [{"wildcard": {"issue_type": {"value": "ti*", "case_insensitive": True}}}]}},
             {"nested": {"path": "logs", "score_mode": ..., "query": {"bool": {
                 "filter": [{"range": {"logs.log_level": {"gte": min_log_level}}}],
                 "should": nested_should}}}}]}}
```
Analysis query structure in `os_migration.py:276-288` (bool with filter/must_not/must/should).

### nested queries + inner_hits
All log matching is `nested` on path `logs` (`search_service.py:104`, `os_migration.py:236`, `cluster_service.py:352`). `inner_hits` (`os_migration.py:240-243`, size 5) return the matched nested logs; extracted in `extract_inner_hit_logs` (`os_migration.py:158-182`). `score_mode` configurable (`avg` default, `launch_objects.py:46`; `max` in clustering `cluster_service.py:354`).

### term / terms
`terms`: `launch_id` filter (`search_service.py:92`), delete-by-id (`os_client.py:410,435,620,706`). `term` with boost: `test_case_hash` (`os_migration.py:251-258`), `is_auto_analyzed` (`utils.py:294-300`), `launch_name`/`launch_id` (`analyzer_service.py:29,34,52,57,62`).

### wildcard (case-insensitive)
Issue-type restriction: `{"wildcard": {"issue_type": {"value": "ti*", "case_insensitive": True}}}` and `"nd*"` (`utils.py:436-438`, `search_service.py:99`).

### range
`logs.log_level >= min` (`search_service.py:109`, `cluster_service.py:358`), time-range deletes (`os_client.py:451,759`).

### exists
`{"exists": {"field": "issue_type"}}` (`search_service.py:91`, `os_migration.py:282`).

### function_score + decay (exp) + script_score + boost_mode
Time-decay re-scoring wraps the main query. `analyzer_service.py:122-146`:
```python
"function_score": {"query": main_query["query"], "functions": [
    {"exp": {"start_time": {"origin": start_time, "scale": "7d", "offset": "1d",
             "decay": self.search_cfg.TimeWeightDecay}}},
    {"script_score": {"script": {"source": "0.6"}}}],
    "score_mode": "max", "boost_mode": "multiply"}
```
Same pattern in clustering with constant `0.2` (`cluster_service.py:288-306`). `script_score` here is only a constant-floor script, not a full custom scoring script.

### min_should_match / minimum_should_match
Set via `text_processing.prepare_es_min_should_match` (`text_processing.py:776`) and threaded into MLT. Config: `MinShouldMatch="80%"` (`launch_objects.py:101`), `searchLogsMinShouldMatch=95` (`:41`).

### Boosting
Field-level boosts on MLT (`boost=4.0` messages, `8.0` for `found_exceptions`/`potential_status_codes`, `2.0` stacktrace — `auto_analyzer_service.py:176,182,199,213`). AA/MA term boosts (`utils.py:282-312`), launch boosts (`analyzer_service.py`), test-item-name boost 2.0 (`os_migration.py:27-29`).

### sort
`"sort": ["_score", {"start_time": "desc"}]` (`os_migration.py:278`).

### Scripted updates (Painless)
`bulk_update_issue_history` appends to `issue_history` array (`os_client.py:543-550`); `bulk_update_cluster_info` mutates nested `logs[i]` cluster fields (`os_client.py:581-591`).

### NOT used
No `highlight` (field exists on the `Hit` model at `db.py:31` but is never populated by a query), no `aggregations`/`aggs`, no explicit `fuzziness`/`fuzzy`, no `match`/`match_phrase` (MLT is used instead of `match`).

### API surface
`scan` helper for scrolling (`os_client.py:494`), `msearch` for batched multi-query analysis (`os_client.py:511`, driven from `auto_analyzer_service.py:272`), `delete_by_query` (`os_client.py:383`), `bulk` (`os_client.py:255`).

---

## 3. Analyzers / tokenizers / scoring

### Custom analyzer — the only one
`res/index_settings.json`:
```json
{"number_of_shards": 1,
 "analysis": {"analyzer": {"standard_english_analyzer": {
     "type": "standard", "stopwords": "_english_"}}}}
```
It is the built-in **standard tokenizer** with the **English stop-word list** applied. That is the entirety of the index-side text analysis: **no custom char filters, no n-gram/edge_ngram, no stemming, no synonyms, no pattern_replace, no whitespace analyzer.** All text `type:text` fields use this analyzer (mappings :7, :21, :64, :75, and all `logs.*` analyzed fields).

Note: n-gram/stemming-style processing DOES happen, but **in Python before indexing**, not in ES — e.g. n-gram hashing in `clustering.py:37-43`, `TfidfVectorizer(ngram_range=(1,2))` in `text_processing.py:955`, and the many `*_extended` / `*_without_params` derived fields produced by `request_factory`. ES just stores those already-processed strings.

### Role of BM25/TF-IDF scoring
OpenSearch's default **BM25** similarity drives candidate retrieval: MLT selects high-TF-IDF terms (`min_doc_freq`/`min_term_freq`/`max_query_terms`) and the bool/nested `should` clauses accumulate BM25 scores per matched log field. This raw `_score` is captured on `Hit.score` (`db.py:27`) and then **re-normalized in Python** (`boosting_featurizer.normalize_results`, `boosting_featurizer.py:663-682`): `hit.normalized_score = hit_score / max_score`. No custom similarity module is registered — stock BM25 is used.

---

## 4. Retrieval → ranking pipeline

Two-stage: **ES candidate generation → external ML (XGBoost-style boosting) re-rank.**

**Stage 1 — ES candidate generation (top-K):**
- Per request log, build nested MLT query (`auto_analyzer_service._build_nested_analyze_query`, :155-248), wrap with AA/MA boosts, launch constraints, and `function_score` time decay.
- `size` per query is 10 (`auto_analyzer_service.py:159`); overall search `size = esChunkNumber` (1000) in `search_service.py:87`.
- All per-log queries batched via `msearch` (`auto_analyzer_service.py:272`), deduped by `test_item_id` (`:270-277`).
- `inner_hits` (size 5) yields the matched nested logs; `extract_inner_hit_logs` flattens them back to log-centric `LogItemIndexData` (`os_migration.py:158-182`).
- `bucket_sort_logs_by_similarity` (`os_migration.py:198-223`) re-aligns each found log to its most-similar request log via Python cosine similarity.

**Stage 2 — ML re-ranking:**
- `AutoAnalysisPredictor.predict(candidates)` (`auto_analyzer_service.py:396`) runs the boosting model over features from `BoostingFeaturizer` (`app/ml/boosting_featurizer.py`).
- Positive predictions → `group_predictions_by_test_item` → `score_and_rank_test_items` (weighted central score, `utils.py:392-432`) → top result chosen (`auto_analyzer_service.py:415-423`).

**Where the ES score is used as a feature:** The normalized BM25 `_score` is a direct model feature. Feature registry (`boosting_featurizer.py:101-145`):
- feature 0: `_calculate_score` — normalized ES score summed per issue type (`:468-475`, uses `hit.normalized_score/total_normalized_score` from `find_most_relevant_by_type` :249)
- features 3/5/26/27/28: max/min/mean of `hit.normalized_score` and their positions (`:600-656`)
- feature 64: `_calculate_decay_function_score` (`:312-331`), a Python re-implementation of the ES exp-decay on `start_time`.

So the ES score feeds the ML model twice: implicitly (which docs are retrieved / their rank order) and explicitly (normalized score as numeric features).

---

## 5. Index lifecycle

### Naming — one index per project
`get_test_item_index_name(project_id, prefix)` → `f"{prefix}{project_id}"` (`os_client.py:39-47`). Prefix from `esProjectIndexPrefix` (default `""`, `launch_objects.py:63`). So indices are named per project id, e.g. `1`, `2` (or `<prefix>1`). A legacy `<index>_suggest` index is cleaned up if present (`os_client.py:153-155`).

### Creation
Lazy/auto-create on first write. `_ensure_index_exists` (`os_client.py:191-215`) caches checked indices in `_checked_indexes` and calls `_create_index` (`:158-174`), which loads `res/index_settings.json` + `res/test_item_index_mappings.json`. Triggered by `bulk_index_raw` (`:307-310`).

### Cleaning / deletion (`CleanIndexService` → `OsClient`)
- Delete whole index for a project: `delete_index` (`os_client.py:144-156`) + namespace/trigger/model cleanup (`clean_index_service.py:115-123`).
- Delete test items / launches: `delete_by_query` with `terms` on `test_item_id` / `launch_id` (`os_client.py:390-438`), batched by `esChunkNumber`.
- Delete logs by id / time range: because logs are nested, it can't `delete_by_query` a log; instead it fetches candidate Test Items (`nested` query), removes matching nested logs in Python, then bulk-updates the doc (or deletes the Test Item if it becomes empty) — `_remove_logs_with_predicate` (`os_client.py:628-688`), `delete_logs_by_ids` (:690-711), `delete_by_log_time_range` (:734-764).
- Time-range launch delete: `delete_by_query` on `range` of `launch_start_time` (`os_client.py:456-474`).

### Migration
No ES `_reindex`. The "migration" is the code-level model change (log-centric → Test-Item-centric); `os_migration.py` provides the adapter (`convert_test_item_log`, :74-128) so ML featurizers still see the old `LogItemIndexData` shape. `res/model_settings.json` is ML model config, unrelated to index settings.

### Read-only recovery
On `TransportError` during bulk (disk-watermark read-only block), it PUTs `index.blocks.read_only_allow_delete: null` on `_all/_settings` and retries (`os_client.py:217-229, 262-272`).

### ES-specific features that would be hard to replicate
- **`more_like_this`** — the entire candidate-retrieval mechanism depends on it (term selection + `minimum_should_match` "5<..." syntax).
- **`nested` documents + `inner_hits`** — logs are nested; retrieval and per-log extraction depend on nested scoring (`score_mode`) and inner_hits.
- **`function_score` with Gauss/exp `decay`** (`exp` on dates) + `boost_mode:multiply` / `score_mode:max`.
- **Painless scripted updates** for nested-array mutation (`issue_history.add`, in-place `logs[i]` cluster updates) — `os_client.py:543-591`.
- **BM25 relevance `_score`** consumed as an ML feature.
- Case-insensitive `wildcard`.

### Features NOT used (easy portability, and confirms absence)
No **percolator**, no **ILM/lifecycle policy**, no custom **routing** (`processor.py` "routing" is AMQP message routing, unrelated), no custom **similarity** module, no **dense_vector/kNN**, no aggregations, single shard (`index_settings.json:2`), no replica/alias configuration in code.
