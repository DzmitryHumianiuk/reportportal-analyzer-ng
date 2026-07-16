# ReportPortal service-auto-analyzer — Architecture Analysis

> Round 1 code analysis. Agent: architecture & storage.

## 1. How the service fits into ReportPortal & how it receives requests

**Role.** ReportPortal's ML/search microservice ("Auto-analysis service for ReportPortal", `README.md:1`). The RP main backend (Java) owns the source of truth (test items, launches, logs) in **PostgreSQL** and forwards work over **RabbitMQ**. The analyzer indexes a subset of log data into OpenSearch/ES and runs ML models to predict defect types and suggest relevant historical items.

**Transport = AMQP/RabbitMQ; Flask only for healthcheck.**
- Flask app created in `app/main.py:252-257`; only route `GET /` health status (`:384-417`), port 5001 (`:426-429`, `ANALYZER_HTTP_PORT`). Reports OpenSearch health + per-thread running tasks.
- RabbitMQ consumers: `app/main.py:277-351` (`init_amqp_queues`) starts two consumer threads via `AmqpClient.receive`:
  - Queue **`all`** → main handler, every routing key except `train_models` (`except_train`, `:267-269`).
  - Queue **`train`** → only `train_models` (`only_train`, `:272-274`).
- AMQP client: `app/amqp/amqp.py`. Exponential-backoff connect (`_connect_with_retry`, `:96-117`), exchange declare (`_do_declare_exchange`, `:129-146`), `prefetch_count=1` (`:218`). Exchange default `analyzer`, type `fanout` (`launch_objects.py:69-70`). Declared with capability args: `analyzer_index`, `analyzer_priority`, `analyzer_log_search`, `analyzer_suggest`, `analyzer_cluster` (`amqp.py:136-144`) — backend discovery mechanism.
- RPC-style replies: published to `props.reply_to` with original `correlation_id` (`amqp.py:289-306`; handlers `amqp_handler.py:310-321`, `:610-618`).

**Message routes (routing key → handler)** — defined in `ServiceProcessor.__configs` (`app/service/processor.py:151-267`):

| Routing key | Handler | Purpose |
|---|---|---|
| `index` | `IndexService.index_logs` | Index launches/items+logs into ES (`processor.py:156-160`) |
| `analyze` | `AutoAnalyzerService.analyze_logs` | Auto-analysis (`:166-170`) |
| `suggest` | `SuggestService.suggest_items` | "Make Decision" suggestions (`:206-210`) |
| `cluster` | `ClusterService.find_clusters` | Group similar logs (`:211-215`) |
| `search` | `SearchService.search_logs` | Full-text log search (`:201-205`) |
| `clean` | `CleanIndexService.delete_logs` | Remove logs from index (`:176-180`) |
| `delete` | `CleanIndexService.delete_index` | Delete project index (`:171-175`) |
| `train_models` | `RetrainingService.train_models` | Retrain per-project models (`:152-155`) |
| `defect_update`, `item_remove`, `launch_remove`, `remove_by_launch_start_time`, `remove_by_log_time` | CleanIndex/Index services | Maintenance (`:161-200`) |
| `namespace_finder`, `suggest_patterns` | NamespaceFinder / SuggestPatterns | (`:216-224`) |
| `index_suggest_info`, `remove_suggest_info`, `update_suggest_info`, `remove_models`, `get_model_info` | deprecated stubs, WARN (`:225-253`) |
| `noop_sleep`, `noop_echo`, `noop_fail` | test-only (`:254-266`) |

**"Only part of logs go to ES" — verified.** `ERROR_LOGGING_LEVEL = 40000` (`launch_objects.py:24`), `AnalyzerConf.minimumLogLevel` defaults to it (`:44`). `IndexService.index_logs` → `prepare_test_items` (`index_service.py:91-96`) filters `log.logLevel >= minimal_log_level` (`request_factory.py:179`), caps to `numberOfLogsToIndex` (20, `launch_objects.py:43`), drops near-dupes above 0.95 (`request_factory.py:181-185`). PostgreSQL (main app) holds all logs; analyzer indexes only a deduplicated subset of ERROR-level logs per test item. Indexed doc is Test-Item-centric with nested logs (`request_factory.py:151-207`).

