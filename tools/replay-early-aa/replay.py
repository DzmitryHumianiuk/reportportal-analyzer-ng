#!/usr/bin/env python3
"""Replay stored launch-finish decisions through the early singleton corner.

Answers "how often would early per-item AA disagree with the launch-finish
pass?" without running a launch: every stored GBM feature snapshot is scored
twice with the active model — once as stored, once with the launch-context
features forced to their singleton constants — and the disagreement is
reported by class and band. Read-only; writes nothing.

Usage (from the repo root, the analyzer venv):

    python tools/replay-early-aa/replay.py \
        --dsn postgresql://analyzer:analyzer@127.0.0.1:5432/analyzer \
        --project 7 [--limit 5000]

The flip rate this prints is the gate for widening the early label policy
(docs/EARLY-ITEM-AA.md): GBM-band early auto-labeling stays off until these
numbers say the singleton corner agrees with the finish context.
"""

from __future__ import annotations

import argparse
import json
import sys

from psycopg_pool import ConnectionPool

from analyzer_ng.ml.artifacts import PgModelStore
from analyzer_ng.ml.early_replay import aggregate, replay_row
from analyzer_ng.ml.serving import GbmPredictor

# Launch-finish GBM rows only: hash/KB decisions never consult the launch-context
# features, and 'early' rows are already the singleton corner.
_QUERY = """
SELECT item_id, features
FROM analyzer.suggestion
WHERE project_id = %(project)s
  AND method = 'gbm'
  AND source IS DISTINCT FROM 'early'
  AND features IS NOT NULL AND features <> '{}'::jsonb
ORDER BY suggestion_id DESC
LIMIT %(limit)s
"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", required=True, help="analyzer database DSN")
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--limit", type=int, default=5000)
    args = ap.parse_args()

    pool = ConnectionPool(args.dsn, min_size=1, max_size=2)
    predictor = GbmPredictor(PgModelStore(pool))
    if not predictor.has_model():
        print("no shipped GBM model — nothing to replay", file=sys.stderr)
        return 2

    with pool.connection() as conn:
        rows = conn.execute(_QUERY, {"project": args.project, "limit": args.limit}).fetchall()

    records = []
    for item_id, features in rows:
        rec = replay_row(predictor, args.project, item_id, features)
        if rec is not None:
            records.append(rec)

    report = aggregate(records)
    report["model_version"] = predictor.active_version()
    report["project"] = args.project
    print(json.dumps(report, indent=2))
    flips = report.get("label_flips_by_pair") or {}
    if flips:
        print(
            f"\n{report['label_flip_rate']:.1%} of decisions change label at the "
            "singleton corner. Widening the early label policy is not justified "
            "until this is near zero.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
