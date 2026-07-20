#!/usr/bin/env python3
"""Migrate ReportPortal launches between instances via the public API.

Reads launches (structure, description, attributes, nested suites/tests/steps,
logs, attachments) from a SOURCE instance and replays them into a TARGET
instance through the async v2 reporting API — so the target's analyzer
(analyzer-ng) ingests them exactly like a live report.

Defect types (analysis results) are transferred as a separate, explicit
post-finish triage replay (RP ``PUT /item`` defect update), because that is the
path that emits ``defect_update`` AMQP events — the signal the ML feedback loop
learns from. Launches listed in ``--skip-defects-launches`` (or all, with
``--skip-all-defects``) keep their items To-Investigate.

Custom defect subtypes present on migrated items are created on the target
project first (same group / long name / short name / color); locators are
re-mapped source→target automatically.

Config lives in an env file (default ``.env`` next to this script), see
``.env.example``. Selection: ``--launch-ids 12,15`` or ``--from 2026-07-01
[--to 2026-07-15]``.

Optionally, with ``ANALYZER_PG_EXEC`` configured, the script verifies that the
analyzer actually consumed the replayed labels (``analyzer.label_event`` rows
per migrated item) and retries the defect replay once for items that raced
indexing.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HERE = Path(__file__).resolve().parent

DEFAULT_ISSUE_LOCATORS = {"pb001", "ab001", "si001", "nd001", "ti001"}
TI_GROUP = "TO_INVESTIGATE"
BATCH_DEFECTS = 20
PAGE_SIZE = 200


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        sys.exit(f"env file not found: {path} (copy .env.example and fill it in)")
    env: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


class RP:
    """Minimal RP API client (API-key auth, retrying session)."""

    def __init__(self, base_url: str, project: str, api_key: str, tag: str) -> None:
        self.base = base_url.rstrip("/")
        self.project = project
        self.tag = tag
        self.s = requests.Session()
        retry = Retry(
            total=5, backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "PUT"],
        )
        self.s.mount("http://", HTTPAdapter(max_retries=retry))
        self.s.mount("https://", HTTPAdapter(max_retries=retry))
        self.s.headers["Authorization"] = f"Bearer {api_key}"

    # -- plumbing ---------------------------------------------------------- #
    def get(self, path: str, **params: Any) -> dict:
        r = self.s.get(f"{self.base}{path}", params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    def get_bytes(self, path: str) -> tuple[bytes, str]:
        r = self.s.get(f"{self.base}{path}", timeout=120)
        r.raise_for_status()
        return r.content, r.headers.get("Content-Type", "application/octet-stream")

    def post(self, path: str, body: dict, **kw: Any) -> dict:
        r = self.s.post(f"{self.base}{path}", json=body, timeout=120, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"[{self.tag}] POST {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    def put(self, path: str, body: dict) -> dict:
        r = self.s.put(f"{self.base}{path}", json=body, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"[{self.tag}] PUT {path} -> {r.status_code}: {r.text[:300]}")
        return r.json() if r.text else {}

    def paged(self, path: str, **params: Any):
        page = 1
        while True:
            data = self.get(path, **{**params, "page.page": page, "page.size": PAGE_SIZE})
            content = data.get("content", [])
            yield from content
            total = data.get("page", {}).get("totalPages", 1)
            if page >= total or not content:
                return
            page += 1

    # -- domain ------------------------------------------------------------ #
    def ping(self) -> None:
        self.get(f"/api/v1/{self.project}/launch", **{"page.size": 1})

    def launch_by_id(self, launch_id: int) -> dict:
        return self.get(f"/api/v1/{self.project}/launch/{launch_id}")

    def launches_between(self, ts_from: int, ts_to: int):
        yield from self.paged(
            f"/api/v1/{self.project}/launch",
            **{
                "filter.gte.startTime": ts_from,
                "filter.lte.startTime": ts_to,
                "page.sort": "startTime,ASC",
            },
        )

    def launch_exists(self, name: str, start_time: Any) -> bool:
        for la in self.paged(
            f"/api/v1/{self.project}/launch", **{"filter.eq.name": name}
        ):
            if norm_ts(la.get("startTime")) == norm_ts(start_time):
                return True
        return False

    def items_of_launch(self, launch_id: int) -> list[dict]:
        items = list(
            self.paged(
                f"/api/v1/{self.project}/item",
                **{"filter.eq.launchId": launch_id, "page.sort": "id,ASC"},
            )
        )
        # Some 5.x builds omit nested (hasStats=false) steps from the flat
        # listing — fetch children per parent for any gap.
        seen = {it["id"] for it in items}
        frontier = [it for it in items if it.get("hasChildren")]
        while frontier:
            parent = frontier.pop()
            for child in self.paged(
                f"/api/v1/{self.project}/item",
                **{"filter.eq.parentId": parent["id"], "page.sort": "id,ASC"},
            ):
                if child["id"] in seen:
                    continue
                seen.add(child["id"])
                items.append(child)
                if child.get("hasChildren"):
                    frontier.append(child)
        items.sort(key=lambda it: it["id"])
        return items

    def logs_of_item(self, item_id: int) -> list[dict]:
        return list(
            self.paged(
                f"/api/v1/{self.project}/log",
                **{"filter.eq.item": item_id, "page.sort": "logTime,ASC"},
            )
        )

    def settings(self) -> dict:
        return self.get(f"/api/v1/{self.project}/settings")

    def create_subtype(self, type_ref: str, long_name: str, short_name: str, color: str) -> str:
        out = self.post(
            f"/api/v1/{self.project}/settings/sub-type",
            {"typeRef": type_ref, "longName": long_name,
             "shortName": short_name, "color": color},
        )
        return out.get("locator") or out.get("id") or ""

    def resolve_uuid(self, uuid: str) -> int:
        return int(self.get(f"/api/v1/{self.project}/item/uuid/{uuid}")["id"])

    def item(self, item_id: int) -> dict:
        return self.get(f"/api/v1/{self.project}/item/{item_id}")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def norm_ts(value: Any) -> int:
    """RP returns startTime as epoch-millis int or ISO string; normalize to ms."""
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return 0


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat().replace("+00:00", "Z")


def clean_attributes(attrs: list[dict] | None) -> list[dict]:
    out = []
    for a in attrs or []:
        if a.get("system"):
            continue
        entry = {"value": a.get("value", "")}
        if a.get("key"):
            entry["key"] = a["key"]
        out.append(entry)
    return out


def parse_date(text: str) -> int:
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def drop_passed(items: list[dict]) -> list[dict]:
    """Filter out PASSED subtrees (``--skip-passed``).

    An item survives when its own status is not PASSED or when any descendant
    survives — so containers stay as long as they hold anything worth
    transferring, and a kept child always keeps its ancestor chain (defensive:
    RP derives container status from children, but retries/interruptions can
    disagree). Only PASSED is dropped; FAILED/SKIPPED/INTERRUPTED/… transfer."""
    children: dict[Any, list[dict]] = {}
    for it in items:
        children.setdefault(it.get("parent"), []).append(it)
    keep: set[int] = set()

    def visit(it: dict) -> bool:
        kept_child = False
        for child in children.get(it["id"], []):
            kept_child = visit(child) or kept_child
        if kept_child or (it.get("status") or "").upper() != "PASSED":
            keep.add(it["id"])
            return True
        return False

    for root in children.get(None, []):
        visit(root)
    return [it for it in items if it["id"] in keep]


# --------------------------------------------------------------------------- #
# defect-type sync
# --------------------------------------------------------------------------- #
def sync_defect_types(src: RP, dst: RP, needed_locators: set[str]) -> dict[str, str]:
    """Ensure every needed source locator has an equivalent on the target.

    Returns source locator -> target locator. Built-in defaults map 1:1;
    custom subtypes are matched by (group, longName) and created when missing
    with the same longName/shortName/color.
    """
    mapping = {loc: loc for loc in needed_locators & DEFAULT_ISSUE_LOCATORS}
    custom = needed_locators - DEFAULT_ISSUE_LOCATORS
    if not custom:
        return mapping

    def flat(settings: dict) -> dict[str, dict]:
        out = {}
        for group, entries in (settings.get("subTypes") or {}).items():
            for e in entries:
                out[e["locator"]] = {**e, "group": group}
        return out

    src_types = flat(src.settings())
    dst_types = flat(dst.settings())
    dst_by_name = {
        (t["group"], t["longName"].strip().lower()): loc
        for loc, t in dst_types.items()
    }
    for loc in sorted(custom):
        st = src_types.get(loc)
        if not st:
            print(f"  ! source defect type {loc} not found in settings; keeping locator as-is")
            mapping[loc] = loc
            continue
        key = (st["group"], st["longName"].strip().lower())
        if key in dst_by_name:
            mapping[loc] = dst_by_name[key]
            continue
        new_loc = dst.create_subtype(
            st["group"], st["longName"], st.get("shortName", st["longName"][:4].upper()),
            st.get("color", "#777777"),
        )
        print(f"  + created defect type on target: {st['longName']} ({st['group']}) -> {new_loc}")
        mapping[loc] = new_loc or loc
        dst_by_name[key] = mapping[loc]
    return mapping


# --------------------------------------------------------------------------- #
# migration of one launch
# --------------------------------------------------------------------------- #
def migrate_launch(
    src: RP, dst: RP, launch: dict, items: list[dict], *, transfer_attachments: bool
) -> tuple[str, dict[int, str], dict[int, str]]:
    """Replay one launch. Returns (launch_uuid, src_item_id -> dst uuid,
    src_item_id -> source issue locator for later defect replay)."""
    lid = launch["id"]
    name = launch.get("name", f"launch {lid}")
    start_ms = norm_ts(launch.get("startTime"))
    body = {
        "name": name,
        "startTime": iso(start_ms),
        "mode": launch.get("mode", "DEFAULT"),
        "description": launch.get("description") or "",
        "attributes": clean_attributes(launch.get("attributes")),
    }
    luuid = dst.post(f"/api/v2/{dst.project}/launch", body)["id"]
    print(f"  {len(items)} items")
    uuid_of: dict[int, str] = {}
    issue_of: dict[int, str] = {}
    order: list[dict] = []

    for it in items:  # sorted by id => parents precede children
        parent_uuid = uuid_of.get(it.get("parent")) if it.get("parent") else None
        item_body: dict[str, Any] = {
            "name": it.get("name", "")[:1024],
            "startTime": iso(norm_ts(it.get("startTime"))),
            "type": it.get("type", "STEP"),
            "launchUuid": luuid,
            "description": it.get("description") or "",
            "attributes": clean_attributes(it.get("attributes")),
        }
        if it.get("parameters"):
            item_body["parameters"] = [
                {"key": p.get("key", ""), "value": p.get("value", "")}
                for p in it["parameters"]
            ]
        if it.get("codeRef"):
            item_body["codeRef"] = it["codeRef"]
        if it.get("hasStats") is False:
            item_body["hasStats"] = False
        if it.get("retry") or it.get("retryOf"):
            item_body["retry"] = True
        path = f"/api/v2/{dst.project}/item" + (f"/{parent_uuid}" if parent_uuid else "")
        uuid_of[it["id"]] = dst.post(path, item_body)["id"]
        order.append(it)

        issue = (it.get("issue") or {})
        if issue.get("issueType"):
            issue_of[it["id"]] = issue["issueType"]
            issue_of[-it["id"]] = json.dumps(  # side-channel: comment/ignore flags
                {"comment": issue.get("comment") or "",
                 "ignoreAnalyzer": bool(issue.get("ignoreAnalyzer"))}
            )

        # logs (nested steps carry logs too)
        for log in src.logs_of_item(it["id"]):
            replay_log(src, dst, log, luuid, uuid_of[it["id"]], transfer_attachments)

    # finish children before parents (reverse id order), then the launch
    for it in reversed(order):
        end_ms = norm_ts(it.get("endTime")) or norm_ts(it.get("startTime"))
        fin: dict[str, Any] = {"endTime": iso(end_ms), "launchUuid": luuid}
        if it.get("status"):
            fin["status"] = it["status"]
        dst.put(f"/api/v2/{dst.project}/item/{uuid_of[it['id']]}", fin)

    end_ms = norm_ts(launch.get("endTime")) or start_ms
    dst.put(f"/api/v2/{dst.project}/launch/{luuid}/finish", {"endTime": iso(end_ms)})
    return luuid, uuid_of, issue_of


def replay_log(
    src: RP, dst: RP, log: dict, luuid: str, item_uuid: str, transfer_attachments: bool
) -> None:
    body = {
        "launchUuid": luuid,
        "itemUuid": item_uuid,
        "time": iso(norm_ts(log.get("logTime") or log.get("time"))),
        "message": log.get("message", ""),
        "level": (log.get("level") or "INFO").lower(),
    }
    binary = log.get("binaryContent") or {}
    if transfer_attachments and binary.get("id"):
        try:
            blob, ctype = src.get_bytes(f"/api/v1/data/{src.project}/{binary['id']}")
            fname = f"attachment-{binary['id']}"
            body["file"] = {"name": fname}
            r = dst.s.post(
                f"{dst.base}/api/v2/{dst.project}/log",
                files={
                    "json_request_part": (None, json.dumps([body]), "application/json"),
                    "file": (fname, blob, ctype),
                },
                timeout=120,
            )
            if r.status_code < 400:
                return
            print(f"    ! attachment upload {r.status_code}; sending text-only")
            body.pop("file", None)
        except Exception as exc:  # noqa: BLE001 - degrade to text-only
            print(f"    ! attachment transfer failed ({exc}); sending text-only")
            body.pop("file", None)
    dst.post(f"/api/v2/{dst.project}/log", body)


# --------------------------------------------------------------------------- #
# defect replay + analyzer verification
# --------------------------------------------------------------------------- #
def replay_defects(
    dst: RP,
    uuid_of: dict[int, str],
    issue_of: dict[int, str],
    locator_map: dict[str, str],
) -> dict[int, str]:
    """Apply source defect types on the target via bulk defect update.

    Returns dst_item_id -> applied locator. Emits RP ``defect_update`` events —
    the exact feedback signal the analyzer's ML loop consumes.
    """
    updates: list[tuple[int, dict]] = []
    applied: dict[int, str] = {}
    for src_id, locator in issue_of.items():
        if src_id < 0:
            continue
        target_loc = locator_map.get(locator, locator)
        # ti001 is the default state — replaying it is a no-op and would only
        # spam defect_update; skip TI.
        if target_loc.lower().startswith("ti"):
            continue
        meta = json.loads(issue_of.get(-src_id, "{}") or "{}")
        dst_id = dst.resolve_uuid(uuid_of[src_id])
        applied[dst_id] = target_loc
        updates.append(
            (
                dst_id,
                {
                    "testItemId": dst_id,
                    "issue": {
                        "issueType": target_loc,
                        "comment": meta.get("comment", "") or "migrated triage",
                        "ignoreAnalyzer": meta.get("ignoreAnalyzer", False),
                        "autoAnalyzed": False,
                    },
                },
            )
        )
    for i in range(0, len(updates), BATCH_DEFECTS):
        chunk = [u[1] for u in updates[i : i + BATCH_DEFECTS]]
        dst.put(f"/api/v1/{dst.project}/item", {"issues": chunk})
    return applied


def analyzer_label_events(pg_exec: str, item_ids: list[int]) -> int:
    """Count analyzer.label_event rows for the given target item ids using the
    operator-provided command template (``{sql}`` placeholder)."""
    total = 0
    for i in range(0, len(item_ids), 500):
        ids = ",".join(str(x) for x in item_ids[i : i + 500])
        sql = f"select count(distinct item_id) from analyzer.label_event where item_id in ({ids})"
        cmd = [part.replace("{sql}", sql) for part in shlex.split(pg_exec)]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if out.returncode != 0:
            print(f"  ! analyzer verification command failed: {out.stderr.strip()[:200]}")
            return -1
        total += int(out.stdout.strip().splitlines()[-1] or 0)
    return total


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env", default=str(HERE / ".env"), help="env file path")
    sel = ap.add_mutually_exclusive_group(required=True)
    sel.add_argument("--launch-ids", help="comma-separated SOURCE launch ids")
    sel.add_argument("--from", dest="date_from", help="ISO date/datetime lower bound")
    ap.add_argument("--to", dest="date_to", help="ISO upper bound (default: now)")
    ap.add_argument("--skip-defects-launches", default="",
                    help="source launch ids whose analysis results must NOT be transferred")
    ap.add_argument("--skip-all-defects", action="store_true",
                    help="transfer no analysis results at all")
    ap.add_argument("--skip-passed", action="store_true",
                    help="do not transfer PASSED tests/steps (whole passed subtrees "
                         "are dropped; containers survive while they hold non-passed "
                         "descendants)")
    ap.add_argument("--no-attachments", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="list what would be migrated")
    args = ap.parse_args()

    env = load_env(Path(args.env))
    for req in ("SRC_URL", "SRC_PROJECT", "SRC_API_KEY", "DST_URL", "DST_PROJECT", "DST_API_KEY"):
        if not env.get(req):
            sys.exit(f"missing {req} in {args.env}")
    src = RP(env["SRC_URL"], env["SRC_PROJECT"], env["SRC_API_KEY"], "src")
    dst = RP(env["DST_URL"], env["DST_PROJECT"], env["DST_API_KEY"], "dst")
    index_wait = int(env.get("INDEX_WAIT_S", "20"))
    pg_exec = env.get("ANALYZER_PG_EXEC", "").strip()

    src.ping()
    dst.ping()
    print(f"source: {src.base} / {src.project}\ntarget: {dst.base} / {dst.project}")

    if args.launch_ids:
        launches = [src.launch_by_id(int(x)) for x in args.launch_ids.split(",") if x.strip()]
    else:
        ts_from = parse_date(args.date_from)
        ts_to = parse_date(args.date_to) if args.date_to else int(time.time() * 1000)
        launches = list(src.launches_between(ts_from, ts_to))
    print(f"{len(launches)} source launch(es) selected")
    if args.dry_run:
        for la in launches:
            print(f"  [{la['id']}] {la['name']} #{la.get('number')} @ {la.get('startTime')}")
        return

    skip_defects = {int(x) for x in args.skip_defects_launches.split(",") if x.strip()}

    # Pre-scan needed defect locators (only for launches whose defects transfer).
    needed: set[str] = set()
    per_launch_items: dict[int, list[dict]] = {}
    for la in launches:
        items = src.items_of_launch(la["id"])
        if args.skip_passed:
            kept = drop_passed(items)
            if len(kept) != len(items):
                print(f"  [{la['id']}] --skip-passed: {len(items) - len(kept)} of "
                      f"{len(items)} item(s) dropped (passed subtrees)")
            items = kept
        per_launch_items[la["id"]] = items
        if args.skip_all_defects or la["id"] in skip_defects:
            continue
        for it in items:
            loc = (it.get("issue") or {}).get("issueType")
            if loc:
                needed.add(loc)
    locator_map = sync_defect_types(src, dst, needed) if needed else {}

    all_applied: dict[int, str] = {}
    pending_replays: list[tuple[dict[int, str], dict[int, str]]] = []
    for la in launches:
        if dst.launch_exists(la.get("name", ""), la.get("startTime")):
            print(f"SKIP [{la['id']}] {la['name']} — already on target (same name+startTime)")
            continue
        print(f"migrating [{la['id']}] {la['name']} #{la.get('number')}")
        _, uuid_of, issue_of = migrate_launch(
            src, dst, la, per_launch_items[la["id"]],
            transfer_attachments=not args.no_attachments,
        )
        if not args.skip_all_defects and la["id"] not in skip_defects and issue_of:
            pending_replays.append((uuid_of, issue_of))

    if pending_replays:
        print(f"waiting {index_wait}s for target indexing before defect replay "
              "(defect_update for a not-yet-indexed item is dropped by the analyzer)")
        time.sleep(index_wait)
        for uuid_of, issue_of in pending_replays:
            all_applied.update(replay_defects(dst, uuid_of, issue_of, locator_map))
        print(f"defects replayed on {len(all_applied)} item(s)")

        # RP-side confirmation
        missing = [i for i, loc in all_applied.items()
                   if dst.item(i).get("issue", {}).get("issueType") != loc]
        if missing:
            print(f"  ! RP did not confirm {len(missing)} defect update(s): {missing[:10]}")

        # analyzer-side confirmation (the ML actually ate the labels)
        if pg_exec:
            ids = list(all_applied)
            got = analyzer_label_events(pg_exec, ids)
            if got >= 0:
                print(f"analyzer label_event coverage: {got}/{len(ids)} item(s)")
                if got < len(ids):
                    print(f"  retrying defect replay once after {index_wait}s "
                          "(indexing race)…")
                    time.sleep(index_wait)
                    for uuid_of, issue_of in pending_replays:
                        replay_defects(dst, uuid_of, issue_of, locator_map)
                    got = analyzer_label_events(pg_exec, ids)
                    print(f"analyzer label_event coverage after retry: {got}/{len(ids)}")
        else:
            print("ANALYZER_PG_EXEC not set — skipped analyzer-side verification "
                  "(see README for the one-liner)")

    print("done")


if __name__ == "__main__":
    main()
