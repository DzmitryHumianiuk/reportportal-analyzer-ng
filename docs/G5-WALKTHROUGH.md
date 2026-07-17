# G5 Walkthrough — analyzer-ng against a real ReportPortal

**Gate G5** (MASTER_PLAN): *full docker-compose with a real local ReportPortal
instance: launch import → auto-analysis visible, suggestions in "Make Decision",
defect update flows back as label_event.*

This document is a **real, executed** walkthrough — every block below is a verbatim
curl / psql / rabbitmqctl transcript captured on the date shown, not a mock-up.

- **Environment:** macOS (arm64) + colima, Docker Engine 29.2.1, docker-compose 5.1.1,
  4 vCPU / 7.7 GiB. ReportPortal **5.15** official compose. analyzer-ng image
  `analyzer-ng:latest` (local, 1.01 GB). Stack peak memory ≈ 1.5 GiB (OpenSearch and
  service-analyzer-train removed — see `docs/INSTALL.md`).
- **Reproduce:** apply the base-file edits in `docs/INSTALL.md`, bring the stack up,
  then run `scripts/g5/10-report-launch.sh`, `20-enable-and-analyze.sh`,
  `30-second-launch-suggest.sh` (each reads a saved token/ids from the previous step).

All API calls go through the RP gateway on `http://localhost:8080`; DB checks run
against the shared postgres container (separate `analyzer` database).

---

## 0. Stack up — analyzer-ng healthy, no OpenSearch / no analyzer-train

```
$ docker compose -f rp-compose.yml -f docker-compose.analyzer-ng.yml \
    -p rpng --profile core --profile infra --profile "" ps
NAME              SERVICE    STATUS
postgres          postgres   Up (healthy)
rpng-analyzer-1   analyzer   Up (healthy)      <- analyzer-ng
rpng-api-1        api        Up (healthy)
rpng-gateway-1    gateway    Up (healthy)
rpng-index-1      index      Up (healthy)
rpng-jobs-1       jobs       Up (healthy)
rpng-rabbitmq-1   rabbitmq   Up (healthy)
rpng-uat-1        uat        Up (healthy)
rpng-ui-1         ui         Up (healthy)
# (no `opensearch`, no `analyzer-train`)
```

analyzer-ng cold-start (bootstrap → migrations → seed KB → consumers), from
`docker logs rpng-analyzer-1`:

```
INFO analyzer_ng.db.migrate   Applied 5 migration(s): [1, 2, 3, 4, 5]
INFO __main__                  Migrations applied this start: 5
INFO analyzer_ng.seeds.loader  Seed KB loaded: 50 failure modes
INFO analyzer_ng.amqp.client   Exchange 'analyzer' declared
INFO analyzer_ng.amqp.consumer Consumer 'all' consuming queue 'analyzer-ng.all'
INFO analyzer_ng.amqp.consumer Consumer 'train' consuming queue 'analyzer-ng.train'
INFO analyzer_ng.service       analyzer-ng 0.1.0 ready (emb=None, gbm=None)
```

Health endpoint:

```
$ curl -s .../health   # (via docker exec on :5001)
{"live":true,"ready":true,"pg":true,"amqp":true,
 "emb_model_ver":null,"gbm_model_ver":null,"version":"0.1.0", ...}
```

## 1. RP discovers the analyzer (RabbitMQ topology + args)

Acceptance §10.2/§10.3 — exchange `analyzer` (fanout, `durable=false`,
`auto_delete=true`) with the exact advertised args, and the durable queues:

```
$ docker exec rpng-rabbitmq-1 rabbitmqctl list_exchanges -p analyzer name type durable auto_delete
name       type     durable  auto_delete
analyzer   fanout   false    true

$ docker exec rpng-rabbitmq-1 rabbitmqctl list_exchanges -p analyzer name arguments
analyzer  [{"analyzer","analyzer"},{"analyzer_index",true},{"analyzer_priority",1},
          {"analyzer_log_search",true},{"analyzer_suggest",true},
          {"analyzer_cluster",true},{"version","0.1.0"}]

$ docker exec rpng-rabbitmq-1 rabbitmqctl list_queues -p analyzer name durable messages consumers
name               durable  messages  consumers
analyzer-ng.all    true     0         1
analyzer-ng.dlq    true     0         0
analyzer-ng.train  true     0         1
```

RP's `service-api` connects to the analyzer vhost and later runs analysis against
this analyzer (see §4), which only happens for a registered/available analyzer.

## 2. Import a launch with a failure (RP reporting API)

`scripts/g5/10-report-launch.sh` — login (superadmin), start launch, start a
failing `test_login_timeout` item, attach an ERROR log with a stack trace, finish
item as `ti001` (To Investigate), finish launch:

```
### STEP 0  login ###
access_token acquired (len=477)
### STEP 2  start launch ###
launch uuid=e7e33761-1965-4a5a-a024-2d5888b9e27d
### STEP 3  start a failing test item ###
item uuid=076f4bbb-9e9c-4311-a355-9cc022fdacd6
### STEP 4  attach an ERROR log with a stack trace ###
{ "id": "34b87c8a-e3b5-4647-89c8-26bc13dfde78" }
### STEP 5  finish item as FAILED / To Investigate (ti001) ###
{ "message": "Accepted finish request for test item ID = 076f4bbb-..." }
### STEP 6  finish launch ###
{ "id": "e7e33761-...", "link": ".../ui/#superadmin_personal/launches/all/e7e33761-..." }
```

On launch **finish**, RP sends `index` + `cluster` to analyzer-ng, from
`docker logs rpng-analyzer-1`:

```
INFO ... msg="handled 'index'"    routing_key=index    duration_ms=22
INFO ... msg="handled 'cluster'"  routing_key=cluster  project=1  duration_ms=3
```

Numeric launch id + statistics (RP side):

```
$ curl -s .../api/v1/superadmin_personal/launch/uuid/<uuid>
launch id= 2  status= FAILED
statistics= {'executions': {'total': 1, 'failed': 1},
             'defects': {'to_investigate': {'total': 1, 'ti001': 1}}}
```

## 3. Enable auto-analysis + trigger analysis

`scripts/g5/20-enable-and-analyze.sh`. Auto-analysis config lives in RP **project
attributes** (`analyzer.isAutoAnalyzerEnabled`, `analyzer.autoAnalyzerMode`, …):

```
### STEP 7  enable auto-analysis (project attributes) ###
{ "message": "Project with name = 'superadmin_personal' is successfully updated." }
-- read back --
{'analyzer.autoAnalyzerMode': 'ALL', 'analyzer.isAutoAnalyzerEnabled': 'true', ...}

### STEP 8  trigger auto-analysis on launch 2  (POST .../launch/analyze) ###
{ "message": "autoAnalyzer analysis for launch with ID='2' started." }
```

analyzer-ng handles the `analyze` RPC and replies to RP:

```
INFO ... msg="handled 'analyze'"  routing_key=analyze  project=1  duration_ms=28
```

RP api confirms it drove the analysis through this analyzer:

```
c.e.t.r.c.a.a.impl.AnalyzerServiceImpl  Start analysis of '1' items for launch with id '2'
c.e.t.r.c.a.a.indexer.BatchLogIndexer   Indexing of 1 logs is finished for 1 items.
```

On the **first, cold** launch the analyze/suggest results are correctly **empty**
(a single brand-new failure, no labeled history, nothing above threshold), so RP
leaves the item at `ti001`. This is correct abstain behavior — the analyzer does
not guess. The positive result appears in §5 once a label exists.

## 4. Manual defect change → `label_event` in the analyzer DB

`scripts/g5/…` STEP 9 — operator changes item 2 from `ti001` to `pb001`:

```
### STEP 9  manual defect change ti001 -> pb001 (Product Bug) ###
[ { "issueType": "pb001",
    "comment": "G5: operator marks this a product bug",
    "autoAnalyzed": false, ... } ]
```

RP publishes `defect_update`; analyzer-ng records it:

```
INFO ... msg="handled 'defect_update'"  routing_key=defect_update  project=1  duration_ms=8
```

The append-only feedback row **and** the current-label overwrite are both persisted
(spec 01 §4.5):

```
$ psql analyzer -c "SELECT event_id,project_id,item_id,old_label,new_label,source,ts FROM analyzer.label_event;"
 event_id | project_id | item_id | old_label | new_label |      source      |              ts
----------+------------+---------+-----------+-----------+------------------+-------------------------------
        1 |          1 |       2 | ti001     | pb001     | rp_defect_update | 2026-07-17 02:35:33.279801+00

$ psql analyzer -c "SELECT project_id,item_id,issue_type FROM analyzer.test_item WHERE item_id=2;"
 project_id | item_id | issue_type
------------+---------+------------
          1 |       2 | pb001
```

## 5. Auto-analysis applies a label + "Make Decision" suggestion (the positive case)

`scripts/g5/30-second-launch-suggest.sh` — report a **second** launch with the
**same** SocketTimeoutException failure (item 3), then open Make Decision on it.

**5a. Auto-analysis auto-labels item 3** (`GET /api/v1/{project}/item/3` on the RP side):

```
{ "id": 3, "name": "test_login_timeout", "status": "FAILED",
  "issue": { "issueType": "pb001",
             "comment": "G5: operator marks this a product bug",
             "autoAnalyzed": true,          <-- applied by analyzer-ng
             "ignoreAnalyzer": false } }
```

