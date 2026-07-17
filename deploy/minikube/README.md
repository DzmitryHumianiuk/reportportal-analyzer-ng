# ReportPortal on minikube with analyzer-ng as THE analyzer

This directory deploys ReportPortal on a local minikube cluster with the stock
`service-auto-analyzer` (OpenSearch-based) replaced by **analyzer-ng** (PostgreSQL
+ pgvector). analyzer-ng gets its own `pgvector/pgvector:pg16` database; ReportPortal
keeps its own bitnami PostgreSQL for application data.

```
                 rabbitmq (exchange: analyzer-default, vhost: analyzer)
                     ▲                         │
   service-api ──────┘                         ▼
                                        reportportal-analyzer (analyzer-ng image)
                                                 │
                                                 ▼
                                        analyzer-pg  (pgvector/pgvector:pg16)
```

## Files

| File | Purpose |
|------|---------|
| `analyzer-pg.yaml` | pgvector PostgreSQL for analyzer-ng: Secret + PVC (2Gi) + Deployment + Service `analyzer-pg` (namespace `default`). |
| `values-analyzer-ng.yaml` | Helm overlay: repoints the analyzer StatefulSet at the analyzer-ng image, injects `ANALYZER_PG_*` env pointing at `analyzer-pg`, and (for this registry-blocked minikube) forces `pullPolicy: IfNotPresent` on the RP services. |
| `scripts/` | G5 end-to-end scenario adapted from `scripts/g5/` to talk to port-forwarded k8s Services. |
| `VERIFY.md` | Recorded end-to-end verification transcript. |

## What the values file overrides and why

* **`serviceanalyzer.image` + `pullPolicy: Never`** — the chart's analyzer
  StatefulSet has no enable flag, it always renders. We simply repoint its image
  at the locally-loaded `docker.io/library/analyzer-ng:latest` and forbid pulling.
* **`serviceanalyzer.extraEnvs`** — points analyzer-ng at its own pgvector store
  (`ANALYZER_PG_HOST=analyzer-pg`, password from the `analyzer-pg` Secret,
  `ANALYZER_PG_CREATE_DB=true`). The chart already injects the legacy
  `AMQP_URL` / `AMQP_EXCHANGE_NAME=analyzer-default` / `AMQP_VIRTUAL_HOST` env and
  the (ignored) `ES_*` / `DATASTORE_*` env; analyzer-ng reads the AMQP vars
  natively and WARN-ignores the ES/datastore vars by design.
* **probes** — the chart's readiness/liveness GET `/` on 5001; analyzer-ng serves
  `/` (200 when PostgreSQL is healthy), so the path is satisfied unchanged. We
  only relax the timings for the ONNX embedder cold-start.
* **`<service>.pullPolicy: IfNotPresent`** — see the registry note below.

There is only ONE analyzer workload in the chart (`analyzer-statefulset.yaml`,
`replicas: 1`). There is **no** `analyzer-train` statefulset/deployment, so
nothing runs the legacy train image.

## Registry note (this minikube)

This minikube VM cannot reach `registry-1.docker.io` (its upstream DNS refuses),
so **no image can be pulled at run time**. Every required image is side-loaded
with `minikube image load` (below). Because the chart ships `pullPolicy: Always`
for the RP services, the values file overrides those to `IfNotPresent` so kubelet
uses the side-loaded images. On a cluster with working registry access these
overrides are unnecessary.

## Prerequisites

* minikube running (this env: `--driver=vfkit --cpus=4 --memory=8192`, k8s 1.35).
* `kubectl`, `helm` v3+ on PATH.
* The analyzer-ng image built and loaded:
  `docker build -t analyzer-ng:latest . && minikube image load analyzer-ng:latest`
* The RP Helm chart with dependencies built (see below).

## Deploy

```sh
# 0. from the analyzer-ng repo root
cd /path/to/analyzer-ng

# 1. build the chart's subchart dependencies (postgresql, rabbitmq, opensearch,
#    minio). The upstream clone ships no charts/ dir. Copy the chart so the clone
#    stays pristine, then build deps into the copy:
cp -R /path/to/reportportal-kubernetes/reportportal /tmp/rp-chart
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add opensearch https://opensearch-project.github.io/helm-charts
helm dependency build /tmp/rp-chart

# 2. side-load every image the deploy needs (this minikube can't pull).
#    See scripts/preload-images.sh for the exact list; each is
#    `docker pull <img> && minikube image load <img>`.
bash deploy/minikube/scripts/preload-images.sh

# 3. deploy analyzer-ng's pgvector database
kubectl apply -f deploy/minikube/analyzer-pg.yaml

# 4. install ReportPortal with the analyzer-ng swap
helm install reportportal /tmp/rp-chart \
  -f deploy/minikube/values-analyzer-ng.yaml \
  --set uat.superadminInitPasswd.password=superadmin \
  --set storage.type=filesystem \
  --set minio.install=false \
  --set opensearch.install=false \
  --set ingress.enable=false \
  --timeout 10m
```

`ingress.enable=false` is set because this minikube's ingress-nginx addon is in
ImagePullBackOff and its admission webhook would reject the Ingress object; we use
`kubectl port-forward` instead. If your ingress controller is healthy you may omit
that flag and reach everything through the single ingress host.

Watch it come up:

```sh
kubectl get pods -w
```

## Access (port-forwards)

```sh
kubectl port-forward svc/reportportal-uat       9999:9999  &   # /uat/... auth
kubectl port-forward svc/reportportal-api        8585:8585  &   # /api/...  API
kubectl port-forward svc/reportportal-ui         8081:8080  &   # web UI (optional)
kubectl port-forward svc/reportportal-analyzer   5001:5001  &   # analyzer /health
kubectl port-forward svc/reportportal-rabbitmq  15672:15672 &   # rabbit mgmt
```

* API base: `http://localhost:8585/api` (context path `/api`)
* Auth base: `http://localhost:9999/uat` (context path `/uat`)
* Superadmin login: `superadmin` / `superadmin`
* analyzer health: `curl http://localhost:5001/health`
* rabbit mgmt: `http://localhost:15672` (`rabbitmq` / `rabbitmqpassword`)

## End-to-end scenario

With the uat + api port-forwards up:

```sh
deploy/minikube/scripts/10-report-launch.sh        # report a failing launch, auto-analyze on finish
deploy/minikube/scripts/20-enable-and-analyze.sh   # enable AA, trigger analyze, read back autoAnalyzed
deploy/minikube/scripts/30-second-launch-suggest.sh # 2nd launch, Make Decision suggestions
```

Verify analyzer state directly in pgvector:

```sh
kubectl exec deploy/analyzer-pg -- \
  psql -U analyzer -d analyzer -c \
  "select id, project_id, test_item_id, old_type, new_type, source from analyzer.label_event order by id desc limit 5;"
```

## Teardown

```sh
helm uninstall reportportal
kubectl delete -f deploy/minikube/analyzer-pg.yaml     # includes the 2Gi PVC
# stop background port-forwards
pkill -f 'kubectl port-forward'
```

## Optional: ingress instead of port-forwards

If your minikube ingress addon is healthy (`minikube addons enable ingress`),
drop `--set ingress.enable=false`, then add a hosts entry and browse the single
host:

```sh
echo "$(minikube ip)  reportportal.local" | sudo tee -a /etc/hosts
# then helm install ... --set ingress.hosts='{reportportal.local}'
```
All of `/ui`, `/uat`, `/api` are routed off that one host by the chart's Ingress.
