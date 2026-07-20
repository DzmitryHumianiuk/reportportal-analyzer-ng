# analyzer-ng demo-data generator

Compiles [`SCENARIOS.md`](SCENARIOS.md) (the authoritative timeline + scenario
spec) and [`corpus/*.json`](corpus/) (per-framework failure archetypes) into
concrete ReportPortal launches / items / logs and uploads them to the live
stand via the RP **async v2 reporting API**, with client-supplied **back-dated**
timestamps so the ~4-week history exists in one sitting.

The output is a self-scoring demo: every generated item is tagged with
`scenario:Sxx` (and `adv_case:ADV-n.x` where applicable), and
[`expected.json`](expected.json) records what the analyzer *should* do for each
probe, so predictions can be joined to ground truth after a run.

---

## 1. Requirements

- Python 3.11+, `requests` (only third-party dep). No other deps.
- The RP stand reachable via two port-forwards:

  ```bash
  kubectl port-forward svc/reportportal-uat 9999:9999 &
  kubectl port-forward svc/reportportal-api 8585:8585 &
  ```

  Auth base `http://localhost:9999/uat`, API base `http://localhost:8585/api`,
  superadmin / superadmin (token flow as in
  `deploy/minikube/scripts/_lib.sh`). Override with `--rp-uat` / `--rp-api`.

The generator **creates** the three projects (`webshop-ui`, `payments-services`,
`frontend-apps`) if missing and configures per-project analyzer settings itself.

---

## 2. Running

```bash
cd demo-data

# 1. Inspect the whole-timeline plan (uploads nothing):
python3 generate.py --dry-run

# 2. (Re)write the expected-outcome manifest:
python3 generate.py --emit-expected

# 3. Tiny end-to-end smoke against the live stand
#    (creates/reuses webshop-ui, uploads one small launch, verifies items+logs):
python3 generate.py --smoke

# 4. Full upload, phase by phase (recommended: run in order, watch each phase):
python3 generate.py --phase 0     # backdated pre-history pack
python3 generate.py --phase 1     # FEA cold analysis (install-wide events < 50)
python3 generate.py --phase 2     # WSU+PSV history + defect_update replay
python3 generate.py --phase 3     # probe launches, analyzed unlabeled
#   ...or the entire timeline at once:
python3 generate.py

# Filters (compose with any mode):
python3 generate.py --dry-run --phase 2
python3 generate.py --day 2026-07-01           # just the outage day
python3 generate.py --log-workers 4            # gentler on the laptop VM
```

Determinism: all expansion/placement is seeded (`gen/config.SEED = 20260717`),
so re-runs produce byte-for-byte the same corpus (RP launch UUIDs differ, the
item content does not).

---

## 2a. Remediation / operations modes

These exist for recovering a partial run (a crash between upload and triage) and
for the analyzer coverage the phased upload alone does not produce:

```bash
# Re-send history triage that a crashed/resumed run never applied. Idempotent:
# reads each item's stamped ground_truth attribute (order-independent) and skips
# item ids listed in the exclude file (items that already have a label_event).
python3 generate.py --replay-only --exclude-items-file /tmp/exclude_items.txt

# Give history items a suggestion.features snapshot so the GBM can train.
# (reset already-triaged items to To-Investigate, analyze, then --replay-only
#  re-applies the human labels, whose label_events now join to those features.)
python3 generate.py --analyze-history
```

**Why `--analyze-history` is needed (hard lesson).** GBM training is
snapshot-only: `ml/trainer.py` drops any label_event whose item has no stored
`suggestion.features`. History launches uploaded in phase 2 with auto-analysis
**off** never produced that snapshot, so their labels — however many
label_events you replay — contribute **zero** training rows and the GBM never
ships. The correct phase-2 flow is **analyze first (features), then replay the
human triage (labels)**. A future generator revision should enable AA during
phase-2 history upload and analyze each launch before its `defect_update` replay,
making `--analyze-history` unnecessary. Also note: `defect_update` only creates a
`label_event` if the item is already in `analyzer.test_item`; firing triage
immediately after launch-finish races indexing and silently drops most events —
replay once indexing has settled.

If the retrain scheduler is stuck in its cold-phase 1/hour debounce, force a
retrain out-of-band by publishing `train_models` (route reason bypasses the
event-trigger throttle):

```bash
kubectl exec pod/reportportal-rabbitmq-0 -c rabbitmq -- curl -s \
  -u rabbitmq:rabbitmqpassword -H 'content-type:application/json' -XPOST \
  http://localhost:15672/api/exchanges/analyzer/analyzer-default/publish \
  -d '{"properties":{"content_type":"application/json"},"routing_key":"train_models",
       "payload":"{\"model_type\":1,\"project\":3,\"gathered_metric_total\":0}","payload_encoding":"string"}'
```

## 3. Phase meanings (SCENARIOS.md §6)

| Phase | What runs | Analyzer state | Why |
|---|---|---|---|
| **0** | Backdated pre-history pack (Dec 2025 – May 2026) | AA off | > 180 d stale-guard items, recency ladder, old fingerprints |
| **1** | `frontend-apps` launches (Jun 29) | AA **on, FEA only** | proves rule fallback / cold model while install-wide `label_events` < 50 (`method ∈ {hash,kb,rule_cold}`, never `gbm`) |
| **2** | `webshop-ui` + `payments-services` history (Jun 29 → Jul 14) + `defect_update` replay | AA off on WSU/PSV | builds label history; install crosses 50 → 100 → 300 → GBM train, then WSU isotonic |
| **3** | Probe launches (Jul 17 demo day) analyzed unlabeled | AA **on, all** | the probe decisions are the assertions scored against `expected.json` |
| **4** | S43 feedback sweep (Jul 16) | — | accept/override/flip `defect_update`s → retrain + ship-gate |
| **5** | S44 route calls (cluster / search / suggest_patterns) + S45 isolation sequence | — | grouping stability, cross-project isolation invariants |

