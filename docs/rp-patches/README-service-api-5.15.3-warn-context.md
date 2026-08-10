# service-api 5.15.3 — forward near-error WARN context to the analyzer

Closes the **S16 / ADV-1 residual**: three confidently-wrong `pb` auto-decisions in the
mk6 scorecard where one shared `TimeoutException` stack is raised by three different root
causes (slow-query → `pb`, pool-exhausted → `si`, bare timeout → `ti`). The **only**
discriminant is a WARN/INFO context line that *precedes* the ERROR (`SLOW QUERY …` vs
`connection pool exhausted …`). analyzer-ng already folds near-error WARN context into
`msg_text` (kept out of the identity hashes — commit `6af392f`, `extract_context_lines`),
but **RP's service-api never forwards WARN logs**, so the discriminant never arrives in
production. This patch makes RP forward it.

- Patch: [`service-api-5.15.3-warn-context-forward.patch`](./service-api-5.15.3-warn-context-forward.patch)
- Target: `github.com/reportportal/service-api` tag **5.15.3**
- Companion analyzer hardening (this repo): `src/analyzer_ng/core/ingest.py` `_time_ordered_logs`
  (see [Ordering](#ordering-the-non-obvious-part) below) + proof test
  `tests/unit/test_analysis_fanout.py::test_wire_order_independence_via_logtime`.

## Where the ERROR-only filter lives (investigation)

RP loads an item's logs for the analyzer at a **hard-coded `LogLevel.ERROR.toInt()` (40000)**
at every analyzer log-loading site. It is **not** a project setting and **not** configurable:

| Path | Site (service-api 5.15.3) | Filter |
|---|---|---|
| **Index** (standard) | `core/analyzer/auto/impl/preparer/StandardTestItemPreparerService#getLogsMapping` | `…LogLevelGte(…, LogLevel.ERROR.toInt())` |
| **Index** (legacy) | `…/preparer/TestItemPreparerServiceImpl#getLogsMapping` | same |
| **Index** (retries) | `…/preparer/TestItemWithRetriesPreparerService` | `logRepository.findNestedLogsOfRetryItem(id, LogLevel.ERROR.toInt())` |
| Suggest | `core/analyzer/auto/impl/SuggestItemService#prepareSuggestRq` | `…LogLevelGte(…, ERROR_INT)` |

The underlying DB query is a `>= :logLevel` filter (`commons-dao` 5.15.4
`LogRepositoryCustomImpl#findAllIndexUnderTestItemByLaunchIdAndTestItemIdsAndLogLevelGte`,
and the `findNestedLogsOfRetryItem` native query) — it already selects `log_time` and is
level-parameterised. So **the level is entirely the caller's choice**; lowering it needs no
DAO/model change.

Why config alone can't fix it: analyzer-ng's `AnalyzerConfig.minimumLogLevel` exists but is
consumed *analyzer-side* only, defaults to ERROR, and RP never sets it below ERROR — and even
if it did, the DB fetch above is hard-capped at ERROR before the payload is built. The WARN
line is dropped in RP before AMQP, so no analyzer-side setting can recover it.

## The change

Forward **WARN+** (`LogLevel.WARN.toInt()`, 30000) for the index paths, then **bound and
order** the result in `AnalyzerUtils.withNearErrorWarnContext(logs, CONTEXT_LOOKBACK_LINES)`:

- Keep **every ERROR+ log** (the failure identity — unchanged from today; FATAL 50000 included).
- Keep the **last `CONTEXT_LOOKBACK_LINES` (=5) WARN lines that chronologically precede the
  first ERROR** — nothing else. This matches analyzer-ng's own `CONTEXT_LOOKBACK_LINES` window
  (`preprocessing/pipeline.py`) so the payload never carries more WARN than the analyzer will use.
- Items with **no ERROR log stay unindexed** (helper returns empty → the existing
  `isNotEmpty` filter drops them), preserving legacy behaviour.

Result: the payload grows by at most 5 short WARN lines per failed item, and only for items
that actually have preceding WARN context.

### Ordering (the non-obvious part)

`IndexTestItem.logs` is a `Set<IndexLog>` (RP `commons` model). Today the preparers wrap the
DAO result in a **`HashSet`**, and the DAO query has **no `ORDER BY`** — so log order on the
wire is arbitrary. analyzer-ng's `extract_context_lines` is **positional** (it takes WARN
lines appearing *before* the first ERROR in the array), so a naive "just lower the level"
change would forward WARN logs in random positions and **silently drop the discriminant**
about half the time.

The patch fixes this **at the source**: `withNearErrorWarnContext` returns a **`LinkedHashSet`
in chronological order** (by `logTime`, `logId` breaking ties — both already populated by the
index DAO query, which selects `log.log_time`). Jackson serializes a `Set`-typed field in the
concrete collection's iteration order, so an ordered `LinkedHashSet` yields a **time-ordered
JSON `logs` array** without any model change.

Belt-and-suspenders on the analyzer side: `ingest.py` now sorts each item's logs by `logTime`
before processing (`_time_ordered_logs`), so the analyzer no longer depends on wire order *at
all* — it is robust even against a stock/other forwarder that emits an unordered `Set`. The
sort is stable, so synthetic payloads with tied (defaulted) timestamps keep their wire order
and no existing behaviour changes. RP already serializes `logTime` as a 7-int array
(`LocalDateTimeSerializer`, "Required for compatibility with analyzer"), which analyzer-ng's
`Log.logTime` already parses — so this needs no new field on the wire.

### Files changed (service-api tree)

- `core/analyzer/auto/impl/AnalyzerUtils.java` — new `CONTEXT_LOOKBACK_LINES` constant + static
  `withNearErrorWarnContext(...)` helper.
- `…/preparer/StandardTestItemPreparerService.java` — fetch WARN+, apply the helper.
- `…/preparer/TestItemPreparerServiceImpl.java` — same (legacy strategy).
- `…/preparer/TestItemWithRetriesPreparerService.java` — same, for the retry nested-log path.

The **suggest** path (`SuggestItemService`) is intentionally left ERROR-only: the S16 residual
is driven by *indexed* items' stored `failure_signature` (auto-analysis reads the index), not by
suggest. Extending suggest is a small follow-up (change `ERROR_INT` → `WARN_INT` and route the
`SuggestInfo` logs through the same helper); note its `IndexLog`s come via `AnalyzerUtils.fromLogs`,
whose `TO_INDEX_LOG` mapper does **not** currently set `logTime`, so that follow-up should also
copy `log.getLogTime()` there (or rely on the analyzer-side `logTime` sort, which no-ops when
`logTime` is absent).

## How to apply & build

```bash
git clone --branch 5.15.3 https://github.com/reportportal/service-api.git
cd service-api
git apply /path/to/service-api-5.15.3-warn-context-forward.patch   # applies cleanly (verified)
./gradlew build            # standard service-api build (JDK 17/21, Gradle wrapper)
```

Image: build the standard service-api image (`./gradlew buildDocker` / the repo Dockerfile).
Unlike the service-ui patch, there is **no thin-overlay shortcut** — a JVM service ships
compiled classes inside the jar, so the jar must be rebuilt. On minikube, side-load with a
**unique** tag and set the image (mirrors the service-ui rollout):

```bash
minikube image load reportportal/service-api:5.15.3-ng1
kubectl set image deployment/reportportal-api api=reportportal/service-api:5.15.3-ng1
kubectl rollout status deployment/reportportal-api
```

## Live rebuild feasibility on the stand — honest assessment

**Not done on the stand — infeasible in this environment; patch + verification plan delivered
instead.** Rebuilding service-api is far heavier than the service-ui overlay we ship:

- It is a Spring Boot / Gradle service whose build resolves `commons-bom`, `commons`, and
  `commons-dao:5.15.4` and (for the DAO) can require jOOQ codegen against a Postgres schema —
  minutes-long and network-heavy.
- The minikube VM has broken DNS (images must be side-loaded) and a **tight disk** — the memory
  notes even UI images use thin overlays and full rebuilds risk ENOSPC. A JVM service image is
  larger and cannot use the class-copy overlay trick.

The patch itself is validated by: (1) it **applies cleanly** to the pristine `5.15.3` tag
(`git apply --check` ✓); (2) it is type-checked by inspection against the *actual* upstream
APIs read from `commons`/`commons-dao` (`IndexLog#getLogTime/#getLogId/#getLogLevel`,
`LogLevel.WARN_INT`/`ERROR_INT`, the level-parameterised repository queries); (3) the analyzer
end is proven ready end-to-end (below).

## Analyzer-readiness proof (only the RP forwarding was missing)

The analyzer side is demonstrated ready **without** a patched RP, by feeding it a WARN-bearing
payload directly through the real ingest pipeline:

- `tests/unit/test_analysis_fanout.py::test_same_hash_divergent_context_splits_pb_si_abstain`
  — the full S16 family (shared `TimeoutException`, divergent WARN context) is indexed and
  analyzed through `IndexPipeline`/the decision engine: the pool-exhausted member decides `si`
  (not the representative's `pb`), the bare member abstains, and the slow-query twin inherits
  `pb`. i.e. once the WARN context is in the payload, the discriminant separates the triplet.
- `tests/unit/test_analysis_fanout.py::test_wire_order_independence_via_logtime` (**new**) —
  indexes the same family with logs **deliberately scrambled on the wire** (ERROR before its
  context, as RP's `Set` would emit) but with realistic `logTime`, and asserts the resulting
  `msg_text` is **byte-identical** to the in-order run, with the discriminant intact. This
  proves the analyzer recovers order from `logTime` and is ready for RP's `Set`-ordered
  forwarding.

Full suite after the change: **647 passed, 1 skipped**; the 2 remaining failures
(`test_golden_signatures` tail-token drift; `test_feature_total_is_data_driven` asserting a
stale `39` vs the current `47` features) are **pre-existing** and unrelated (confirmed by
re-running with this change stashed); the 91 errors are the known local testcontainers/Ryuk
integration skips.

## Stand verification plan (run once the patched image is deployed)

1. Deploy `reportportal/service-api:5.15.3-ng1`; confirm `reportportal-api` healthy.
2. Pick the S16 family in `migrated-project` (the pool-exhausted `B_pool` items that were
   confidently-wrong `pb` in score-mk6).
3. Re-index: `PUT /api/v1/project/migrated-project/index`, then trigger analyze per launch
   (the reindex playbook in memory). Because the patch forwards WARN, the stored
   `failure_signature.msg_text` for the pool items now carries the `pool exhausted` tokens.
4. In the inspector, open a `B_pool` item and confirm: same `error_hash`/`exception_fp` as the
   slow-query members (identity unchanged), but `msg_text` now diverges (pool context folded in).
5. Confirm the decision flips: `B_pool` items decide/suggest `si` (pool-exhausted history),
   **not** `pb`. Re-run `demo-data/score.py`: the 3 S16 residual confidently-wrong should reach
   **0** (target).
6. Sanity: a normal ERROR-only failure elsewhere is unchanged (payload identical to before);
   payload size per item grows by ≤5 short WARN lines only where WARN context exists.
