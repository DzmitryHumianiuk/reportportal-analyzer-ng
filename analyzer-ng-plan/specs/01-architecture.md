# Spec 01 — analyzer-ng Service Architecture & ReportPortal Integration

Status: authoritative. Derived from `CONTEXT.md` and verified against the legacy
`reportportal/service-auto-analyzer` source (read-only clone at
`/private/tmp/claude-501/-Users-Dmitriy-Gumeniuk-scripts/73236fa0-35ae-4721-b518-a2ce62e95c78/scratchpad/service-auto-analyzer`).
All AMQP field names, exchange arguments, and payload schemas below are copied verbatim
from that code; **do not rename any wire-visible field** — the ReportPortal backend and UI
depend on them.

---

## 1. Service overview

analyzer-ng is a single-container Python 3.12 microservice. It is a **drop-in AMQP
replacement** for the legacy `analyzer` + `analyzer-train` containers and removes the
`opensearch` dependency entirely. All state lives in PostgreSQL (own database `analyzer`,
schema `analyzer`, pgvector-enabled). An optional Ollama sidecar provides LLM roles
(spec 04); the service is fully functional with LLM off.

### 1.1 Process/thread model

One OS process, multiple threads (no multiprocessing — the legacy process-restart
machinery existed to contain OpenSearch client memory leaks; we do not inherit it):

| Component | Kind | Count | Role |
|---|---|---|---|
| main thread | thread | 1 | startup orchestration, signal handling, graceful shutdown |
| uvicorn health server | thread | 1 | FastAPI app on `ANALYZER_HTTP_PORT` (`/`, `/health`, `/metrics`) |
| AMQP consumer `all` | thread | 1 | pika `BlockingConnection`, consumes queue `analyzer-ng.all`, enqueues to dispatcher |
| AMQP consumer `train` | thread | 1 | separate connection, consumes queue `analyzer-ng.train` (train_models only) |
| worker pool | `ThreadPoolExecutor` | `ANALYZER_NG_WORKERS` (default 2) | runs pipeline handlers (CPU: ONNX embed, LightGBM, SQL) |
| reply publisher | thread | 1 | owns a dedicated AMQP connection; drains an in-memory reply queue, publishes RPC replies |
| migration runner | main thread, startup only | — | ordered SQL migrations under PG advisory lock |
| Drain3/model warmup | main thread, startup only | — | ONNX session init + warmup inference |

pika `BlockingConnection` is **not thread-safe**; each consumer thread and the reply
publisher own their own connection. Acks are performed on the consumer's own
channel via `connection.add_callback_threadsafe` when a worker finishes.

### 1.2 Component diagram

```
                 RabbitMQ (vhost "analyzer")
                 exchange "analyzer" (fanout, args advertise capabilities)
   RP backend ---publish(routing_key=analyze, reply_to, correlation_id)--->
        |                                                     ^
        |                                                     | reply (default exchange,
        v                                                     |  routing_key=reply_to)
+---------------------------- analyzer-ng container ----------------------------+
|                                                                               |
|  +----------------+     +----------------+      +--------------------------+  |
|  | consumer "all" |     | consumer       |      | reply publisher thread   |  |
|  | thread (pika)  |     | "train" thread |      | (own AMQP connection)    |  |
|  +-------+--------+     +-------+--------+      +------------^-------------+  |
|          | ProcessingItem       |                            |                |
|          v                      v                            |                |
|  +---------------------------------------------+   reply(to, corr_id, json)  |
|  |  Dispatcher: routing-key table              |             |                |
|  |  (validate -> handler -> serialize)         |             |                |
|  +-------+-------------------------------------+             |                |
|          v                                                   |                |
|  +---------------------------------------------+             |                |
|  | ThreadPoolExecutor (N workers)              |-------------+                |
|  |  core/pipeline: preprocess -> Drain3 ->     |                              |
|  |  fingerprint -> retrieve -> GBM -> decide   |                              |
|  +---+--------------------+--------------------+                              |
|      |                    |                                                   |
|      v                    v                                                   |
|  +--------+        +-----------+        +-----------------------------+       |
|  | ml/    |        | db/       |        | api/ (FastAPI + uvicorn)    |       |
|  | ONNX   |        | psycopg3  |        |  GET /  GET /health         |       |
|  | e5     |        | pool ->   |        |  GET /metrics               |       |
|  | LightGBM        | PostgreSQL|        +-----------------------------+       |
|  +--------+        +-----+-----+                                              |
|                          |                        +---------------------+     |
|                          |                        | llm/ (optional)     |     |
|                          |                        | Ollama HTTP client  |     |
|                          |                        +---------------------+     |
+-------------------------------------------------------------------------------+
                           |
                           v
              PostgreSQL 16 + pgvector (database "analyzer", schema "analyzer")
```

### 1.3 End-to-end message flow (RPC request, e.g. `analyze`)

1. RP backend publishes JSON to exchange `analyzer` with routing key `analyze`,
   properties `reply_to=<amq.gen queue>`, `correlation_id=<uuid>`, optional header
   `timestamp_in_ms` (may carry a trailing `L`, e.g. `1749029201296L` — strip it).
2. Consumer thread receives from queue `analyzer-ng.all` (bound to the fanout exchange,
   so it receives *every* routing key), parses JSON (`json.loads(body, strict=False)`).
   Parse failure ⇒ `basic_nack(requeue=False)` + error log. Parse success ⇒ build a
   `ProcessingItem{routing_key, reply_to, correlation_id, priority, body}` and put it on
   the bounded in-memory priority queue (priority = `timestamp_in_ms` header or now).
3. A pool worker takes the item, looks up the routing-key table, validates the body into
   the pydantic request model, runs the handler, serializes the response model to JSON.
4. Worker pushes `(reply_to, correlation_id, json_body)` to the reply queue; the reply
   publisher publishes to the **default exchange** (`exchange=""`,
   `routing_key=reply_to`) with `BasicProperties(correlation_id=<original>,
   content_type="application/json")`. Handlers without a response (see §4.4) skip this.
5. Worker schedules `basic_ack(delivery_tag)` back on the consumer channel via
   `add_callback_threadsafe`. Ack semantics detailed in §8.3.