## 2. External infrastructure dependencies

| Dependency | Required? | Evidence |
|---|---|---|
| OpenSearch/ES | **Required** — core datastore | `OsClient` at startup (`main.py:377`); health gate (`:388`); `opensearch-py==3.1.0`; `ES_HOSTS` default `http://opensearch:9200` (`main.py:180`) |
| RabbitMQ | **Required** — sole transport | consumer threads (`main.py:321-351`); `pika==1.2.1`; `AMQP_URL` |
| Object store (one of three) | **Required**, pluggable | `DATASTORE_TYPE` ∈ {`filesystem` (default), `minio`, `s3`} (`main.py:52-73`) |
| PostgreSQL | **Not a dependency of this service** | no PG client in requirements/code; PG is the main backend's |
| NLTK data | build-time asset | `Dockerfile:39` |

## 3. ML model & artifact persistence (object_saving abstraction)

`ObjectSaver` delegates to a `Storage` impl chosen by `DATASTORE_TYPE` (`object_saver.py:40-63`): `STORAGE_FACTORIES = {"minio": MinioClient, "filesystem": FilesystemSaver, "s3": Boto3Client}`.
- `Storage` interface (`storage.py:26-61`): `put/get_project_object`, `does_object_exists`, `get_folder_objects`, `remove_folder_objects`, `remove_project_objects`. Namespaced `bucket_prefix + project_id + bucket_postfix` (default prefix `prj-`).
- `BlobStorage` (`blob_storage.py`): single-bucket vs bucket-per-project (`:35-69`); **pickle** default, JSON with `using_json=True` (`:71-99`).
- `MinioClient` (`minio_client.py:34-62`), `Boto3Client` (IAM support, `boto3_client.py:47-102`), `FilesystemSaver` (`filesystem_saver.py:45-86`).
- `ModelChooser`: global models always from **filesystem** (`model_chooser.py:76`, folders from `model_settings.json` via `main.py:363-371`); custom per-project from configured datastore at `{model_type}_model/` (`:95-100`). Legacy env vars (`ANALYZER_BINSTORE_*`, `MINIO_*`) mapped with deprecation warnings (`main.py:52-174`).

## 4. Scale / performance profile

- **Process isolation**: each AMQP handler runs ML in a separate OS process (`ProcessAmqpRequestHandler` → `RealProcessor`/`Worker`, `amqp_handler.py:196-198`), 3 helper threads (`:203-215`). Debug = in-thread `DirectAmqpRequestHandler`.
- **Backpressure**: `PriorityQueue(maxsize=100)` (`:192`), `NUMBER_OF_TASKS_TO_SEND = 2` in flight (`:35, 436`), `prefetch_count=1`. Priority from `timestamp_in_ms` header.
- **Fault tolerance**: `AMQP_HANDLER_TASK_TIMEOUT` 600s → processor restart; retries up to `AMQP_HANDLER_MAX_RETRIES` 3 (`:295-308, 360-400`).
- **ES bulk**: `ES_CHUNK_NUMBER` 1000, `ES_CHUNK_NUMBER_UPDATE_CLUSTERS` 500 (`os_client.py:249, 373, 453, 619, 645`). AWS 10/100MB warnings in README.
- **Per-project indices**: index name = project id + optional `ES_PROJECT_INDEX_PREFIX` (`os_client.py:39-47`). Shared indices: `rp_aa_stats, rp_stats, rp_model_train_stats, rp_done_tasks, rp_suggestions_info_metrics`. `number_of_shards: 1`.
- **Caps**: `ANALYZER_MAX_ITEMS_TO_PROCESS` 4000/request (`auto_analyzer_service.py:289-293`); `MAX_LOGS_FOR_DEFECT_TYPE_MODEL` 10000 (1GB train image); `numberOfLogsToIndex` 20.
- **Scaling**: multiple analyzers register on fanout exchange with `ANALYZER_PRIORITY`.

## 5. Config / env vars

