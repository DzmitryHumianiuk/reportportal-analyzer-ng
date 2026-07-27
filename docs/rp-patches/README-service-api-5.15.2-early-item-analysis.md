# service-api 5.15.2 — early per-item auto-analysis trigger

The ReportPortal half of early per-item auto-analysis (`docs/EARLY-ITEM-AA.md`
in this repo). With this patch, a test item that finishes as FAILED while its
launch is still running is auto-analyzed a few seconds later, whenever auto
analysis is enabled on the project.

- Patch: [`service-api-5.15.2-early-item-analysis.patch`](./service-api-5.15.2-early-item-analysis.patch)
- Applies to: `reportportal/service-api`, tag `5.15.2` (init the `api-registry`
  submodule before building)
- Needs analyzer-ng with the `analyze_item_early` route and
  `ANALYZER_EARLY_ITEM_ANALYSIS=true`

## What it changes

Stock 5.15 already ships the machinery: `TestItemFinishedEvent` reaches
`TestItemAutoAnalysisRunner`, which indexes the item's logs and runs the
analyzer on a single-item launch. It just fires only for items carrying the
system attribute `immediateAutoAnalysis=true`, and it publishes to the normal
`analyze` route where every confident answer is applied.

The patch makes three changes, all inside that existing shape:

1. **Trigger condition.** The runner now fires for every finished To
   Investigate item (not ignored by the analyzer) when the project's auto
   analysis setting is on. The `immediateAutoAnalysis` attribute path keeps
   working on its own, unchanged.
2. **Debounce.** The analysis is scheduled about 2 seconds after the finish
   request instead of running inline, so log entries still sitting in the
   reporting queue land first and the analyzer sees the complete item. Override
   with the `EARLY_ITEM_ANALYSIS_DEBOUNCE_MS` environment variable.
3. **Routing key.** The request goes to `analyze_item_early` instead of
   `analyze`. On that route the analyzer applies only deterministic answers
   (exact-hash inherit, knowledge-base match, and optionally high-confidence
   Product Bug); everything else is stored as a suggestion. The launch-finish
   analysis pass is untouched and stays the authority.

Files touched: `TestItemAutoAnalysisRunner`, `AnalyzerService(+Impl)`,
`AnalyzerServiceClient(+Impl)`. No schema or API changes; replies reuse the
stock apply-label path, so early labels look exactly like normal auto-analysis
labels (`autoAnalyzed=true`) and can be revised by the launch-finish pass.

## Build (host build + thin overlay, same trick as the service-ui patch)

```bash
git clone --branch 5.15.2 https://github.com/reportportal/service-api.git
cd service-api
git submodule update --init --depth 1
git apply /path/to/service-api-5.15.2-early-item-analysis.patch

# JDK 21 required
./gradlew -x test "-Porg.gradle.java.installations.paths=$JDK21_HOME" bootJar

# Thin overlay over the stock image (jar swap, no in-image gradle build)
cat > Dockerfile.ng <<'EOF'
FROM reportportal/service-api:5.15.2
COPY build/libs/service-api-*exec.jar /usr/app/
EOF
minikube image build -f Dockerfile.ng -t reportportal/service-api:5.15.2-ng1 .
kubectl set image deployment/reportportal-api api=reportportal/service-api:5.15.2-ng1
```

Note: the stock image starts `java -jar /usr/app/service-api-*exec.jar`; the
overlay must not leave two jars matching that glob. The stock jar is named
`service-api-5.15.2-exec.jar`; a locally built jar usually is too, so the copy
replaces it. Check with `kubectl exec <pod> -- ls /usr/app` after rollout.
