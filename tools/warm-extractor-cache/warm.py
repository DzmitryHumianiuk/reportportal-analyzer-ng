#!/usr/bin/env python3
"""Eagerly warm the extractor LLM cache for template sets with no fresh row.

Items analyzed before commit ba23301 ended extractor ``validation_fail``, so
their template sets have no cached extractor output and the ``llm_*`` GBM
features sit at the ``unknown`` sentinel until a suggest/analyze happens to
touch them (~12s first run). This tool replays the extractor for those template
sets eagerly, through the normal role path (``LlmEngine.run``), so results are
validated, cached and audit-logged exactly like production calls (issue #5).

Bounded and off-peak by design: ``--limit`` caps the run, ``--sleep-ms`` spaces
the calls out. ``--dry-run`` only lists what would run.

Usage (from the repo root, the analyzer venv, ports forwarded to the stand):

    python tools/warm-extractor-cache/warm.py \
        --dsn postgresql://analyzer:analyzer@127.0.0.1:5432/analyzer \
        --project 7 --dry-run

    python tools/warm-extractor-cache/warm.py \
        --dsn postgresql://analyzer:analyzer@127.0.0.1:5432/analyzer \
        --project 7 --limit 100 --ollama-url http://127.0.0.1:11434

The model tag must be the production ``ANALYZER_LLM_MODEL`` (the cache key is
``sha256(model || role || prompt_hash)`` — a different tag warms nothing the
service will ever read). Metric to watch: the printed ``validation_fail`` count
should stay near zero after the ba23301 fix.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

from psycopg_pool import ConnectionPool

from analyzer_ng.db.repositories.llm_cache import PgLlmCacheStore
from analyzer_ng.db.repositories.llm_events import PgLlmEventStore
from analyzer_ng.db.repositories.suggestion_ops import PgLlmFacts
from analyzer_ng.llm.breaker import CircuitBreaker
from analyzer_ng.llm.client import OllamaClient
from analyzer_ng.llm.engine import LlmEngine
from analyzer_ng.llm.roles.extractor import ExtractorRole
from analyzer_ng.llm.warmup import (
    FRESH_EXTRACTOR_HASHES_SQL,
    TEMPLATE_SETS_SQL,
    TemplateSet,
    stale_template_sets,
)
from analyzer_ng.llm.wiring import PgLlmFactLoader


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dsn", required=True, help="analyzer database DSN")
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--limit", type=int, default=200, help="max template sets to warm")
    ap.add_argument("--dry-run", action="store_true", help="list stale template sets, call nothing")
    ap.add_argument("--sleep-ms", type=int, default=0, help="pause between calls")
    ap.add_argument(
        "--ollama-url",
        default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"),
        help="LLM server base URL (default: $OLLAMA_URL or local forward)",
    )
    ap.add_argument(
        "--model",
        default=os.environ.get("ANALYZER_LLM_MODEL", "qwen3:4b-q4_K_M"),
        help="exact production model tag (default: $ANALYZER_LLM_MODEL)",
    )
    ap.add_argument(
        "--api",
        choices=["ollama", "openai"],
        default=os.environ.get("ANALYZER_LLM_API", "ollama"),
    )
    ap.add_argument(
        "--timeout-s", type=float, default=float(os.environ.get("ANALYZER_LLM_TIMEOUT_S", "20"))
    )
    ap.add_argument(
        "--num-ctx", type=int, default=int(os.environ.get("ANALYZER_LLM_NUM_CTX", "4096"))
    )
    return ap.parse_args()


def _enumerate(pool: ConnectionPool, project: int, limit: int) -> list[TemplateSet]:
    with pool.connection() as conn:
        rows = conn.execute(TEMPLATE_SETS_SQL, {"project": project}).fetchall()
        fresh = [
            r[0]
            for r in conn.execute(
                FRESH_EXTRACTOR_HASHES_SQL,
                {"project": project, "ttl_days": ExtractorRole.ttl_days},
            ).fetchall()
        ]
    return stale_template_sets(rows, fresh, limit=limit)


def main() -> int:
    args = _parse_args()
    pool = ConnectionPool(args.dsn, min_size=1, max_size=2)

    stale = _enumerate(pool, args.project, args.limit)
    print(
        f"project {args.project}: {len(stale)} template set(s) lacking a fresh "
        f"extractor cache row (limit {args.limit})",
        file=sys.stderr,
    )
    if args.dry_run:
        for ts in stale:
            print(
                f"would warm template_hash={ts.template_hash} via item {ts.item_id} "
                f"(exception_fp={ts.exception_fp}, {len(ts.template_ids)} template id(s))"
            )
        return 0
    if not stale:
        return 0

    client = OllamaClient(
        args.ollama_url,
        args.model,
        api=args.api,
        timeout_s=args.timeout_s,
        num_ctx=args.num_ctx,
    )
    probe = client.probe()
    if not probe.reachable:
        print(f"LLM server unreachable at {args.ollama_url} — nothing warmed", file=sys.stderr)
        return 2
    if not probe.model_present:
        print(f"model '{args.model}' not present at {args.ollama_url}", file=sys.stderr)
        return 2

    engine = LlmEngine(
        client=client,
        breaker=CircuitBreaker(),
        cache_store=PgLlmCacheStore(pool),
        event_store=PgLlmEventStore(pool),
        model=args.model,
    )
    loader = PgLlmFactLoader(PgLlmFacts(pool))
    role = ExtractorRole()

    outcomes: Counter[str] = Counter()
    aborted = False
    for i, ts in enumerate(stale, start=1):
        inp = loader("extractor", args.project, ts.item_id, {})
        if inp is None:
            outcomes["skipped_no_facts"] += 1
            print(f"[{i}/{len(stale)}] item {ts.item_id}: facts gone, skipped", file=sys.stderr)
            continue
        started = time.perf_counter()
        result = engine.run(role, args.project, ts.item_id, inp)
        duration_ms = int((time.perf_counter() - started) * 1000)
        outcomes[result.outcome] += 1
        print(
            f"[{i}/{len(stale)}] item {ts.item_id} template_hash={ts.template_hash}: "
            f"{result.outcome} in {duration_ms}ms" + (" (cache hit)" if result.cache_hit else ""),
            file=sys.stderr,
        )
        if result.outcome == "breaker_open":
            print("circuit breaker opened — stopping early", file=sys.stderr)
            aborted = True
            break
        if args.sleep_ms > 0 and i < len(stale):
            time.sleep(args.sleep_ms / 1000.0)

    client.close()
    summary = {
        "project": args.project,
        "stale_template_sets": len(stale),
        "outcomes": dict(outcomes),
        "validation_fail": outcomes.get("validation_fail", 0),
    }
    print(json.dumps(summary, indent=2))
    if summary["validation_fail"]:
        print(
            f"{summary['validation_fail']} extractor call(s) still end validation_fail "
            "— expected near zero after the ba23301 fix; inspect llm_event for these items.",
            file=sys.stderr,
        )
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