Phases 0–3 are fully implemented. Phases 4–5 print the actions they cover and
run best-effort route calls against the uploaded probe launches; deepen them
against the real DB once phase 3 has landed (see "Deviations").

Roles per item: **H** = history (a `defect_update` replays its ground-truth
label after the launch finishes); **P** = probe (stays To-Investigate, gets
analyzed); **decoy** = passing item carrying the same logs (S23 spam / S24
herrings) with plausible info-level pass logs.

---

## 4. Scoring against `expected.json`

`expected.json` has one row per probe item:

```json
{
  "archetype_id": "JAVA-SEL-01", "project": "webshop-ui",
  "scenario": ["S09"], "adv_case": null, "ground_truth": "pb",
  "expected_action": "auto",        // auto | suggest | abstain
  "expected_label": "pb",
  "expected_method": "hash"          // hash | kb | gbm | rule_cold | "" (unspecified)
}
```

After a full run, join predictions to these rows via the item attributes the
generator stamps (`scenario`, `adv_case`, `archetype`, `ground_truth`) plus the
analyzer's `suggestion` / `launch_group` rows:

- **auto** probe → analyzer must confidently apply `expected_label` with
  `suggestion.method == expected_method` (where specified).
- **suggest** probe → analyzer must return `expected_label` in its top-k but not
  auto-apply.
- **abstain** probe (`ground_truth == "ti"`) → analyzer must abstain; a confident
  label here is a **hard fail** (SCENARIOS.md §6 scoring rules).

Scoring hard rules (inherited): abstaining on a decidable item = soft miss;
confident wrong label = hard fail; any cross-project leak (S45) = release blocker.

The scoring harness itself is out of scope for the generator; `expected.json`
plus the stamped attributes are everything it needs to join.

---

## 5. Layout

```
demo-data/
  generate.py          CLI + phase orchestration
  gen/
    config.py          endpoints, projects, issue-locator map, timeline dates
    corpus.py          load corpus/*.json, expand variants, substitute {placeholders}
    schedule.py        POLICY table + Scheduler -> the concrete upload plan (+ filler)
    rp.py              RP client (auth, project create, analyzer config, v2 reporting,
                       defect_update replay, analyze, route calls, verification)
    expected.py        build expected.json
  expected.json        generated manifest (checked in; regen with --emit-expected)
  corpus/*.json        input archetypes (not modified)
  SCENARIOS.md         the spec (not modified)
```

---

## 6. Deviations (SCENARIOS.md said "note the reading you chose")

The spec warns it may be internally ambiguous and instructs choosing the reading
that maximises analyzer-mechanism coverage. Choices made:

1. **Volume is corpus + filler driven, not §3.1's per-day counts.** SCENARIOS.md
   §3.1's small parenthetical failed-counts (e.g. "12 f") sum to far less than
   §3.2's roll-up (~1450 failed, ~418 label_events) once the large scenario
   populations (S16/S18/S19/S20 …) are included. The dry-run therefore prioritises
   uploading the full corpus expansion (what actually proves the mechanisms) over
   matching the daily hints. Current plan: **~144 launches, ~880 failed + ~850
   passing items, ~465 label_events** (WSU 349 → clears 300/isotonic, PSV 101,
   FEA 15 → stays < 20/cold, crossing 50→100→300 in order). Launch count and
   label thresholds match the spec; raw failed-item count is lower than 1450 but
   the same order of magnitude and every archetype/scenario is represented. Bump
   `POLICY[...]['target']` or `_filler_targets` to push volume higher.

2. **Generic "filler" failures/passes** (`scenario:filler`) pad nightly launches
   so they look real and the launch skeleton isn't half-empty. They are tagged so
   scoring ignores them, and ~35% carry a real label to feed the threshold walk.

3. **No custom defect sub-types.** All five labels map onto RP's built-in default
   groups (`pb001/ab001/si001/nd001/ti001`); no scenario needs a bespoke subtype,
   so none are created. `ti` (`ti001`, To-Investigate) doubles as "abstain".

4. **`frontend-apps` label replay is capped at 15** (`FEA_LABEL_BUDGET`) — per
   SCENARIOS §1 the FEA team quarantines via a `flaky-quarantine` attribute
   instead of setting defect types, so it must stay < 20 `label_events`. FEA
   history items above the cap stay To-Investigate (unlabeled), which is realistic
   and preserves the cold-model proof (S42).

5. **Probe items land on the Jul 17 demo-day probe launches** rather than being
   sprinkled across Jul 15–17. This keeps the "arrive unlabeled, analyzer decides"
   population in one clearly-analyzable phase-3 batch.

6. **Spam blocks are posted as one large multi-line log row** (not N rows), and
   are **capped to 40 lines under `--smoke`** so the smoke stays fast. The full
   run uses the corpus counts (200×, 5000×).

7. **Phases 4–5 are functional-but-light.** Phase 3 must land first (probe launches
   uploaded + analyzed) before the S43 sweep and S44/S45 route calls have real
   `testItemId`s / launch ids to act on. The scaffolding, endpoints, and the exact
   `defect_update` / `cluster` / `suggest_patterns` call shapes are in `gen/rp.py`;
   wire the concrete item selections once a phase-3 run exists.

8. **Log batching** uses a small thread pool of per-log POSTs (`--log-workers`,
   default 6) rather than the array endpoint, matching the proven single-object
   shape in `_lib.sh` while staying polite to the laptop VM.