The defect is now `pb001` with **`autoAnalyzed: true`** — the analyzer matched the
new failure to the labeled item 2 and applied its label. This is the "auto-analysis
visible" half of G5.

**5b. Make Decision suggestions** (`GET /api/v1/{project}/item/suggest/3`) returns a
full `SuggestAnalysisResult` (spec §4.2 — every field present incl. `esScore`,
`esPosition`, `modelInfo`, `usedLogLines`, `minShouldMatch`, `processedTime`,
`methodName`):

```json
[ { "testItemResource": { "id": 2, "issue": { "issueType": "pb001", ... } },
    "logs": [ { "id": 2, "message": "java.net.SocketTimeoutException: Read timed out ...",
                "level": "ERROR" } ],
    "suggestRs": {
      "project": 1, "testItem": 3, "launchId": 3,
      "issueType": "pb001", "relevantItem": 2,
      "matchScore": 95.0, "resultPosition": 0,
      "esScore": 0.0, "esPosition": 0,
      "modelInfo": "analyzer-ng;gbm=none;emb=none;kb_mode=none",
      "usedLogLines": -1, "minShouldMatch": 5, "processedTime": 0.0091,
      "methodName": "auto_analysis",
      "modelFeatureNames": "top1_cosine;top1_rrf;top1_jaccard;...(39 features)",
      "modelFeatureValues": "0.0;0.016;1.0;...;0.2" } } ]
```

Make Decision shows the labeled item 2 (with its log) as the relevant match and
suggests **pb001**, `matchScore 95.0`.

analyzer-ng handled the corresponding RPC:

```
INFO ... msg="handled 'suggest'"  routing_key=suggest  project=1  duration_ms=10
```

## 6. Stats / metrics

```
$ psql analyzer -c "SELECT project_id,test_case_hash,window_runs,window_failures,last_status
                     FROM analyzer.test_history_stats;"
 project_id | test_case_hash | window_runs | window_failures | last_status
------------+----------------+-------------+-----------------+-------------
          1 |    -2118801080 |           4 |               4 | failed

$ psql analyzer -c "SELECT count(*) FROM analyzer.failure_signature;"   -> 2

# /metrics (analyzer_requests_total by routing_key), after the run:
analyzer_requests_total{outcome="success",routing_key="index"}          4.0
analyzer_requests_total{outcome="success",routing_key="cluster"}        2.0
analyzer_requests_total{outcome="success",routing_key="analyze"}        2.0
analyzer_requests_total{outcome="success",routing_key="suggest"}        2.0
analyzer_requests_total{outcome="success",routing_key="defect_update"}  1.0
analyzer_requests_total{outcome="success",routing_key="namespace_finder"} 4.0
```

`namespace_finder` (an obsolete OpenSearch route RP still emits on finish) is a
logged no-op with no reply — deprecated-route handling (spec §4.4) confirmed live.

---

## G5 result

| G5 requirement | Status | Evidence |
|---|---|---|
| Full RP + analyzer-ng up, no OpenSearch/train | PASS | §0 |
| RP recognizes the analyzer (exchange args, RP runs analysis) | PASS | §1, §3 |
| Launch import with failures indexed | PASS | §2 |
| Auto-analysis visible (label applied, `autoAnalyzed:true`) | PASS | §5a |
| Suggestions in Make Decision (full `SuggestAnalysisResult`) | PASS | §5b |
| Manual defect change → `label_event` recorded + stats | PASS | §4, §6 |

**Every G5 sub-goal was demonstrated against a real ReportPortal 5.15 stack.**

### Honesty notes / observations
- **Embeddings inactive in this run.** `modelInfo` reports `emb=none` and the
  suggestion's `top1_cosine=0.0` while `top1_jaccard=1.0`: the ONNX embedder did not
  contribute to scoring; retrieval matched on lexical + error-signature features and
  still produced the correct `pb001`/95.0 result. `/health` also shows
  `emb_model_ver:null` (main.py never wires the loaded model tag into the health
  provider). Neither blocks G5, but confirming ONNX embedder initialization is a
  recommended follow-up (see `docs/OPERATIONS.md` → Troubleshooting).
- **pgvector needs a superuser** on the stock RP postgres — the fully-automatic
  self-create path fails at `CREATE EXTENSION vector`; the pg-init path (run as the
  `postgres` superuser) is required. Details + fix in `docs/INSTALL.md`.
- **A benign `Connection reset by peer`** is logged on each RPC reply. It is RabbitMQ
  closing the `amq.rabbitmq.reply-to` direct-reply pseudo-queue after RP consumes the
  reply; the reply is delivered first (proved by RP's 200s and applied labels). The
  publisher reconnects automatically. No reply was lost in this run.