The suggest read path performs only query-signature embedding + SQL retrieval
synchronously (CONTEXT.md §2.9); heavy analysis is queue-driven.

---

## 2. Repository layout

New repository `analyzer-ng`, Apache-2.0, src layout:

```
analyzer-ng/
├── pyproject.toml               # PEP 621; uv or pip-tools lock committed
├── LICENSE                      # Apache-2.0
├── VERSION                      # single-line semver, baked into image, read at startup
├── Dockerfile
├── docker-compose.override.yml  # RP integration fragment (§7.2)
├── src/
│   └── analyzer_ng/
│       ├── __init__.py
│       ├── main.py              # entrypoint: startup sequence (§6), signal handling
│       ├── config.py            # AppConfig (pydantic-settings), env parsing, validation
│       ├── amqp/
│       │   ├── __init__.py
│       │   ├── client.py        # connection mgmt, retry/backoff, exchange declare, reply
│       │   ├── consumer.py      # queue bind, basic_consume, threadsafe ack scheduling
│       │   ├── dispatcher.py    # routing-key table, ProcessingItem, worker pool glue
│       │   └── models.py        # ALL wire models (§4.2) — verbatim legacy field names
│       ├── api/
│       │   ├── __init__.py
│       │   └── http.py          # FastAPI app: GET /, /health, /metrics
│       ├── core/                # pipeline (spec 03)
│       │   ├── __init__.py
│       │   ├── preprocess.py    # ported legacy text_processing subset
│       │   ├── templates.py     # Drain3 mining, template store glue
│       │   ├── fingerprint.py   # deterministic failure fingerprint
│       │   ├── signature.py     # failure signature document builder
│       │   ├── retrieval.py     # hybrid retrieval orchestration (RRF fusion)
│       │   ├── decision.py      # LightGBM decision layer + abstain
│       │   ├── clusterer.py     # launch-level grouping
│       │   └── handlers.py      # one function per routing key (§4.4 signatures)
│       ├── db/
│       │   ├── __init__.py
│       │   ├── pool.py          # psycopg_pool.ConnectionPool factory
│       │   ├── migrate.py       # advisory-lock migration runner
│       │   ├── migrations/      # 0001_init.sql, 0002_... (spec 02)
│       │   └── repositories/    # RetrievalStore, ItemRepo, KbRepo, LabelEventRepo, MetricsRepo
│       ├── ml/
│       │   ├── __init__.py
│       │   ├── embedder.py      # ONNX Runtime int8 e5-small wrapper, warmup
│       │   ├── gbm.py           # LightGBM train/predict, calibration
│       │   └── registry.py      # model version pinning/stamping
│       ├── llm/
│       │   ├── __init__.py
│       │   └── ollama.py        # feature-flagged client (spec 04)
│       └── seeds/
│           └── failure_modes.json  # seed KB ~40 generic modes (spec 03)
├── tests/
│   ├── unit/                    # pure-python: fingerprint golden files, model schemas
│   ├── integration/             # testcontainers: pgvector + rabbitmq
│   │   ├── test_amqp_contract.py
│   │   └── test_migrations.py
│   └── golden/                  # signature/fingerprint stability fixtures
└── models/                      # ONNX model + tokenizer, baked at build (§7.1)
```

### 2.1 Dependencies (pyproject, all Apache-2.0-compatible)

| Package | Version pin | License | Why |
|---|---|---|---|
| `pika` | `~=1.3` | BSD-3 | AMQP. **Chosen over aio-pika**: the legacy contract is blocking RPC over a fanout exchange; the workload is CPU-bound (ONNX/LightGBM), so asyncio buys nothing, and pika lets us port the proven legacy reconnect/declare/reply mechanics (§4.1) line-for-line. aio-pika (Apache-2.0) is fine license-wise but would force an async facade over synchronous CPU work. |
| `psycopg[binary,pool]` | `~=3.2` | LGPL-3 w/ linking exception (OK; if strict policy requires, swap `[binary]` for system libpq — interface identical) | PG driver + `psycopg_pool.ConnectionPool` |
| `pgvector` | `~=0.3` | MIT (client lib) | vector adapter for psycopg |
| `pydantic` | `~=2.9` | MIT | wire models, config |
| `pydantic-settings` | `~=2.5` | MIT | env-var config loading |
| `fastapi` | `~=0.115` | MIT | health/metrics HTTP |
| `uvicorn` | `~=0.32` | BSD-3 | HTTP server (single worker, thread mode) |
| `onnxruntime` | `~=1.19` | MIT | int8 embedding inference, CPU EP only |
| `tokenizers` | `~=0.20` | Apache-2.0 | HF tokenizer for e5-small (no `transformers` dependency) |
| `drain3` | `~=0.9` | MIT | log template mining |
| `lightgbm` | `~=4.5` | MIT | decision layer GBM |
| `numpy` | `~=2.1` | BSD-3 | vectors/features |
| `scikit-learn` | `~=1.5` | BSD-3 | isotonic calibration only |
| `tenacity` | `~=9.0` | Apache-2.0 | startup retries (PG, AMQP, Ollama probe) |
| `httpx` | `~=0.27` | BSD-3 | Ollama client |
| `prometheus-client` | `~=0.21` | Apache-2.0 | /metrics |
| dev: `pytest`, `pytest-cov`, `testcontainers[postgres,rabbitmq]`, `ruff`, `mypy` | latest | OK | tests/lint |

Explicitly excluded: `opensearch-py`, `elasticsearch`, `flask`, `celery`, `torch`,
`transformers`, `sentence-transformers` (weight >> value; tokenizers+onnxruntime suffice).

---

## 3. AMQP topology (verified against legacy `app/amqp/amqp.py`)

### 3.1 Connection

