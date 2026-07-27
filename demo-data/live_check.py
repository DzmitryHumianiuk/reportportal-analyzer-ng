#!/usr/bin/env python3
"""Two-run live check: does human triage of run 1 carry into run 2?

Uploads one simulated day of launches into a fresh project, with every failed
item left as To Investigate (no defect type), so a human can triage them by
hand. Then uploads the next day's run of the same launches with auto analysis
on, and the analyzer's decisions can be compared against that triage.

Stage 1 (``--stage first``): creates the project, turns auto analysis OFF and
indexing ON (indexing is what puts the items in the analyzer's own store, which
the later inheritance needs), uploads the day-1 launches. Nothing is labeled.

Stage 2 (``--stage second``): turns auto analysis ON and uploads the day-2
launches. Every failed item goes through the analyzer.

Stage 3 (``--stage report``): reads the analyzer database directly and prints
what it decided for the day-2 items, joined to the day-1 human labels.

Usage (RP reachable through the ingress port-forward on :8080):

    python3 live_check.py --stage first  --project live-check-01
    #  ... triage the day-1 items by hand in the UI ...
    python3 live_check.py --stage second --project live-check-01
    python3 live_check.py --stage report --project live-check-01 \
        --dsn postgresql://analyzer:analyzer@127.0.0.1:15432/analyzer

Defaults describe the payments-services pair 2026-07-06 -> 2026-07-07: three
launches, 11 failures each day, and every day-2 failure repeats a failure kind
seen on day 1.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from gen import rp as rpmod  # noqa: E402
from generate import build_plan  # noqa: E402

SOURCE_PROJECT = "payments-services"
DAY_ONE = "2026-07-06"
DAY_TWO = "2026-07-07"


def _launches_for(plan, source_project, date, target_project):
    """Day's launches, re-pointed at the target project."""
    out = []
    for la in plan:
        if la.project != source_project or la.date != date:
            continue
        out.append(dataclasses.replace(la, project=target_project))
    return sorted(out, key=lambda la: la.name)


def _describe(launches):
    for la in launches:
        failed = [pi.item for pi in la.items if pi.item.status != "passed"]
        print(f"  {la.name}: {len(la.items)} items, {len(failed)} failed")
        for it in failed:
            head = it.logs[0].message.splitlines()[0][:70] if it.logs else ""
            print(f"      {it.test_name[:44]:44s} | {head}")


def stage_first(client, plan, args):
    created = client.ensure_project(args.project)
    print(f"project {args.project}: {'created' if created else 'already exists'}")
    client.set_analyzer(args.project, enabled=False)
    print("auto analysis OFF, indexing ON (items land as To Investigate)")

    launches = _launches_for(plan, args.source_project, args.day_one, args.project)
    print(f"\nday 1 ({args.day_one}): {len(launches)} launches")
    _describe(launches)

    print("\nuploading...", flush=True)
    for la in launches:
        res = client.report_launch(la)
        if res.get("skipped"):
            print(f"  SKIP {la.name} ({res['reason']})", flush=True)
            continue
        print(f"  done {la.name}: {len(res['items'])} items", flush=True)
    print("\nNothing was labeled. Triage the failed items by hand, then run --stage second.")


def stage_second(client, plan, args):
    client.set_analyzer(args.project, enabled=True)
    print(f"project {args.project}: auto analysis ON")

    launches = _launches_for(plan, args.source_project, args.day_two, args.project)
    print(f"\nday 2 ({args.day_two}): {len(launches)} launches")
    _describe(launches)

    print("\nuploading...", flush=True)
    for la in launches:
        res = client.report_launch(la)
        if res.get("skipped"):
            print(f"  SKIP {la.name} ({res['reason']})", flush=True)
            continue
        lid = res.get("lid") or client.numeric_launch(args.project, res["luuid"])
        print(f"  done {la.name}: {len(res['items'])} items, launch id {lid}", flush=True)
    print("\nAuto analysis runs on launch finish; per-item analysis fires seconds after")
    print("each item finishes. Run --stage report once the launches are finished.")


REPORT_SQL = """
SELECT s.item_id,
       s.method,
       s.predicted_label,
       round(s.confidence::numeric, 3) AS confidence,
       s.source,
       s.matched_item_id,
       s.llm_used,
       s.outcome,
       s.abstain_reason
FROM analyzer.suggestion s
WHERE s.project_id = %(project_id)s
ORDER BY s.suggestion_id DESC
LIMIT %(limit)s
"""


def stage_report(args, project_id):
    import psycopg

    with psycopg.connect(args.dsn) as conn:
        rows = conn.execute(
            REPORT_SQL, {"project_id": project_id, "limit": args.limit}
        ).fetchall()

    print(f"analyzer project_id={project_id}, last {len(rows)} decisions (newest first)\n")
    head = (
        f"{'item':>8}  {'method':10s} {'label':5s} {'conf':>6s} {'source':6s} "
        f"{'matched':>8s} {'llm':3s} {'outcome':9s} reason"
    )
    print(head)
    for item_id, method, label, conf, source, matched, llm, outcome, reason in rows:
        print(
            f"{item_id:>8}  {method or '-':10s} {label or '-':5s} {str(conf or '-'):>6s} "
            f"{source or '-':6s} {str(matched or '-'):>8s} {'yes' if llm else '-':3s} "
            f"{outcome or '-':9s} {reason or ''}"
        )
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", required=True, choices=["first", "second", "report", "plan"])
    ap.add_argument("--project", required=True, help="target RP project (created if missing)")
    ap.add_argument("--source-project", default=SOURCE_PROJECT)
    ap.add_argument("--day-one", default=DAY_ONE)
    ap.add_argument("--day-two", default=DAY_TWO)
    ap.add_argument("--rp-api", default="http://localhost:8080")
    ap.add_argument("--rp-uat", default="http://localhost:8080")
    ap.add_argument("--log-workers", type=int, default=4)
    ap.add_argument("--dsn", default="postgresql://analyzer:analyzer@127.0.0.1:15432/analyzer")
    ap.add_argument("--limit", type=int, default=40)
    args = ap.parse_args()

    if args.stage != "plan":
        client = rpmod.RPClient(
            uat_base=args.rp_uat, api_base=args.rp_api, log_workers=args.log_workers
        )
        client.login()

    if args.stage == "report":
        r = client._send("GET", f"{client.api}/api/v1/project/{args.project}")
        r.raise_for_status()
        project_id = r.json()["projectId"]
        print(f"RP project {args.project} -> numeric id {project_id}")
        return stage_report(args, project_id)

    _, plan = build_plan()

    if args.stage == "plan":
        for label, day in (("day 1", args.day_one), ("day 2", args.day_two)):
            launches = _launches_for(plan, args.source_project, day, args.project)
            print(f"{label} ({day}): {len(launches)} launches")
            _describe(launches)
            print()
        return 0

    if args.stage == "first":
        stage_first(client, plan, args)
    else:
        stage_second(client, plan, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
