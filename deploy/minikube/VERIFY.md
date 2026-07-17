# analyzer-ng on minikube — end-to-end verification transcript

Recorded 2026-07-17 on minikube v1.38.1 (vfkit, 4 CPU / 8 GB, k8s 1.35.1),
ReportPortal chart 26.5.28 (appVersion 26.0.3), analyzer-ng image
`docker.io/library/analyzer-ng:latest` (VERSION 0.1.0).

## Summary

| Check | Result |
|-------|--------|
| analyzer-ng discovered by ReportPortal as THE analyzer | **YES** — `analyzer-default` exchange advertises its capabilities |
| Pod Ready | **YES** — `reportportal-analyzer-0` 1/1 Running |
| Migrations 1..5 applied | **YES** |
| Seed KB loaded | **YES** — 50 failure modes |
| Embedder loaded | **NO — documented degrade** (emb=none). Root cause: the pre-loaded image predates the current `develop` embedder wiring. Model itself loads fine. See below. |
| PostgreSQL / pgvector | **YES** — pgvector 0.8.5, schema `analyzer`, 5 migrations |
| AMQP + PG health | **YES** — `/health` `pg:true, amqp:true, ready:true` |
| Auto-analysis pipeline | **YES** — index / cluster / analyze / defect_update all handled |
| Make Decision suggestion | **YES** — suggests `pb001`, matchScore 95.0 |
| Manual defect change → `label_event` row | **YES** — `ti001→pb001`, source `rp_defect_update` |

## 1. Pods

```
analyzer-pg-79749b479f-txmz5          1/1     Running     (pgvector/pgvector:pg16)
reportportal-analyzer-0               1/1     Running     (analyzer-ng:latest)
reportportal-api-56d7698f67-jbbqt     1/1     Running
reportportal-index-798b6d8955-7t4sv   1/1     Running
reportportal-jobs-7654df584-28727     1/1     Running
reportportal-migrations-6vrs2         0/1     Completed
reportportal-postgresql-0             1/1     Running
reportportal-rabbitmq-0               1/1     Running
reportportal-uat-6f48856fbf-vcznt     1/1     Running
reportportal-ui-76788f797d-9k54s      1/1     Running
```

## 2. Analyzer startup logs (kubectl logs reportportal-analyzer-0)

```
Starting analyzer-ng 0.1.0
HTTP server listening on :5001
Applying migration 0001_init.sql .. 0005_metrics_daily_ext.sql
Applied 5 migration(s): [1, 2, 3, 4, 5]
Migrations applied this start: 5
Seed KB loaded: 50 failure modes
AMQP connection established
Exchange 'analyzer-default' declared
Consumer 'train' consuming queue 'analyzer-ng.train'
Consumer 'all' consuming queue 'analyzer-ng.all'
analyzer-ng 0.1.0 ready (emb=None, gbm=None)
```

## 3. /health (port-forward pod/reportportal-analyzer-0 5001:5001)

```json
{"live":true,"ready":true,"pg":true,"amqp":true,
 "emb_model_ver":null,"gbm_model_ver":null,"version":"0.1.0",
 "metrics":{"suggestions":0,"accepted":0,"abstained":0,"auto_labeled":0,...}}
```

## 4. RabbitMQ exchange (proves ReportPortal <-> analyzer discovery)

`GET /api/exchanges/analyzer/analyzer-default` (mgmt, rabbitmq/rabbitmqpassword):

```json
{
  "name": "analyzer-default", "vhost": "analyzer", "type": "fanout",
  "arguments": {
    "analyzer": "analyzer-default",
    "analyzer_index": true, "analyzer_log_search": true,
    "analyzer_suggest": true, "analyzer_cluster": true,
    "analyzer_priority": 1, "version": "0.1.0"
  }
}
```
ReportPortal's service-api reads these exchange arguments to discover the
analyzer's name and capabilities — so analyzer-ng IS registered as THE analyzer.

## 5. End-to-end scenario (scripts/)

* **Login** superadmin/superadmin via `/uat/sso/oauth/token` → access_token (len 477).
* **Project**: used the default `superadmin_personal`.
* **Enable auto-analysis** (`PUT /api/v1/project/superadmin_personal`,
  `analyzer.isAutoAnalyzerEnabled=true`, mode ALL) → "successfully updated";
  read-back confirms `analyzer.isAutoAnalyzerEnabled=true`.
* **Launch 1** with a failing `test_login_timeout` + `SocketTimeoutException`
  ERROR log, finished FAILED/ti001. `POST /api/v1/.../launch/analyze` →
  "autoAnalyzer analysis for launch with ID='1' started." Analyzer handled
  `index`, `cluster`, `analyze`. autoAnalyzed stayed **false** — correct: no
  prior real-defect history to match on the first launch.
* **Manual defect change** `ti001 → pb001` (`PUT /api/v1/.../item`,
  comment "manual: product bug"). Analyzer handled `defect_update` (13 ms).
* **`label_event` row in analyzer-pg** (`kubectl exec deploy/analyzer-pg -- psql`):

  ```
  event_id | project_id | item_id | old_label | new_label |      source
  ---------+------------+---------+-----------+-----------+------------------
         1 |          1 |       1 | ti001     | pb001     | rp_defect_update
  ```

* **Launch 2**, same failure signature. **Make Decision**
  (`GET /api/v1/.../item/suggest/2`) returned a suggestion:

  ```
  issueType     = pb001         (matches the manually-labeled item 1)
  relevantItem  = 1
  matchScore    = 95.0
  methodName    = auto_analysis
  modelInfo     = analyzer-ng;gbm=none;emb=none;kb_mode=none
  top features  = top1_jaccard=1.0, same_test_case_top1=1.0,
                  same_error_hash_top1=1.0, same_exception_fp_top1=1.0
  ```

  The correct `pb001` suggestion is produced from exact error-hash / Jaccard /
  same-test-case features even with dense retrieval disabled.

## 6. Embedder degrade — root cause (NOT a runtime failure)

`/health` and every `suggestRs.modelInfo` report `emb=none`. Investigation:

* The ONNX model is present in the image
  (`/opt/analyzer/models/e5-small-int8/model.onnx` 118 MB + tokenizer) and
  **loads + embeds fine** when driven directly inside the pod:
  `Embedder(...).embed('...')` → 384-dim vector, tag `e5s-int8-r614241f6`.
* But the running image's `AnalyzerService` exposes **only `set_pg_pool`** — it
  has no `_resolve_embedder` / `_load_embedder` / `_bind_handlers` methods. The
  current `develop` source DOES (`grep -c _resolve_embedder src/.../service.py`
  → 2). So the pre-loaded `analyzer-ng:latest` was built from an **older source
  revision** that never wired the embedder into startup.

**Impact**: dense (vector) retrieval is disabled; the analyzer runs lexical /
feature-only. Functionally the pipeline still auto-analyzes and suggests
correctly (see §5). **Fix**: rebuild the image from current `develop` and
reload — `docker build -t analyzer-ng:latest . && minikube image load
analyzer-ng:latest` — then restart `reportportal-analyzer-0`; `/health` should
then report `emb_model_ver: e5s-int8-r614241f6`.