- URL: `AMQP_URL` (stripped of trailing `/` and `\`) + `/` + `AMQP_VIRTUAL_HOST`
  (default vhost **`analyzer`**), plus query `?heartbeat=<AMQP_HEARTBEAT_INTERVAL>`.
  Legacy: `amqpUrl = AMQP_URL.strip("/").strip("\\") + "/" + AMQP_VIRTUAL_HOST`.
- Reconnect with exponential backoff: initial `AMQP_INITIAL_RETRY_INTERVAL` (1 s),
  factor `AMQP_BACKOFF_FACTOR` (2), cap 60 s, give up after `AMQP_MAX_RETRY_TIME`
  (300 s) → process exits non-zero (compose restarts it).

### 3.2 Exchange declaration — EXACT legacy arguments (RP discovery contract)

The RP backend discovers analyzers by scanning exchanges and reading these arguments.
Declare **exactly**:

```python
channel.exchange_declare(
    exchange=cfg.exchange_name,          # AMQP_EXCHANGE_NAME, default "analyzer"
    exchange_type="fanout",              # legacy amqpExchangeType default
    durable=False,
    auto_delete=True,
    internal=False,
    arguments={
        "analyzer":            cfg.exchange_name,       # str, service name == exchange name
        "analyzer_index":      cfg.analyzer_index,      # bool, default True
        "analyzer_priority":   cfg.analyzer_priority,   # int,  default 1
        "analyzer_log_search": cfg.analyzer_log_search, # bool, default True
        "analyzer_suggest":    cfg.analyzer_suggest,    # bool, default True
        "analyzer_cluster":    cfg.analyzer_cluster,    # bool, default True
        "version":             cfg.app_version,         # str, from VERSION file
    },
)
```

Rules (all from legacy):
- Re-declare before every (re)consume/publish, throttled to at most once per 60 s per
  `(broker, exchange)` pair.
- On `ChannelClosedByBroker` code 406 with reply text containing
  `"PRECONDITION_FAILED - inequivalent arg 'type' for exchange"`: delete the exchange
  and re-declare (handles leftover legacy exchange of a different type).
- Because the exchange is **fanout** and auto-delete, message routing keys do NOT route —
  every bound queue receives every message; the consumer filters by
  `method.routing_key`. This is why two queues exist (§3.3).

### 3.3 Queues

| Queue | Declare | Bound to | Consumes | Filter |
|---|---|---|---|---|
| `analyzer-ng.all` | `durable=True, exclusive=False, auto_delete=False` | exchange `analyzer`, no routing key | everything | drop `train_models` (log at DEBUG, ack) |
| `analyzer-ng.train` | same | same | everything | keep only `train_models` (others ack + drop) |

Legacy used queue names `all` and `train`; we use prefixed names so analyzer-ng can run
side-by-side with a legacy analyzer during migration without stealing its messages
(fanout delivers a copy to every queue, so both receive traffic; only one should reply —
**do not run both with the same exchange name in production**, see §7.2). If strict
drop-in naming is desired set `ANALYZER_NG_QUEUE_PREFIX=""` to get `all`/`train`.
Consumer QoS: `basic_qos(prefetch_count=<ANALYZER_NG_PREFETCH, default 1>, prefetch_size=0)`,
`auto_ack=False`, `exclusive=False`.

### 3.4 RPC reply mechanics (verbatim legacy semantics)

Reply is published to the **default exchange** with the caller's `reply_to` as routing
key and the original `correlation_id`:

```python
channel.basic_publish(
    exchange="",
    routing_key=props.reply_to,
    properties=BasicProperties(correlation_id=props.correlation_id,
                               content_type="application/json"),
    mandatory=False,
    body=response_json.encode("utf-8"),
)
```

- If `reply_to` is absent, never publish a reply (fire-and-forget message).
- If the handler produced `None` (see disposition table), never publish a reply —
  legacy replies nothing on handler error and RP treats timeout as "analyzer failed";
  preserve this.
- Bodies are plain JSON (UTF-8), no envelope.

---

## 4. AMQP contract — routing keys, schemas, dispositions

### 4.1 Message properties

- Request: `correlation_id` (uuid string, may be empty), `reply_to` (anonymous queue,
  present for RPC keys), optional header `timestamp_in_ms` (int or string, possible
  trailing `L` — strip before `int()`; fallback: current epoch ms). Used as priority
  (lower = older = first).
- Response: `correlation_id` echoed, `content_type="application/json"`.

### 4.2 Wire models (`src/analyzer_ng/amqp/models.py`)

Copied **field-for-field** from legacy `app/commons/model/launch_objects.py` and
`app/commons/model/ml.py`. Pydantic v2, `BaseModel`, defaults exactly as shown.
`timestamp7 = tuple[int, int, int, int, int, int, int]` (RP sends time as a 7-tuple
`[Y, M, D, h, m, s, weekday]`; default factory = now).

```python
ERROR_LOGGING_LEVEL: int = 40000

class AnalyzerConf(BaseModel):
    analyzerMode: str = "ALL"
    minShouldMatch: int = 80
    numberOfLogLines: int = -1
    isAutoAnalyzerEnabled: bool = True
    indexingRunning: bool = True
    allMessagesShouldMatch: bool = False
    searchLogsMinShouldMatch: int = 95
    uniqueErrorsMinShouldMatch: int = 95
    numberOfLogsToIndex: int = 20
    minimumLogLevel: int = ERROR_LOGGING_LEVEL
    similarityThresholdToDrop: float = 0.95
    searchScoreMode: str = "avg"

class Log(BaseModel):
    logId: int
    logLevel: int = 0
    logTime: timestamp7 = Field(default_factory=timestamp_factory)
    message: str
    clusterId: int = 0
    clusterMessage: str = ""

class TestItem(BaseModel):
    testItemId: int
    isAutoAnalyzed: bool
    uniqueId: str = ""
    issueType: str = ""
    issueDescription: str = ""
    originalIssueType: str = ""
    startTime: timestamp7 = Field(default_factory=timestamp_factory)
    endTime: Optional[list[int]] = None
    lastModified: Optional[list[int]] = None
    testCaseHash: int = 0
    testItemName: str = ""
    description: Optional[str] = None
    linksToBts: list[str] = []
    logs: list[Log] = []

class Launch(BaseModel):
    launchId: int
    project: int
    launchName: str = ""
    launchNumber: int = 0
    previousLaunchId: int = 0
    launchStartTime: timestamp7 = Field(default_factory=timestamp_factory)
    analyzerConfig: AnalyzerConf = AnalyzerConf()
    testItems: list[TestItem] = []
    clusters: dict = {}

class TestItemInfo(BaseModel):          # suggest request
    testItemId: int = 0
    uniqueId: str = ""
    testCaseHash: int = 0
    clusterId: int = 0
    launchId: int
    launchName: str = ""
    launchNumber: int = 0
    previousLaunchId: int = 0
    testItemName: str = ""
    project: int
    analyzerConfig: AnalyzerConf = AnalyzerConf()
    logs: list[Log] = []

class LaunchInfoForClustering(BaseModel):
    launch: Launch
    project: int
    forUpdate: bool = False
    numberOfLogLines: int
    cleanNumbers: bool = False

class SearchLogs(BaseModel):            # search request
    launchId: int
    launchName: str
    itemId: int
    projectId: int
    filteredLaunchIds: list[int]
    logMessages: list[str]
    analyzerConfig: AnalyzerConf = AnalyzerConf()
    logLines: int

class ItemUpdate(BaseModel):
    timestamp: timestamp7 = Field(default_factory=timestamp_factory)
    issueType: str
    issueComment: str = ""

class DefectUpdate(BaseModel):          # defect_update request — FEEDBACK SIGNAL
    project: int | str
    itemsToUpdate: dict[int | str, Union[str, ItemUpdate]]

class DeleteLogsRequest(BaseModel):     # clean
    ids: list[int]
    project: int

class DeleteTestItemsRequest(BaseModel):  # item_remove
    project: int | str
    itemsToDelete: list[int | str]

class DeleteLaunchesRequest(BaseModel):   # launch_remove
    project: int | str
    launch_ids: list[int | str]           # NOTE: snake_case on the wire — keep

class RemoveByDatesRequest(BaseModel):    # remove_by_launch_start_time / remove_by_log_time
    project: int | str
    interval_start_date: str              # snake_case on the wire — keep
    interval_end_date: str

class TrainInfo(BaseModel):               # train_models
    model_type: ModelType                 # enum: defect_type | suggestion | auto_analysis
    project: int
    additional_projects: Optional[Iterable[int]] = None
    gathered_metric_total: int = 0
```

Response models:

```python
class AnalysisResult(BaseModel):        # analyze reply element
    testItem: int
    issueType: str
    relevantItem: int

class SearchLogInfo(BaseModel):         # search reply element
    logId: int
    testItemId: int
    matchScore: float

class ClusterInfo(BaseModel):
    clusterId: int
    clusterMessage: str
    logIds: list[int]
    itemIds: list[int]

class ClusterResult(BaseModel):         # cluster reply
    project: int
    launchId: int
    clusters: list[ClusterInfo]

class SuggestAnalysisResult(BaseModel): # suggest reply element — ALL fields required by UI
    project: int
    testItem: int
    testItemLogId: int
    launchId: int
    launchName: str
    launchNumber: int
    issueType: str
    relevantItem: int
    relevantLogId: int
    isMergedLog: bool = False
    matchScore: float                   # 0..100 percent in legacy UI
    resultPosition: int                 # 0-based rank
    esScore: Optional[float] = 0.0      # fill with fused retrieval score
    esPosition: Optional[int] = None    # fill with retrieval rank
    modelFeatureNames: Optional[str] = None
    modelFeatureValues: Optional[str] = None
    modelInfo: Optional[str] = None     # fill "analyzer-ng;gbm=<ver>;emb=<ver>;kb_mode=<id|none>"
    usedLogLines: int                   # fill from analyzerConfig.numberOfLogLines
    minShouldMatch: int                 # fill from analyzerConfig.minShouldMatch
    processedTime: float                # seconds spent producing the suggestion
    userChoice: int = 0
    methodName: str                     # "auto_analysis" | "suggestion" (keep legacy vocab)
    clusterId: int = 0

class LogExceptionResult(BaseModel):
    logId: int
    foundExceptions: list[str] = []

class BulkResponse(BaseModel):          # index / defect-adjacent reply
    took: int
    errors: bool
    items: list[str] = []
    logResults: list[LogExceptionResult] = []
    status: int = 0

class SuggestPatternLabel(BaseModel):
    pattern: str
    totalCount: int
    percentTestItemsWithLabel: float = 0.0
    label: str = ""

class SuggestPattern(BaseModel):        # suggest_patterns reply
    suggestionsWithLabels: list[SuggestPatternLabel] = []
    suggestionsWithoutLabels: list[SuggestPatternLabel] = []
```

### 4.3 Serialization rules (match legacy `processor.py`)

- List-of-model replies (`analyze`, `search`, `suggest`):
  `json.dumps([m.model_dump() for m in results])`.
- Single-model replies (`index`, `cluster`, `suggest_patterns`): `model.model_dump_json()`.
- Plain scalar replies (`delete`, `clean`, `item_remove`, `launch_remove`,
  `remove_by_*`): stringified int, e.g. `"3"` (count of affected entities).
- `defect_update` reply: `json.dumps(list_of_ints)` — list of testItemIds **not** found/updated.

### 4.4 Routing-key table (complete — mirrors legacy `ServiceProcessor.__configs`)

| Routing key | Request model | Reply | Disposition in analyzer-ng |
|---|---|---|---|
| `index` | `list[Launch]` | `BulkResponse` (model_dump_json) | **Implement**: ingest pipeline (preprocess → Drain3 → fingerprint → embed → store). `took`=ms elapsed, `errors`=any failure, `logResults[].foundExceptions`= extracted exception names per log (RP uses this for "unique errors"). |
| `analyze` | `list[Launch]` | `list[AnalysisResult]` (json list of dumps) | **Implement**: full pipeline; abstained items are **omitted** from the reply (legacy behavior: only confident items returned; RP leaves the rest `ti`). No extra fields in reply elements — keep extended data (matchScore/explanation) internal in PG. |
| `suggest` | `TestItemInfo` | `list[SuggestAnalysisResult]` | **Implement**: read precomputed suggestions; fill legacy-only fields as annotated in §4.2. Empty list is a valid reply. |
| `cluster` | `LaunchInfoForClustering` | `ClusterResult` (model_dump_json) | **Implement**: launch-level grouping output; clusterId = stable hash of group fingerprint (positive int53), clusterMessage = representative normalized message. |
| `search` | `SearchLogs` | `list[SearchLogInfo]` | **Implement**: lexical+dense retrieval over indexed logs restricted to `filteredLaunchIds`; `matchScore` = fused similarity in 0..100. |
| `delete` | int (raw JSON number = project id) | `str(count)` | **Implement**: drop all analyzer data for project (partition truncate). |
| `clean` | `DeleteLogsRequest` | `str(count)` | **Implement**: delete listed log rows. |
| `item_remove` | `DeleteTestItemsRequest` | `str(count)` | **Implement**: delete listed test items (+their logs/signatures). |
| `launch_remove` | `DeleteLaunchesRequest` | `str(count)` | **Implement**: delete by launch ids. |
| `remove_by_launch_start_time` | `RemoveByDatesRequest` | `str(count)` | **Implement**: delete items whose launch start time in `[interval_start_date, interval_end_date)` (ISO strings). |
| `remove_by_log_time` | `RemoveByDatesRequest` | `str(count)` | **Implement**: same, by log time. |
| `defect_update` | `DefectUpdate` | `json list[int]` of NOT-updated item ids | **Implement — PRIMARY FEEDBACK SOURCE.** See §4.5. |
| `train_models` | `TrainInfo` | none (no reply ever) | **Implement**: GBM retrain from label_events for `project` (+`additional_projects`); consumed only by `train` queue/handler. |
| `namespace_finder` | `list[Launch]` | none | **No-op with WARN log** (legacy computed namespace stats for OpenSearch queries; obsolete). Ack normally. |
| `suggest_patterns` | int (project id) | `SuggestPattern` (model_dump_json) | **Implement (minimal)**: derive pattern suggestions from KB modes/templates; empty lists are valid and UI-safe. |
| `index_suggest_info` | `list[SuggestAnalysisResult]` | `json {}` | **No-op with WARN** — deprecated in legacy: logs `"Deprecated 'index_suggest_info' route called with: ..."`, replies `{}`. Mirror exactly. |
| `remove_suggest_info` | int | `str(same int)` | **No-op with WARN** (legacy deprecated); echo the int back. |
| `update_suggest_info` | any JSON | `json 1` | **No-op with WARN** (legacy deprecated); reply `1`. |
| `remove_models` | any JSON | `str(None)`-equivalent: reply not meaningful — mirror legacy: WARN log, handler returns None ⇒ **no reply** | **No-op with WARN**. |
| `get_model_info` | any JSON | none (legacy handler returns None ⇒ no reply) | **No-op with WARN**. |
| `noop_sleep` | number (seconds) | none | **Keep** (test utility): sleep, no reply. |
| `noop_echo` | any | `str(payload)` | **Keep** (test utility). |
| `noop_fail` | any | none | **Keep**: raise deliberately (exercises retry path). |
| *unknown key* | — | none | WARN log `"Unknown routing key '<key>', ignoring"`, ack, no reply. Legacy raised KeyError; be stricter-but-safer. |

### 4.5 `defect_update` — label_event feedback contract

Payload (exact legacy shape): `{"project": <int|str>, "itemsToUpdate": {"<testItemId>":
<issueType-string OR {"timestamp": [...7 ints...], "issueType": "pb001",
"issueComment": "..."}>, ...}}`. Values are either a bare issue-type locator string
(old backends) or an `ItemUpdate` object (new backends) — accept both
(`Union[str, ItemUpdate]`).

Handler semantics:
1. Normalize every entry to `(test_item_id:int, issue_type:str.lower(), issue_comment,
   timestamp)`.
2. For each item known to analyzer-ng: append an **append-only `label_event` row**
   (spec 02) with `source='defect_update'`, previous predicted label (if any), new human
   label, `is_auto_analyzed=False`; update KB-mode purity counters; mark any pending
   suggestion rows for the item as confirmed/corrected (feeds acceptance metrics).
3. Reply with `json.dumps([ids not found in analyzer storage])` — legacy returns the
   not-updated list and RP tolerates non-empty.
4. Optionally enqueue an internal debounced retrain trigger (never synchronous).

---

## 5. Configuration spec

`config.py` uses `pydantic-settings`; every var read once at startup into a frozen
`AppConfig`. Precedence: **process env > `.env` file (dev only) > coded default**. No
config file in production. Boolean parsing accepts `TRUE/True/true/1/Y/y` and
`FALSE/False/false/0/N/n` (legacy `to_bool`); anything else ⇒ startup validation error.

### 5.1 Legacy-compatible vars (names MUST stay — RP compose already sets them)

| Env var | Default | Description |
|---|---|---|
| `AMQP_URL` | *(required)* | e.g. `amqp://rabbitmq:5672` or with creds; trailing slashes stripped |
| `AMQP_VIRTUAL_HOST` | `analyzer` | appended to URL as vhost |
| `AMQP_EXCHANGE_NAME` | `analyzer` | exchange + advertised service name |
| `AMQP_HEARTBEAT_INTERVAL` | `30` | seconds, appended as `?heartbeat=` |
| `AMQP_INITIAL_RETRY_INTERVAL` | `1` | seconds |
| `AMQP_MAX_RETRY_TIME` | `300` | seconds before giving up connecting |
| `AMQP_BACKOFF_FACTOR` | `2` | exponential backoff multiplier |
| `AMQP_HANDLER_MAX_RETRIES` | `3` | per-message processing retries |
| `AMQP_HANDLER_TASK_TIMEOUT` | `600` | seconds; task watchdog (§8.2) |
| `ANALYZER_PRIORITY` | `1` | advertised `analyzer_priority` (lower wins in RP) |
| `ANALYZER_INDEX` | `true` | advertised `analyzer_index` |
| `ANALYZER_LOG_SEARCH` | `true` | advertised `analyzer_log_search` |
| `ANALYZER_SUGGEST` | `true` | advertised `analyzer_suggest` |
| `ANALYZER_CLUSTER` | `true` | advertised `analyzer_cluster` |
| `ANALYZER_HTTP_PORT` | `5001` | health/metrics HTTP port |
| `ANALYZER_FILE_LOGGING_PATH` | `/tmp/config.log` | optional file log sink (empty = stdout only) |
| `LOGGING_LEVEL` | `INFO` | root log level (legacy default DEBUG; we default INFO) |
| `DEBUG_MODE` | `false` | run handlers inline on consumer thread (no pool), verbose logging |

Legacy ES/datastore vars (`ES_HOSTS`, `ES_USER`, `DATASTORE_*`, `MINIO_*`, boost/model
`ES_BOOST_*` etc.) are **accepted but ignored** with a one-line WARN each if set —
prevents crash-loops when dropped into an unmodified legacy env block.

### 5.2 New analyzer-ng vars

| Env var | Default | Description |
|---|---|---|
| `ANALYZER_PG_DSN` | *(required unless discrete vars set)* | e.g. `postgresql://rpuser:rppass@postgres:5432/analyzer` |
| `ANALYZER_PG_HOST`/`_PORT`/`_USER`/`_PASSWORD`/`_DB` | `postgres`/`5432`/—/—/`analyzer` | discrete alternative; DSN wins if both set |
| `ANALYZER_PG_SCHEMA` | `analyzer` | schema for all tables |
| `ANALYZER_PG_POOL_MIN` / `ANALYZER_PG_POOL_MAX` | `2` / `10` | psycopg pool bounds |
| `ANALYZER_PG_CREATE_DB` | `true` | create database `ANALYZER_PG_DB` at startup if missing (§7.2) |
| `ANALYZER_PG_ADMIN_DSN` | *(empty)* | optional superuser DSN used only for create-db; defaults to `ANALYZER_PG_DSN` pointed at db `postgres` |
| `ANALYZER_NG_WORKERS` | `2` | worker pool size |
| `ANALYZER_NG_PREFETCH` | `1` | basic_qos prefetch_count per consumer |
| `ANALYZER_NG_QUEUE_PREFIX` | `analyzer-ng.` | queue name prefix (`""` = legacy `all`/`train`) |
| `ANALYZER_NG_QUEUE_SIZE` | `100` | bounded internal dispatch queue |
| `ANALYZER_EMB_MODEL_PATH` | `/opt/analyzer/models/e5-small-int8` | ONNX model + tokenizer dir |
| `ANALYZER_EMB_DIMS` | `384` | embedding dims (Matryoshka truncation target) |
| `ANALYZER_AUTO_MIN_PROB` | `0.6` | abstain threshold for auto-analysis (below ⇒ omit ⇒ `ti`) |
| `ANALYZER_SUGGEST_MAX` | `3` | max suggestions returned (legacy `MAX_SUGGESTIONS_NUMBER`) |
| `ANALYZER_BURST_SI_SHARE` | `0.5` | dominant-new-fingerprint share of launch failures ⇒ `si` prior |
| `ANALYZER_TIME_DECAY` | `0.999` | per-day recency decay in candidate weighting |
| `ANALYZER_LLM_ENABLED` | `false` | master switch for all LLM roles |
| `OLLAMA_URL` | `http://ollama:11434` | Ollama endpoint (used only if LLM enabled) |
| `ANALYZER_LLM_MODEL` | `qwen3:4b` | primary SLM |
| `ANALYZER_LLM_JUDGE_TAU` | `0.5` | GBM max-prob below which Judge role engages |
| `ANALYZER_SEED_KB_PATH` | `/opt/analyzer/seeds/failure_modes.json` | seed KB file |

### 5.3 Startup validation

Fail fast (exit code 2, clear message) when: `AMQP_URL` missing; no PG DSN derivable;
`ANALYZER_EMB_MODEL_PATH` missing/unloadable; numeric vars unparsable; thresholds
outside [0,1]; `ANALYZER_LLM_ENABLED=true` but `OLLAMA_URL` unreachable is a **WARN not
fatal** (degrade to LLM-off).

---

## 6. Startup sequence & shutdown

`main.py` order (each step logged with duration):

1. **Load & validate config** (§5.3). Read `VERSION` file → `app_version`.
2. **Start HTTP server thread** immediately with `ready=false` (liveness available
   during long startup; readiness gated).
3. **PG connect with retry**: tenacity, exponential 1 s → 30 s cap, total budget 300 s.
   If `ANALYZER_PG_CREATE_DB=true`: connect to maintenance db `postgres` (admin DSN or
   main credentials), `SELECT 1 FROM pg_database WHERE datname=%s`; if absent,
   `CREATE DATABASE analyzer` (cannot run in a transaction — use autocommit). Then
   connect to `analyzer`, `CREATE SCHEMA IF NOT EXISTS analyzer`,
   `CREATE EXTENSION IF NOT EXISTS vector`.
4. **Migrations under advisory lock**: `SELECT pg_advisory_lock(hashtext('analyzer_ng_migrations'))`;
   apply ordered `NNNN_*.sql` not present in `analyzer.schema_migrations`
   (each in its own transaction, recorded with checksum); unlock. Safe for N concurrent
   replicas (spec 02 details).
5. **ONNX model load & warmup**: create `onnxruntime.InferenceSession` (CPU EP,
   `intra_op_num_threads=ANALYZER_NG_WORKERS`), embed a fixed warmup sentence, assert
   output shape `(1, ANALYZER_EMB_DIMS)`; record `emb_model_ver` (dir name + sha256 of
   model file, truncated 12 hex).
6. **Seed KB load (idempotent)**: upsert `seeds/failure_modes.json` into KB tables keyed
   by stable `seed_id`; never overwrite rows a human has touched (`origin='seed'` AND
   `updated_by IS NULL` guard).
7. **AMQP connect with retry + exchange declare** (§3.1–3.2), declare+bind both queues.
8. **Start consumers + worker pool + reply publisher.**
9. **Readiness flips true.** Log single line: `analyzer-ng <version> ready (emb=<ver>, gbm=<ver>, pg=<host>/<db>)`.

**Graceful shutdown** (SIGTERM/SIGINT): stop consuming (`basic_cancel`), wait up to 30 s
for in-flight tasks, publish remaining replies, close AMQP connections, close PG pool,
exit 0. Unacked messages are redelivered by the broker after connection close — handlers
must be idempotent (§8.4). Second signal ⇒ immediate exit 130.

---

## 7. Docker & compose integration

### 7.1 Dockerfile outline

```dockerfile
# syntax=docker/dockerfile:1
FROM python:3.12-slim AS build
WORKDIR /build
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv export --frozen -o req.txt \
 && pip wheel --no-cache-dir -r req.txt -w /wheels
# Model baked at build (deterministic images; no runtime download):
# models/ dir is committed via Git LFS or fetched by CI before docker build,
# pinned by sha256 checked in scripts/fetch_model.py.
COPY models/ /opt/analyzer/models/

FROM python:3.12-slim
RUN useradd -r -u 1301 analyzer
WORKDIR /opt/analyzer
COPY --from=build /wheels /wheels
COPY --from=build /opt/analyzer/models /opt/analyzer/models
RUN pip install --no-cache-dir --no-index -f /wheels analyzer-ng && rm -rf /wheels
COPY VERSION ./
USER analyzer
EXPOSE 5001
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
  CMD python -c "import urllib.request,sys;\
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5001/health',timeout=4).status==200 else 1)"
ENTRYPOINT ["python", "-m", "analyzer_ng.main"]
```

Decision: **bake the ONNX model into the image** (≈120 MB int8). Rationale: air-gapped
installs, deterministic `emb_model_ver`, no first-start download stampede. `start-period`
120 s covers migration + warmup on tiny hosts.

### 7.2 docker-compose fragment (standard ReportPortal compose)

Remove services `analyzer`, `analyzer-train`, `opensearch` (and its volume). Add:

```yaml
services:
  analyzer:                       # keep the service NAME "analyzer" — RP compose
    image: analyzer-ng:latest     # healthchecks/depends_on reference it
    environment:
      LOGGING_LEVEL: info
      AMQP_URL: amqp://${RABBITMQ_DEFAULT_USER}:${RABBITMQ_DEFAULT_PASS}@rabbitmq:5672
      AMQP_VIRTUAL_HOST: analyzer
      AMQP_EXCHANGE_NAME: analyzer
      ANALYZER_PG_DSN: postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/analyzer
      ANALYZER_PG_CREATE_DB: "true"
      # ANALYZER_LLM_ENABLED: "true"
      # OLLAMA_URL: http://ollama:11434
    depends_on:
      rabbitmq: { condition: service_healthy }
      postgres: { condition: service_healthy }
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request;urllib.request.urlopen('http://127.0.0.1:5001/health',timeout=4)\""]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 120s
    restart: unless-stopped
    mem_limit: 2g

  ollama:                         # OPTIONAL — only when LLM roles enabled
    image: ollama/ollama:latest
    volumes: [ "ollama-data:/root/.ollama" ]
    restart: unless-stopped
    # model pulled on first use by analyzer-ng via /api/pull, or pre-pulled here

volumes:
  ollama-data: {}
```

**PG reuse**: the RP compose runs `postgres` with the superuser defined by
`POSTGRES_USER`/`POSTGRES_PASSWORD` (RP main db `reportportal`). analyzer-ng reuses the
same instance and credentials but its **own database `analyzer`**. Creation strategy
(pick automatically): the service itself creates the db at startup because the compose
user is a superuser and `ANALYZER_PG_CREATE_DB=true` (§6 step 3). Fallback for hardened
installs: operator pre-creates db+role and sets `ANALYZER_PG_CREATE_DB=false`; an
example init script `docker/pg-init/01-analyzer-db.sh` (mounted to
`/docker-entrypoint-initdb.d/`) is shipped for that path. The service never touches the
`reportportal` database.

---

## 8. Concurrency & reliability

### 8.1 Sizing & backpressure

- Worker pool: `ANALYZER_NG_WORKERS=2` default (legacy processed max 2 concurrent
  tasks — `NUMBER_OF_TASKS_TO_SEND=2`). ONNX intra-op threads share the same budget;
  target container ≤ 2 GB RAM / 2 vCPU.
- Bounded dispatch queue (`ANALYZER_NG_QUEUE_SIZE=100`); when full, the consumer thread
  blocks on `put()` — combined with `prefetch_count`, the broker stops delivering and
  messages accumulate durably in RabbitMQ. That is the backpressure mechanism; never
  drop silently.
- Priority ordering inside the dispatch queue: `(timestamp_in_ms, arrival_seq)` —
  matches legacy `PriorityQueue` semantics.

### 8.2 Timeouts & retries

- Per-task watchdog: a task running longer than `AMQP_HANDLER_TASK_TIMEOUT` (600 s) is
  cancelled (cooperative cancel flag checked between pipeline stages; PG statement
  timeout set to the remaining budget) and treated as a failure.
- Failure handling per message: retry in-process up to `AMQP_HANDLER_MAX_RETRIES` (3)
  with 1 s/2 s/4 s delays, except non-retryable errors: pydantic `ValidationError`,
  unknown routing key, "not found"-class deletions (mirrors legacy retry predicate that
  skipped retries for missing-document conflicts). After exhaustion: log ERROR with full
  body, ack, publish **no reply** (RP times out — legacy behavior).
- DLQ: declare `analyzer-ng.dlq` (durable, bound to no exchange); exhausted/unparseable
  messages are republished there verbatim with headers `x-error`, `x-routing-key`,
  `x-retries` for post-mortem. Legacy had none; this is additive and invisible to RP.

### 8.3 Ack semantics

Legacy acks immediately after JSON parse (before processing). analyzer-ng keeps
**parse-time ack** for compatibility of throughput behavior: malformed JSON ⇒
`basic_nack(requeue=False)` (+DLQ copy); parsed ⇒ `basic_ack` right away; all
subsequent failure handling is the in-process retry of §8.2. Consequence: a crash
mid-task can lose that task (same as legacy). Deliberate trade-off — RP retries
analysis on its side and all mutating handlers are idempotent anyway.

### 8.4 Idempotency

- `index`: upsert by `(project_id, log_id)` / `(project_id, test_item_id)`; re-indexing
  the same launch is a no-op rewrite. Embedding recompute skipped when
  `content_hash` + `emb_model_ver` unchanged.
- `analyze`/`suggest`: pure reads + suggestion-row upserts keyed by
  `(project_id, test_item_id, model_versions)`.
- `defect_update`: label_events deduped by `(project_id, item_id, issue_type, ts)`.
- deletions: naturally idempotent; count reflects rows actually removed.

---

## 9. Observability

### 9.1 Structured logging

JSON lines to stdout (`python-json-logger`-style formatter, hand-rolled — no extra dep).
Mandatory fields: `ts` (ISO8601), `level`, `logger`, `msg`, `correlation_id` (message
correlation_id or generated uuid, contextvar-propagated), `routing_key`, `project`,
`duration_ms` (on completion lines), `app_version`. Message bodies logged only at DEBUG
(legacy parity: `log_incoming_message`/`log_outgoing_message`). Never log AMQP
credentials (strip like legacy `remove_credentials_from_url`).

### 9.2 HTTP endpoints (FastAPI on `ANALYZER_HTTP_PORT`, default 5001)

- `GET /` — legacy-compatible health JSON, HTTP 200/503:
  `{"status": "healthy", "threads": [{"name": "all", "status": "alive",
  "running_tasks": {"number": n, "tasks": [{"routing_key":..., "correlation_id":...,
  "send_time":...}]}}, ...]}`. On PG failure: `{"status": "PostgreSQL is not healthy"}`
  + 503 (legacy shape used `"OpenSearch is not healthy"` — same key, new text).
- `GET /health` — `{"live": true, "ready": <bool>, "pg": <bool>, "amqp": <bool>,
  "emb_model_ver": "...", "gbm_model_ver": "...", "version": "..."}`; 503 until ready.
- `GET /metrics` — Prometheus text: `analyzer_requests_total{routing_key,outcome}`,
  `analyzer_request_seconds{routing_key}` (histogram), `analyzer_queue_depth`,
  `analyzer_suggest_shown_total` / `_accepted_total` / `_corrected_total{project}`,
  `analyzer_abstain_total{project}`, `analyzer_pg_pool_in_use`.

### 9.3 Model-version stamping

Every persisted suggestion/analysis row and every `SuggestAnalysisResult.modelInfo`
carries `emb_model_ver`, `gbm_model_ver`, `kb_snapshot_id`. Similarity is never computed
across differing `emb_model_ver` (CONTEXT.md §3).

---

## 10. Acceptance criteria (implementing agent must verify each)

1. `docker compose up` on a stock ReportPortal compose (with §7.2 edits) starts
   analyzer-ng; container reaches `healthy`; no OpenSearch/analyzer-train containers.
2. RabbitMQ management API shows exchange `analyzer`, type `fanout`,
   `durable=false, auto_delete=true`, with arguments exactly:
   `analyzer`, `analyzer_index`, `analyzer_priority`, `analyzer_log_search`,
   `analyzer_suggest`, `analyzer_cluster`, `version` — and RP backend lists the analyzer
   as available (Project Settings → Auto-Analysis shows an analyzer present).
3. Queues `analyzer-ng.all` and `analyzer-ng.train` exist (durable), both bound to the
   exchange; `train_models` messages are processed only by the train consumer.
4. RPC round-trip: publishing the sample `index` payload (list of one `Launch` with one
   `TestItem` with one ERROR `Log`) with `reply_to`+`correlation_id` returns within 30 s
   a `BulkResponse` JSON that validates against §4.2 and echoes the correlation_id with
   `content_type=application/json` on the default exchange.
5. `analyze` RPC on the same data returns a JSON **array** of objects each containing
   exactly `testItem` (int), `issueType` (str), `relevantItem` (int).
6. `suggest` RPC returns a JSON array whose elements validate against
   `SuggestAnalysisResult` with **all** fields of §4.2 present (including
   `esScore`, `esPosition`, `modelInfo`, `usedLogLines`, `minShouldMatch`,
   `processedTime`, `methodName`).
7. `defect_update` with `{"project":1,"itemsToUpdate":{"123":"pb001","456":{"issueType":
   "ab001","issueComment":"x","timestamp":[2026,7,14,12,0,0,0]}}}` (a) replies with a
   JSON int array of unknown ids, (b) inserts one `label_event` row per known id.
8. Deprecated keys `index_suggest_info`, `remove_suggest_info`, `update_suggest_info`,
   `remove_models`, `get_model_info`, and `namespace_finder` each produce a single WARN
   log line and the exact reply disposition of §4.4 (no crash, message acked).
9. Malformed JSON body ⇒ nack(requeue=False) + copy in `analyzer-ng.dlq`; consumer
   keeps running.
10. First start against a PG instance with no `analyzer` database creates the database,
    schema, `vector` extension, and all migrations; second start applies nothing and
    logs "0 migrations applied"; two replicas starting simultaneously do not deadlock or
    double-apply (advisory lock test).
11. Seed KB load runs on every start and is idempotent (row counts stable across
    restarts).
12. `GET /` returns 200 with `{"status":"healthy",...}` when PG is up, 503 with
    non-healthy status text when PG is stopped; `GET /health` reports `ready:false`
    before consumers start; `GET /metrics` exposes the §9.2 series.
13. SIGTERM during an in-flight `analyze` completes the task, publishes the reply, and
    exits 0 within 35 s.
14. Killing RabbitMQ for 30 s mid-run: service reconnects with backoff, re-declares the
    exchange, resumes consuming; no message published to `reply_to` twice.
15. `pip-licenses` (or equivalent) over the built image shows only
    Apache-2.0-compatible licenses; no GPL/AGPL packages.
16. Integration test suite (`tests/integration/test_amqp_contract.py`) covering criteria
    4–9 passes against dockerized rabbitmq + pgvector via testcontainers.

---

## 11. Cross-references

- PG schema, migration file format, RetrievalStore SQL contract → `specs/02-database.md`
- Pipeline stages, fingerprint definition, seed KB content → `specs/03-pipeline.md`
- LLM roles, prompts, constrained decoding, security → `specs/04-llm-sidecar.md`
