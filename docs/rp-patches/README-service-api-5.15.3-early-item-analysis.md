# service-api 5.15.3 — early per-item auto-analysis trigger

Ported onto the 5.15.3 analyze contract (2026-08-10). Upstream made the
analyze route fire-and-forget: the request is sent with `convertAndSend`, and
the results come back through a reply exchange (`analyzer-reply` /
`analysis.matches`) consumed by `AnalysisResultConsumer` and applied by
`AnalysisResultHandler` (last-write-wins). The early trigger rides the same
contract now: `analyzeItemEarly` dispatches to the `analyze_item_early` route
the same fire-and-forget way, and the results are applied by the same
consumer. The analyzer must speak the result-queue contract: analyzer-ng
publishes analyze results to that exchange whenever the request arrives
without `reply_to` (see the analyzer's `amqp_result_exchange` /
`amqp_result_routing_key` settings; defaults match the service-api ones).

The ReportPortal half of early per-item auto-analysis (`docs/EARLY-ITEM-AA.md`
in this repo). With this patch, a test item that finishes as FAILED while its
launch is still running is auto-analyzed a few seconds later, whenever auto
analysis is enabled on the project.

The patch also makes the launch-finish analyze request carry
`launchItemsCount` (analyzer-ng issue #4), so the analyzer's
`launch_fail_fraction` feature gets a live denominator. See
"launchItemsCount on the analyze route" below.

- Patch: [`service-api-5.15.3-early-item-analysis.patch`](./service-api-5.15.3-early-item-analysis.patch)
- Applies to: `reportportal/service-api`, tag `5.15.3` (init the `api-registry`
  submodule before building)
- Needs analyzer-ng with the `analyze_item_early` route AND the result-queue
  publishing (mk34+); on older analyzers the 5.15.3 service-api applies no
  analyze results at all, launch-finish included
- The patch also fixes the failure mode it was debugged through: stock
  `ProjectConfigDelegatingSubscriber` swallows every handler exception at
  DEBUG with no stack trace, so a failed early pass looks identical to one
  that never fired. Patched builds log the handler and event names with the
  stack trace at ERROR (one handler failing still does not stop the others).
  On stock builds the old workaround is
  `LOGGING_LEVEL_COM_EPAM_TA_REPORTPORTAL_CORE_EVENTS_SUBSCRIBER=DEBUG`.
- Operational note (any 5.15.x): the reporting queues are per-instance
  auto-delete (`q.reporting.<instance>.N`). With a RollingUpdate deployment
  the new pod can subscribe to the dying pod's queues and lose them when it
  exits, leaving reporting with no queues until the next restart. Run the api
  deployment with `strategy: Recreate` (the stand does now).

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
`AnalyzerServiceClient(+Impl)`, `LaunchPreparerServiceImpl`,
`ProjectConfigDelegatingSubscriber` (error visibility), and the new
`IndexLaunchNg` model class. No schema or API changes; replies reuse the
stock apply-label path, so early labels look exactly like normal auto-analysis
labels (`autoAnalyzed=true`) and can be revised by the launch-finish pass.

## launchItemsCount on the analyze route

Analyzer-ng's GBM feature `launch_fail_fraction` divides the number of
To Investigate items in the analyze payload by the total number of executed
test items in the launch. Stock service-api never sends that total, so the
analyzer treated it as unknown and the feature stayed 0. This patch adds it:

- **Definition.** `launchItemsCount` is the launch's executions total (the
  `statistics$executions$total` counter, the same number the launch view
  shows as the executions count). At launch finish this equals the number of
  executed leaf test items (steps with statistics), retries collapsed, which
  is exactly the denominator the analyzer expects.
- **Wire shape.** The `IndexLaunch` model class lives in the
  `com.github.reportportal:commons` dependency jar, so the field cannot be
  added to it directly. Instead `LaunchPreparerServiceImpl` builds a
  service-api-local subclass, `IndexLaunchNg`, with a
  `@JsonProperty("launchItemsCount") @JsonInclude(NON_NULL) Long` field. The
  analyzer messages are serialized by Jackson from the runtime type, so the
  field reaches the wire naturally; when unset, the payload is byte-identical
  to stock, and legacy analyzers ignore the extra key when present.
- **Where filled.** `AnalyzerServiceImpl.analyzeItemsPartition` sets the
  count on the launch-finish / on-demand `analyze` route only, reading it
  from the launch entity's statistics. The statistics collection is eagerly
  fetched on the Launch entity (`FetchType.EAGER`), so this costs no extra
  repository query and is safe on detached instances. The count is set only
  when positive.
- **Early route.** The early per-item `analyze_item_early` route leaves the
  count absent on purpose: mid-launch the executions total is still growing,
  so any value would be a wrong denominator, and the analyzer intentionally
  treats the early route's total as unknown (`launch_fail_fraction` stays 0
  there by design).
- No dependency source was changed; the whole addition lives in the
  service-api tree and is part of this single patch file.

Note: the stock unit test `TestItemAutoAnalysisRunnerTest` is not updated for
the new constructor and trigger split; the build below skips tests
(`-x test`).

## Build (host build + thin overlay, same trick as the service-ui patch)

```bash
git clone --branch 5.15.3 https://github.com/reportportal/service-api.git
cd service-api
git submodule update --init --depth 1
git apply /path/to/service-api-5.15.3-early-item-analysis.patch

# JDK 21 required
./gradlew -x test "-Porg.gradle.java.installations.paths=$JDK21_HOME" bootJar

# Thin overlay over the stock image (jar swap, no in-image gradle build).
# The repo's .dockerignore excludes build/, so build from a minimal context.
mkdir -p /tmp/service-api-ng-ctx
cp build/libs/service-api-5.15.3-exec.jar /tmp/service-api-ng-ctx/
cat > /tmp/service-api-ng-ctx/Dockerfile.ng <<'EOF'
FROM reportportal/service-api:5.15.3
COPY service-api-5.15.3-exec.jar /usr/app/
EOF
minikube image build -f Dockerfile.ng -t reportportal/service-api:5.15.3-ng1 /tmp/service-api-ng-ctx
kubectl set image deployment/reportportal-api api=reportportal/service-api:5.15.3-ng1
```

Note: the stock image starts `java -jar /usr/app/service-api-*exec.jar`; the
overlay must not leave two jars matching that glob. The stock jar is named
`service-api-5.15.3-exec.jar`; a locally built jar usually is too, so the copy
replaces it. Check with `kubectl exec <pod> -- ls /usr/app` after rollout.
