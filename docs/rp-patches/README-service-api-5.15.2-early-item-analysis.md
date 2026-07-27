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

The patch makes four changes, all inside that existing shape:

1. **Trigger condition, split by route.** The runner now has two separate
   triggers:
   - Early path: when the project's auto analysis setting is on, every
     finished To Investigate item (not ignored by the analyzer) is analyzed
     through the `analyze_item_early` route. On that route the analyzer
     applies only deterministic answers (exact-hash inherit, knowledge-base
     match, and optionally high-confidence Product Bug); everything else is
     stored as a suggestion. The launch-finish analysis pass is untouched and
     stays the authority.
   - Stock path: an item carrying the system attribute
     `immediateAutoAnalysis=true` (with the project setting off) goes through
     the normal `analyze` route where every confident answer is applied,
     exactly as unpatched 5.15 did. So a stock analyzer, or analyzer-ng with
     `ANALYZER_EARLY_ITEM_ANALYSIS=false`, still serves this attribute.
   When both triggers match, the early path wins.
2. **Debounce.** The analysis is scheduled about 2 seconds after the finish
   request instead of running inline, so log entries still sitting in the
   reporting queue land first and the analyzer sees the complete item. Override
   with the `EARLY_ITEM_ANALYSIS_DEBOUNCE_MS` environment variable. Both paths
   share the debounce (a small deviation from stock, which ran the attribute
   trigger inline).
3. **Fire-time re-check.** When the debounced task fires, the item is reloaded
   from the database and the analysis is skipped unless the item still has a
   To Investigate issue that is not ignored by the analyzer. A label applied
   in the meantime by the launch-finish pass or by a human is never
   overwritten by a backlogged early task.
4. **Launch-state guards (early path only).** The early path also requires
   the launch to still be IN_PROGRESS and not in DEBUG mode at fire time; a
   debug launch is never early-analyzed. The stock attribute path applies
   neither launch guard, matching unpatched 5.15, which ran the attribute
   trigger with no launch checks (with asynchronous reporting the item finish
   event can be processed after the launch already finished).

Files touched: `TestItemAutoAnalysisRunner`, `AnalyzerService(+Impl)`,
`AnalyzerServiceClient(+Impl)`. No schema or API changes; replies reuse the
stock apply-label path, so early labels look exactly like normal auto-analysis
labels (`autoAnalyzed=true`) and can be revised by the launch-finish pass.

Note: the stock unit test `TestItemAutoAnalysisRunnerTest` is not updated for
the new constructor and trigger split; the build below skips tests
(`-x test`).

## Build (host build + thin overlay, same trick as the service-ui patch)

```bash
git clone --branch 5.15.2 https://github.com/reportportal/service-api.git
cd service-api
git submodule update --init --depth 1
git apply /path/to/service-api-5.15.2-early-item-analysis.patch

# JDK 21 required
./gradlew -x test "-Porg.gradle.java.installations.paths=$JDK21_HOME" bootJar

# Thin overlay over the stock image (jar swap, no in-image gradle build).
# The repo's .dockerignore excludes build/, so build from a minimal context.
mkdir -p /tmp/service-api-ng-ctx
cp build/libs/service-api-5.15.2-exec.jar /tmp/service-api-ng-ctx/
cat > /tmp/service-api-ng-ctx/Dockerfile.ng <<'EOF'
FROM reportportal/service-api:5.15.2
COPY service-api-5.15.2-exec.jar /usr/app/
EOF
minikube image build -f Dockerfile.ng -t reportportal/service-api:5.15.2-ng2 /tmp/service-api-ng-ctx
kubectl set image deployment/reportportal-api api=reportportal/service-api:5.15.2-ng2
```

Note: the stock image starts `java -jar /usr/app/service-api-*exec.jar`; the
overlay must not leave two jars matching that glob. The stock jar is named
`service-api-5.15.2-exec.jar`; a locally built jar usually is too, so the copy
replaces it. Check with `kubectl exec <pod> -- ls /usr/app` after rollout.