**ES connection** (`ApplicationConfig`, `main.py:177-224`; applied `os_client.py:79-105`): `ES_HOSTS`, `ES_USER`, `ES_PASSWORD`, `ES_USE_SSL`, `ES_VERIFY_CERTS`, `ES_SSL_SHOW_WARN`, `ES_CA_CERT`, `ES_CLIENT_CERT`, `ES_CLIENT_KEY`, `ES_TURN_OFF_SSL_VERIFICATION`, `ES_CHUNK_NUMBER*`, `ES_PROJECT_INDEX_PREFIX`. Client `timeout=30, max_retries=5, retry_on_timeout=True`.

**Capability modes**: `ANALYZER_INDEX`, `ANALYZER_LOG_SEARCH`, `ANALYZER_SUGGEST`, `ANALYZER_CLUSTER`, `ANALYZER_PRIORITY` (`main.py:202-206`).

**Search/boost/thresholds** (`SearchConfig`, `main.py:226-249`): `ES_MIN_SHOULD_MATCH` (80%), `ES_BOOST_AA` (0.0), `ES_BOOST_MA` (10.0), `ES_BOOST_LAUNCH` (4.0), `ES_BOOST_TEST_CASE_HASH` (8.0), `ES_MAX_QUERY_TERMS` (50), `ES_MIN_WORD_LENGTH` (2), `ES_TIME_WEIGHT_DECAY` (0.999), `PATTERN_*`, `PROB_CUSTOM_MODEL_SUGGESTIONS` (≤0.8), `PROB_CUSTOM_MODEL_AUTO_ANALYSIS` (≤1.0), `ML_MODEL_FOR_SUGGESTIONS` ∈ {suggestion, auto_analysis, similarity}, `MAX_SUGGESTIONS_NUMBER` (3).

## 6. Request/response contract — `analyze` and `suggest`

### `analyze`
- **Input**: JSON array of launches → `list[Launch]` (`processor.py:167-168`). `Launch` (`launch_objects.py:194-205`): `launchId`, `project`, `analyzerConfig` (`AnalyzerConf`: `analyzerMode`, `minShouldMatch`, `numberOfLogLines`, ... `:32-46`), `testItems[]` with `logs[]` (`:158-174`).
- **Processing**: per failed item ERROR logs prepared (`prepare_request_logs_for_launch`, `auto_analyzer_service.py:76-101`), nested MLT query with boosts (`_build_nested_analyze_query`, `:155-248`), `msearch` (`:272`), `AutoAnalysisPredictor` predicts, rank (`:356-423`).
- **Output**: `list[AnalysisResult]` (`launch_objects.py:229-234`) = `{testItem: int, issueType: str, relevantItem: int}` (`to_analysis_result`, `:104-111`).

### `suggest`
- **Input**: `TestItemInfo` (`launch_objects.py:177-191`): `testItemId`, `launchId`, `project`, `testCaseHash`, `clusterId`, `analyzerConfig`, `logs[]`.
- **Processing**: `_query_suggested_items` (`suggest_service.py:478`), predictor by `ML_MODEL_FOR_SUGGESTIONS` (`:489`), custom-vs-global by probability (`:495`), grouped/ranked, deduped, `MaxSuggestionsNumber`, `suggest_threshold` 0.4 (`:502-529`).
- **Output**: `list[SuggestAnalysisResult]` (`launch_objects.py:252-277`, built `suggest_service.py:539-562`): `testItem`, `relevantItem`, `relevantLogId`, `issueType`, `matchScore` (×100), `esScore`, `esPosition`, `resultPosition`, `modelInfo`/`modelFeatureNames`/`modelFeatureValues`, `usedLogLines`, `minShouldMatch`, `clusterId`, `methodName="suggestion"`.

**Key source files**: `app/main.py`, `app/amqp/*`, `app/service/processor.py`, `app/service/auto_analyzer_service.py`, `app/service/suggest_service.py`, `app/service/index_service.py`, `app/service/analyzer_service.py`, `app/service/retraining_service.py`, `app/commons/os_client.py`, `app/commons/model_chooser.py`, `app/commons/request_factory.py`, `app/commons/object_saving/*`, `app/commons/model/launch_objects.py`, `res/index_settings.json`.
