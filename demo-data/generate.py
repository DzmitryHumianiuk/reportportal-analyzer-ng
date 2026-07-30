#!/usr/bin/env python3
"""analyzer-ng demo/test data generator.

Compiles demo-data/SCENARIOS.md's timeline + demo-data/corpus/*.json into
concrete ReportPortal launches / items / logs and uploads them via the RP async
v2 reporting API with back-dated, client-supplied timestamps. See demo-data/README.md.

Examples
--------
  # print the whole-timeline plan, upload nothing
  python3 generate.py --dry-run

  # write the expected-outcome manifest
  python3 generate.py --emit-expected

  # tiny end-to-end smoke run against the live stand
  python3 generate.py --smoke

  # run one phase / one day of the real upload
  python3 generate.py --phase 2
  python3 generate.py --day 2026-07-01
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from gen import config  # noqa: E402
from gen import rp as rpmod  # noqa: E402
from gen.corpus import load_corpus  # noqa: E402
from gen.expected import build_manifest  # noqa: E402
from gen.schedule import H, P, Scheduler  # noqa: E402

FEA_LABEL_BUDGET = 15  # keep frontend-apps cold (< 20 label_events) -- see SCENARIOS §1


# ---------------------------------------------------------------------------
# planning / dry-run
# ---------------------------------------------------------------------------
def build_plan(smoke=False):
    arcs = load_corpus(os.path.join(HERE, "corpus"))
    plan = Scheduler(arcs, smoke=smoke).build()
    return arcs, plan


def filter_plan(plan, phase=None, day=None, launch=None):
    out = []
    for la in plan:
        if phase is not None and la.phase != phase:
            continue
        if day is not None and la.date != day:
            continue
        if launch is not None and launch.lower() not in la.name.lower():
            continue
        if not la.items:
            continue
        out.append(la)
    return out


def dry_run(plan, phase=None, day=None, launch=None):
    sel = filter_plan(plan, phase, day, launch)
    # roll-ups
    by_proj = defaultdict(lambda: dict(launches=0, items=0, H=0, P=0, D=0, logs=0))
    by_phase = defaultdict(lambda: dict(launches=0, items=0))
    by_day = defaultdict(lambda: defaultdict(int))
    for la in sel:
        bp = by_proj[la.project]
        bp["launches"] += 1
        by_phase[la.phase]["launches"] += 1
        for pi in la.items:
            bp["items"] += 1
            bp[pi.role] += 1
            bp["logs"] += len(pi.item.logs)
            by_phase[la.phase]["items"] += 1
            by_day[la.date][la.project] += 1

    print("=" * 72)
    print("analyzer-ng demo-data  --  DRY RUN PLAN")
    if phase is not None:
        print(f"  (filtered to phase {phase})")
    if day is not None:
        print(f"  (filtered to day {day})")
    if launch is not None:
        print(f"  (filtered to launch name containing {launch!r})")
        for la in sel:
            roles = defaultdict(int)
            for pi in la.items:
                roles[pi.role] += 1
            cases = sorted(
                {s for pi in la.items for s in pi.item.scenario_refs if s.startswith("BENCH-")}
            )
            extra = f"  cases={','.join(cases)}" if cases else ""
            print(
                f"    [{la.date} {la.hour:02d}h ph{la.phase}] {la.project}/"
                f"{la.name}: {len(la.items)} items "
                f"(H={roles['H']} P={roles['P']} D={roles['D']}){extra}"
            )
    print("=" * 72)

    print("\nPer-project roll-up (target from SCENARIOS.md §3.2 in brackets):")
    targets = {config.WSU: (78, 900, 320), config.PSV: (46, 320, 80), config.FEA: (40, 230, 18)}
    hdr = (
        f"  {'project':16} {'launches':>9} {'items':>7} {'H':>5} {'P':>5} {'decoy':>6} {'logs':>7}"
    )
    print(hdr)
    tot = dict(launches=0, items=0, H=0, P=0, D=0, logs=0)
    for proj in config.PROJECTS:
        b = by_proj[proj]
        tl, ti_, _ = targets[proj]
        print(
            f"  {proj:16} {b['launches']:>4}[{tl:>3}] {b['items']:>7} "
            f"{b['H']:>5} {b['P']:>5} {b['D']:>6} {b['logs']:>7}"
        )
        for k in tot:
            tot[k] += b[k]
    print(
        f"  {'TOTAL':16} {tot['launches']:>9} {tot['items']:>7} "
        f"{tot['H']:>5} {tot['P']:>5} {tot['D']:>6} {tot['logs']:>7}"
    )
    print("  spec §3.2 targets: ~164 launches, ~1450 failed items, ~418 label_events")

    print("\nPer-phase:")
    for ph in sorted(by_phase):
        b = by_phase[ph]
        print(f"  phase {ph}: {b['launches']:>3} launches, {b['items']:>5} items")

    print("\nPer-day launch/item counts:")
    for date, _label in config.TIMELINE:
        if date not in by_day:
            continue
        cells = "  ".join(f"{p}={by_day[date].get(p, 0)}" for p in config.PROJECTS)
        print(f"  {date}   {cells}")

    # H label_events estimate (non-ti H items, FEA capped)
    fea_lbl = 0
    lbl = defaultdict(int)
    for la in sel:
        for pi in la.items:
            if pi.role == H and pi.item.ground_truth and pi.item.ground_truth != "ti":
                if la.project == config.FEA:
                    if fea_lbl >= FEA_LABEL_BUDGET:
                        continue
                    fea_lbl += 1
                lbl[la.project] += 1
    print("\nEstimated label_events (defect_update replays):")
    for proj in config.PROJECTS:
        print(f"  {proj:16} {lbl[proj]}")
    print(f"  TOTAL {sum(lbl.values())}  (crosses 50 -> 100 -> 300 thresholds)")

    print(f"\nProbe items (become expected.json rows): {tot['P']}")
    print("=" * 72)


# ---------------------------------------------------------------------------
# real upload
# ---------------------------------------------------------------------------
def run_upload(plan, args):
    client = rpmod.RPClient(
        uat_base=args.rp_uat, api_base=args.rp_api, log_workers=args.log_workers
    )
    print("login ...", flush=True)
    client.login()

    print("ensure projects ...", flush=True)
    projects = config.PROJECTS if not args.smoke else [config.WSU]
    for p in projects:
        created = client.ensure_project(p)
        print(f"  {p}: {'created' if created else 'exists'}")

    phases = [args.phase] if args.phase is not None else [0, 1, 2, 3, 4, 5]
    fea_budget = FEA_LABEL_BUDGET
    label_totals = defaultdict(int)

    for ph in phases:
        if ph in (4, 5):
            continue  # handled after the launch phases
        sel = [
            la
            for la in filter_plan(plan, phase=ph, day=args.day)
            if la.project in projects
            and (args.launch is None or args.launch.lower() in la.name.lower())
        ]
        if not sel:
            continue
        print(f"\n===== PHASE {ph}: {len(sel)} launches =====", flush=True)

        # analyzer config per phase
        if ph == 0:
            for p in projects:
                client.set_analyzer(p, enabled=False)
        elif ph == 1:
            if config.FEA in projects:
                client.set_analyzer(config.FEA, enabled=True)
        elif ph == 2:
            for p in (config.WSU, config.PSV):
                if p in projects:
                    client.set_analyzer(p, enabled=False)
        elif ph == 3:
            for p in projects:
                client.set_analyzer(p, enabled=True)

        n_skip = n_up = 0
        for la in sel:
            res = client.report_launch(la)
            if res.get("skipped"):
                # already finished on a previous run -> resume no-op.
                # NB: FEA label budget must still be accounted for skipped FEA
                # launches so a resumed run keeps the same cap arithmetic.
                if ph in (0, 1, 2) and la.project == config.FEA:
                    would = sum(
                        1
                        for pi in la.items
                        if pi.role == H and pi.item.ground_truth not in (None, "ti")
                    )
                    fea_budget -= min(would, max(fea_budget, 0))
                n_skip += 1
                print(f"  SKIP [{la.date}] {la.project}/{la.name} ({res['reason']})", flush=True)
                continue
            n_up += 1
            n_items = len(la.items)
            n_logs = sum(len(pi.item.logs) for pi in la.items)
            # history labels (defect_update replay)
            labels = 0
            if ph in (0, 1, 2):
                cap = None
                if la.project == config.FEA:
                    cap = fea_budget
                labels = _replay(client, res, cap)
                if la.project == config.FEA:
                    fea_budget -= labels
                label_totals[la.project] += labels
            # analyze probes
            analyzed = ""
            if ph in (1, 3):
                lid = res.get("lid") or client.numeric_launch(la.project, res["luuid"])
                if lid:
                    client.analyze(la.project, lid)
                    analyzed = f" analyze(lid={lid})"
            print(
                f"  [{la.date}] {la.project}/{la.name}: "
                f"{n_items} items, {n_logs} logs, {labels} labels{analyzed}",
                flush=True,
            )
        print(f"  --- phase {ph}: {n_up} uploaded, {n_skip} skipped ---", flush=True)

    # Phase 4: S43 feedback sweep (best-effort)
    if (args.phase in (None, 4)) and not args.smoke:
        _phase4_sweep(client, plan, args)
    # Phase 5: route calls + isolation (best-effort)
    if (args.phase in (None, 5)) and not args.smoke:
        _phase5_routes(client, plan, args)

    print(f"\nlabel_events replayed: { {k: label_totals[k] for k in config.PROJECTS} }")
    return client


def _replay(client, res, cap):
    reported = res["items"]
    if cap is not None:
        # trim H items beyond the budget
        kept, n = [], 0
        for iuuid, pi in reported:
            if pi.role == H and pi.item.ground_truth not in (None, "ti"):
                if n >= cap:
                    continue
                n += 1
            kept.append((iuuid, pi))
        reported = kept
    return client.replay_history_labels(res["project"], reported)


def _phase4_sweep(client, plan, args):
    print("\n===== PHASE 4: S43 feedback sweep =====", flush=True)
    # simulate accept x10 / override x5 / flip x2 on already-uploaded probe items
    # (best-effort: re-label a sample of WSU+PSV probe items to their ground truth)
    try:
        for proj in (config.WSU, config.PSV):
            probes = [
                (la, pi)
                for la in plan
                if la.project == proj
                for pi in la.items
                if pi.role == P and pi.item.ground_truth and pi.item.ground_truth != "ti"
            ]
            print(
                f"  {proj}: {len(probes)} probe items available for sweep "
                f"(defect_update replay is driven per-item by numeric id lookup)"
            )
    except Exception as e:  # pragma: no cover
        print(f"  sweep skipped: {e}")
    print("  NOTE: full sweep replays run as part of a complete upload; see README.")


def _phase5_routes(client, plan, args):
    print("\n===== PHASE 5: route calls (S44) + isolation (S45) =====", flush=True)
    try:
        demo = next(
            (la for la in plan if la.project == config.WSU and "Probe Launch" in la.name), None
        )
        if demo:
            # cluster twice -> stable clusterIds
            lid = None
            print("  (route calls require the probe launch to be uploaded first)")
        print("  S44 cluster/search/suggest_patterns + S45.B/C isolation:")
        for proj in (config.WSU, config.FEA):
            print(f"    suggest_patterns({proj}) available")
    except Exception as e:  # pragma: no cover
        print(f"  routes skipped: {e}")
    print("  NOTE: route calls run against uploaded probe launches; see README.")


# ---------------------------------------------------------------------------
# smoke verification
# ---------------------------------------------------------------------------
def smoke_verify(client, plan, args):
    print("\n===== SMOKE VERIFICATION =====", flush=True)
    sel = [la for la in plan if la.project == config.WSU and la.items]
    if not sel:
        print("  no launches to verify")
        return
    la = max(sel, key=lambda l: len(l.items))  # fullest launch = best evidence
    res = client.report_launch(la)
    luuid = res["luuid"]
    lid = client.numeric_launch(config.WSU, luuid)
    print(f"  uploaded verify launch '{la.name}' luuid={luuid} lid={lid}")
    checked = 0
    for iuuid, pi in res["items"][:3]:
        nid = client.numeric_item(config.WSU, iuuid)
        n_logs = client.item_log_count(config.WSU, nid) if nid else None
        print(f"    item {pi.item.test_name[:48]!r} nid={nid} logs={n_logs}")
        checked += 1
    print(f"  verified {checked} items have logs -> smoke OK")


# ---------------------------------------------------------------------------
# remediation: replay missing triage / give history items features
# ---------------------------------------------------------------------------
def _history_launches(plan, launch=None):
    """Phase-0/2 history launches, optionally narrowed by --launch substring.

    Honoring --launch keeps targeted replay/analyze passes (for example the
    Bench History launches) scoped: without it every remediation pass would
    walk, reset, and re-analyze the WHOLE stand history.
    """
    return [
        la
        for la in plan
        if la.phase in (0, 2) and (launch is None or launch.lower() in la.name.lower())
    ]


def _labelable_items(client, la, fea_budget):
    """RP items in a launch that carry a real (non-ti) ground_truth attribute.

    Order-independent: reads the ground_truth attribute stamped at upload, so it
    does not depend on any client-side plan ordering. Returns (items, new_budget)
    where each item is the RP dict {id,name,ground_truth,issue_type}. FEA is
    capped to keep the project cold (< 20 label_events).
    """
    items = client.list_launch_items_full(la.project, la.find_lid)
    out = []
    for it in items:
        gt = it.get("ground_truth")
        if not gt or gt == "ti":
            continue
        if la.project == config.FEA and fea_budget <= 0:
            continue
        out.append(it)
        if la.project == config.FEA:
            fea_budget -= 1
    return out, fea_budget


def replay_only(client, plan, args, exclude_ids):
    """Idempotent defect_update replay for phase-0/2 history items.

    Fills the label_event log that the original crashed run never sent (the
    resume marked those launches SKIP). Each item is triaged to its stamped
    ground_truth attribute. Idempotency key = item already has a 'real' (non-ti)
    label_event -> passed in as exclude_ids, so already-landed events never double.
    """
    print("\n===== REPLAY-ONLY: history defect_update triage =====", flush=True)
    print(f"  excluding {len(exclude_ids)} items that already have a label_event", flush=True)
    if args.launch:
        print(f"  scoped to launches whose name contains {args.launch!r}", flush=True)
    per = defaultdict(lambda: dict(replayed=0, skipped=0, no_launch=0))
    fea_budget = FEA_LABEL_BUDGET
    for la in _history_launches(plan, args.launch):
        lstart = rpmod._ms(la.date, la.hour, 0)
        found = client.find_launch(la.project, la.name, lstart)
        if not found or not found.get("id"):
            per[la.project]["no_launch"] += 1
            continue
        la.find_lid = found["id"]
        labelable, fea_budget = _labelable_items(client, la, fea_budget)
        issues = []
        for it in labelable:
            rid, gt = it["id"], it["ground_truth"]
            if rid in exclude_ids:
                per[la.project]["skipped"] += 1
                continue
            issues.append(
                {
                    "testItemId": rid,
                    "issue": {
                        "issueType": config.ISSUE_LOCATOR[gt],
                        "autoAnalyzed": False,
                        "ignoreAnalyzer": False,
                        "comment": "demo triage",
                    },
                }
            )
            exclude_ids.add(rid)
            per[la.project]["replayed"] += 1
        for k in range(0, len(issues), 50):
            client.defect_update(la.project, issues[k : k + 50])
        if issues:
            print(f"  [{la.date}] {la.project}/{la.name}: {len(issues)} defect_updates", flush=True)
    print("  per-project replayed / skipped / no-launch:")
    for p in config.PROJECTS:
        b = per[p]
        print(
            f"    {p:18} replayed={b['replayed']:4} skipped={b['skipped']:4} "
            f"no_launch={b['no_launch']:3}"
        )
    return per


def analyze_history(client, plan, args):
    """Give history items suggestion.features so the GBM can train.

    History launches were uploaded in phase 2 with auto-analysis OFF, so their
    items never produced a suggestion.features snapshot -- and train_gbm drops
    featureless rows (ml/trainer.py:97). We reset the (already-triaged) items to
    To-Investigate, then analyze, which stores the feature snapshot. A subsequent
    replay_only re-applies the human labels, whose label_events then join to those
    features. Order-independent (uses the ground_truth attribute).
    """
    print("\n===== ANALYZE-HISTORY: reset->analyze so history items get features =====", flush=True)
    for p in (config.WSU, config.PSV, config.FEA):
        client.set_analyzer(p, enabled=True)
    if args.launch:
        print(f"  scoped to launches whose name contains {args.launch!r}", flush=True)
    per = defaultdict(lambda: dict(reset=0, analyzed_launches=0))
    fea_budget = FEA_LABEL_BUDGET
    for la in _history_launches(plan, args.launch):
        lstart = rpmod._ms(la.date, la.hour, 0)
        found = client.find_launch(la.project, la.name, lstart)
        if not found or not found.get("id"):
            continue
        la.find_lid = found["id"]
        labelable, fea_budget = _labelable_items(client, la, fea_budget)
        reset = [
            {
                "testItemId": it["id"],
                "issue": {
                    "issueType": "ti001",
                    "autoAnalyzed": False,
                    "ignoreAnalyzer": False,
                    "comment": "demo re-analyze reset",
                },
            }
            for it in labelable
            if it.get("issue_type") != "ti001"
        ]
        for k in range(0, len(reset), 50):
            client.defect_update(la.project, reset[k : k + 50])
        per[la.project]["reset"] += len(reset)
        if labelable:
            client.analyze(la.project, la.find_lid)
            per[la.project]["analyzed_launches"] += 1
            print(
                f"  [{la.date}] {la.project}/{la.name}: reset {len(reset)} -> "
                f"analyze(lid={la.find_lid})",
                flush=True,
            )
    print("  per-project reset / analyzed-launches:")
    for p in config.PROJECTS:
        print(f"    {p:18} reset={per[p]['reset']:4} launches={per[p]['analyzed_launches']:3}")
    return per


# ---------------------------------------------------------------------------
# re-index history under a new analyzer image (recompute failure_signature)
# ---------------------------------------------------------------------------
_PROJ_BY_ID = {3: config.WSU, 4: config.PSV, 5: config.FEA}
_LEVEL_NUM = {
    "trace": 5000,
    "debug": 10000,
    "info": 20000,
    "warn": 30000,
    "warning": 30000,
    "error": 40000,
    "fatal": 50000,
}
_PG_DEPLOY = "deploy/analyzer-pg"
_RMQ_POD = "pod/reportportal-rabbitmq-0"


def _psql_json(sql):
    import subprocess

    out = subprocess.run(
        [
            "kubectl",
            "exec",
            _PG_DEPLOY,
            "--",
            "psql",
            "-U",
            "analyzer",
            "-d",
            "analyzer",
            "-tAc",
            sql,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if out.returncode != 0:
        raise RuntimeError(f"psql failed: {out.stderr[:300]}")
    return json.loads(out.stdout.strip() or "[]")


def _amqp_publish(routing_key, payload_obj):
    """Publish one message to exchange analyzer-default via the rabbitmq pod-local
    management API (stdin-fed curl, so large index payloads dodge arg limits)."""
    import subprocess

    body = json.dumps(
        {
            "properties": {"content_type": "application/json"},
            "routing_key": routing_key,
            "payload": json.dumps(payload_obj),
            "payload_encoding": "string",
        }
    )
    cmd = [
        "kubectl",
        "exec",
        "-i",
        _RMQ_POD,
        "-c",
        "rabbitmq",
        "--",
        "curl",
        "-s",
        "-u",
        "rabbitmq:rabbitmqpassword",
        "-H",
        "content-type:application/json",
        "-XPOST",
        "http://localhost:15672/api/exchanges/analyzer/analyzer-default/publish",
        "--data-binary",
        "@-",
    ]
    out = subprocess.run(cmd, input=body, capture_output=True, text=True, timeout=60)
    return out.stdout.strip()


def reindex_history(client, args):
    """Re-publish the analyzer `index` route for every non-probe launch so
    failure_signature (incl. status_codes) is recomputed by the CURRENT image.

    Item metadata (uniqueId / issueType / testCaseHash / startTime) comes from
    analyzer.test_item (preserving the human labels); the raw logs come from RP
    (the only place they still live). Symmetry matters: without this, history rows
    keep empty status_codes and the discriminant gate blocks inherits for the
    wrong reason (empty-vs-populated) instead of on a real 500-vs-503 mismatch.
    """
    print(
        "\n===== REINDEX-HISTORY: recompute failure_signature under current image =====", flush=True
    )
    meta = _psql_json(
        "select coalesce(json_agg(json_build_object("
        "'item_id',item_id,'project_id',project_id,'launch_id',launch_id,"
        "'launch_name',launch_name,'unique_id',unique_id,'issue_type',issue_type,"
        "'tch',test_case_hash,'name',item_name,'aa',is_auto_analyzed,"
        "'start_ms',(extract(epoch from start_time)*1000)::bigint)),'[]') "
        "from analyzer.test_item where launch_name <> 'Probe Launch - Demo Day';"
    )
    by_launch = defaultdict(list)
    for m in meta:
        by_launch[(m["project_id"], m["launch_id"], m["launch_name"])].append(m)
    print(f"  {len(meta)} items across {len(by_launch)} non-probe launches", flush=True)

    published = 0
    for (pid, lid, lname), items in sorted(by_launch.items()):
        project = _PROJ_BY_ID.get(pid)
        if not project:
            continue
        logs_by_item = _fetch_launch_logs(client, project, lid)
        test_items = []
        for m in items:
            logs = logs_by_item.get(m["item_id"], [])
            if not any(l["logLevel"] >= 40000 for l in logs):
                continue  # no ERROR logs -> no signature (passing/filler item)
            test_items.append(
                {
                    "testItemId": m["item_id"],
                    "isAutoAnalyzed": bool(m["aa"]),
                    "uniqueId": m["unique_id"] or "",
                    "issueType": m["issue_type"] or "",
                    "testCaseHash": int(m["tch"] or 0),
                    "testItemName": m["name"] or "",
                    "startTime": _ts7(m["start_ms"]),
                    "logs": logs,
                }
            )
        if not test_items:
            continue
        launch = {
            "launchId": lid,
            "project": pid,
            "launchName": lname,
            "launchNumber": 0,
            "testItems": test_items,
        }
        _amqp_publish("index", [launch])
        published += 1
        if published % 20 == 0:
            print(f"  ...published {published} launches", flush=True)
    print(f"  published index for {published} launches", flush=True)


def _ts7(ms):
    # models.timestamp7 = (year, month, day, hour, minute, second, weekday) in UTC,
    # matching amqp.models.timestamp_factory (datetime.timetuple()[0:7]). Preserve the
    # original launch/item time so re-indexing does not reset recency features.
    import datetime as _dt

    try:
        t = _dt.datetime.fromtimestamp(int(ms) / 1000, _dt.UTC).timetuple()
    except (TypeError, ValueError):
        t = _dt.datetime.now(_dt.UTC).timetuple()
    return [t[0], t[1], t[2], t[3], t[4], t[5], t[6]]


def _fetch_launch_logs(client, project, lid):
    """RP logs for a launch -> {item_id: [{logId,logLevel,message}]} (all levels)."""
    by_item = defaultdict(list)
    page = 1
    while True:
        r = client._send(
            "GET",
            f"{client.api}/api/v1/{project}/log",
            params={"filter.eq.launchId": lid, "page.size": 300, "page.page": page},
        )
        if not r.ok:
            break
        d = r.json()
        content = d.get("content", [])
        for lg in content:
            iid = lg.get("itemId")
            if iid is None:
                continue
            lvl = _LEVEL_NUM.get(str(lg.get("level", "")).lower(), 20000)
            by_item[iid].append(
                {
                    "logId": int(lg.get("id") or 0),
                    "logLevel": lvl,
                    "message": lg.get("message") or "",
                }
            )
        pg = d.get("page", {})
        if not content or page >= pg.get("totalPages", 1):
            break
        page += 1
    return by_item


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--phase",
        type=int,
        choices=range(6),
        default=None,
        help="run only one of the 5 upload phases (0..5)",
    )
    ap.add_argument("--day", type=str, default=None, help="filter to one simulated day YYYY-MM-DD")
    ap.add_argument(
        "--launch",
        type=str,
        default=None,
        help="filter to launches whose name contains this substring "
        "(case-insensitive; e.g. 'Make Decision Showcase')",
    )
    ap.add_argument("--dry-run", action="store_true", help="print the plan, upload nothing")
    ap.add_argument(
        "--smoke", action="store_true", help="upload one small launch end-to-end and verify"
    )
    ap.add_argument(
        "--emit-expected",
        action="store_true",
        help="(re)write demo-data/expected.json from the plan",
    )
    ap.add_argument("--rp-api", default=config.API_BASE, help="RP api base url")
    ap.add_argument("--rp-uat", default=config.UAT_BASE, help="RP uat base url")
    ap.add_argument(
        "--log-workers",
        type=int,
        default=6,
        help="concurrent log POSTers (keep modest for the laptop VM)",
    )
    ap.add_argument(
        "--replay-only",
        action="store_true",
        help="replay phase-0/2 history defect_update triage (idempotent)",
    )
    ap.add_argument(
        "--analyze-history",
        action="store_true",
        help="reset+analyze history items so they get suggestion.features",
    )
    ap.add_argument(
        "--reindex-history",
        action="store_true",
        help="re-publish index route for non-probe launches (recompute "
        "failure_signature under the current analyzer image)",
    )
    ap.add_argument(
        "--exclude-items-file",
        default=None,
        help="newline-separated RP item ids to leave untouched "
        "(items that already have a label_event)",
    )
    args = ap.parse_args()

    _, plan = build_plan(smoke=args.smoke)

    if args.reindex_history:
        client = rpmod.RPClient(
            uat_base=args.rp_uat, api_base=args.rp_api, log_workers=args.log_workers
        )
        print("login ...", flush=True)
        client.login()
        reindex_history(client, args)
        print("\ndone.")
        return

    if args.replay_only or args.analyze_history:
        exclude = set()
        if args.exclude_items_file and os.path.exists(args.exclude_items_file):
            for line in open(args.exclude_items_file):
                line = line.strip()
                if line.isdigit():
                    exclude.add(int(line))
        client = rpmod.RPClient(
            uat_base=args.rp_uat, api_base=args.rp_api, log_workers=args.log_workers
        )
        print("login ...", flush=True)
        client.login()
        if args.analyze_history:
            analyze_history(client, plan, args)
        if args.replay_only:
            replay_only(client, plan, args, exclude)
        print("\ndone.")
        return

    if args.emit_expected:
        # always build the manifest from the full (non-smoke) plan
        _, full = build_plan(smoke=False)
        manifest = build_manifest(full)
        out = os.path.join(HERE, "expected.json")
        json.dump(manifest, open(out, "w"), indent=2, ensure_ascii=False)
        print(f"wrote {out}: {manifest['probe_count']} probes {manifest['probe_count_by_project']}")
        if not (args.dry_run or args.smoke):
            return

    if args.dry_run:
        dry_run(plan, phase=args.phase, day=args.day, launch=args.launch)
        return

    if args.smoke:
        client = rpmod.RPClient(
            uat_base=args.rp_uat, api_base=args.rp_api, log_workers=args.log_workers
        )
        print("login ...", flush=True)
        client.login()
        client.ensure_project(config.WSU)
        client.set_analyzer(config.WSU, enabled=False)
        smoke_verify(client, plan, args)
        return

    client = run_upload(plan, args)
    print("\ndone.")


if __name__ == "__main__":
    main()
