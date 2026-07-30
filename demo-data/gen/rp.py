"""Thin ReportPortal client for the demo-data generator.

Reuses the plumbing proven in deploy/minikube/scripts/_lib.sh (password grant
against /uat, async v2 reporting API, api/v1 for numeric ids / defect_update /
analyze), but adds: admin project creation, per-project analyzer config toggling,
client-supplied (back-dated) timestamps, batched log posting, and -- importantly
for the laptop VM stand -- resilience:

  * every RP call retries with exponential backoff (5 tries, 2..30 s) on
    ReadTimeout / ConnectError / 5xx, with a 120 s per-call timeout;
  * the two non-idempotent creates (launch start, item start) re-check whether
    the entity actually landed before re-POSTing, so a lost response never
    duplicates a launch/item;
  * launch-level resume: a launch that already exists finished (matched on
    name + startTime) is skipped; an in-progress leftover from a crash is
    deleted by its exact queried id and re-uploaded cleanly.
"""

from __future__ import annotations

import datetime as dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from . import config

TIMEOUT = 120  # per-call read timeout (s)
BACKOFF = [2, 4, 8, 16, 30]  # retry sleeps; len == max attempts
_RETRYABLE_EXC = (
    requests.exceptions.ReadTimeout,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ConnectionError,
)


class _LandedError(Exception):
    """Raised internally when a create timed out but the entity is present."""

    def __init__(self, entity):
        self.entity = entity


def _ms(date: str, hour: int, sec: int) -> int:
    y, m, d = (int(x) for x in date.split("-"))
    base = dt.datetime(y, m, d, hour, 0, 0, tzinfo=dt.UTC)
    return int(base.timestamp() * 1000) + sec * 1000


def _to_ms(v) -> int | None:
    """Normalise an RP startTime (ISO string or epoch ms) to epoch ms."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return int(v)
    s = str(v).strip()
    if s.isdigit():
        return int(s)
    try:
        s2 = s.replace("Z", "+00:00")
        return int(dt.datetime.fromisoformat(s2).timestamp() * 1000)
    except ValueError:
        return None


class RPClient:
    def __init__(
        self, uat_base=config.UAT_BASE, api_base=config.API_BASE, log_workers=6, verbose=True
    ):
        self.uat = uat_base.rstrip("/")
        self.api = api_base.rstrip("/")
        self.s = requests.Session()
        self.token = None
        self.log_workers = log_workers
        self.verbose = verbose

    # -- resilient transport ----------------------------------------------
    def _send(self, method, url, *, find_existing=None, **kwargs):
        """Send with retry/backoff. Retries on timeout/conn-error/5xx.

        `find_existing` (for non-idempotent POSTs): a callable returning the
        already-landed entity (any truthy object) or None. If a create times out,
        we consult it before re-POSTing; a hit raises _LandedError(entity) so the
        caller can adopt it instead of creating a duplicate.
        """
        kwargs.setdefault("timeout", TIMEOUT)
        last = None
        for attempt in range(len(BACKOFF)):
            try:
                r = self.s.request(method, url, **kwargs)
                if r.status_code >= 500:
                    last = RuntimeError(f"{r.status_code} {url}: {r.text[:200]}")
                    self._log_retry(method, url, attempt, f"HTTP {r.status_code}")
                else:
                    return r
            except _RETRYABLE_EXC as e:
                last = e
                if find_existing is not None:
                    landed = find_existing()
                    if landed:
                        raise _LandedError(landed)
                self._log_retry(method, url, attempt, type(e).__name__)
            if attempt < len(BACKOFF) - 1:
                time.sleep(BACKOFF[attempt])
        raise last if last else RuntimeError(f"exhausted retries: {method} {url}")

    def _log_retry(self, method, url, attempt, reason):
        if self.verbose:
            print(
                f"    retry {attempt + 1}/{len(BACKOFF)} {method} "
                f"{url.rsplit('/', 2)[-2:] and '/'.join(url.rsplit('/', 2)[-2:])} "
                f"({reason}); backoff {BACKOFF[attempt]}s",
                file=sys.stderr,
                flush=True,
            )

    # -- auth --------------------------------------------------------------
    def login(self):
        r = self._send(
            "POST",
            f"{self.uat}/uat/sso/oauth/token",
            headers={"Authorization": f"Basic {config.OAUTH_BASIC}"},
            data={
                "grant_type": "password",
                "username": config.SUPERADMIN,
                "password": config.SUPERPW,
            },
        )
        r.raise_for_status()
        self.token = r.json()["access_token"]
        self.s.headers.update({"Authorization": f"Bearer {self.token}"})
        return self.token

    # -- admin -------------------------------------------------------------
    def ensure_project(self, name: str) -> bool:
        r = self._send(
            "POST",
            f"{self.api}/api/v1/project",
            json={"projectName": name, "entryType": "INTERNAL"},
        )
        if r.status_code in (200, 201):
            return True
        if r.status_code == 409 or "exist" in r.text.lower():
            return False
        r.raise_for_status()
        return False

    def set_analyzer(self, project: str, enabled: bool, mode="ALL"):
        self._send(
            "PUT",
            f"{self.api}/api/v1/project/{project}",
            json={
                "configuration": {
                    "attributes": {
                        "analyzer.isAutoAnalyzerEnabled": "true" if enabled else "false",
                        "analyzer.autoAnalyzerMode": mode,
                        "analyzer.minShouldMatch": "5",
                        "analyzer.numberOfLogLines": "-1",
                        "analyzer.indexingRunning": "true",
                    }
                }
            },
        )

    # -- resume / lookup ---------------------------------------------------
    def find_launch(self, project, name, start_ms) -> dict | None:
        """Return {id,uuid,status} for a launch matching name+startTime, else None."""
        r = self._send(
            "GET",
            f"{self.api}/api/v1/{project}/launch",
            params={"filter.eq.name": name, "page.size": 100},
        )
        if not r.ok:
            return None
        for l in r.json().get("content", []):
            if l.get("name") == name and _to_ms(l.get("startTime")) == start_ms:
                return {"id": l.get("id"), "uuid": l.get("uuid"), "status": l.get("status")}
        return None

    def find_item(self, project, launch_id, name, start_ms) -> str | None:
        """Return the uuid of an item matching launchId+name+startTime, else None."""
        if launch_id is None:
            return None
        r = self._send(
            "GET",
            f"{self.api}/api/v1/{project}/item",
            params={"filter.eq.launchId": launch_id, "filter.eq.name": name, "page.size": 100},
        )
        if not r.ok:
            return None
        for it in r.json().get("content", []):
            if it.get("name") == name and _to_ms(it.get("startTime")) == start_ms:
                return it.get("uuid")
        return None

    def delete_launch(self, project, launch_id):
        self._send("DELETE", f"{self.api}/api/v1/{project}/launch/{launch_id}")

    # -- reporting ---------------------------------------------------------
    def report_launch(self, planned) -> dict:
        """Upload one PlannedLaunch (items + logs) and finish it, idempotently.

        Returns one of:
          {"skipped": True, "reason": "...", ...}          -- already finished
          {"luuid", "lid", "project", "items": [(iuuid, planned_item)]}
        """
        la = planned
        lstart = _ms(la.date, la.hour, 0)

        # --- resume: skip finished, clean in-progress leftovers ----------
        existing = self.find_launch(la.project, la.name, lstart)
        if existing:
            status = (existing.get("status") or "").upper()
            if status in ("PASSED", "FAILED", "FINISHED", "STOPPED"):
                return {
                    "skipped": True,
                    "reason": f"exists {status}",
                    "luuid": existing.get("uuid"),
                    "lid": existing.get("id"),
                    "project": la.project,
                    "items": [],
                }
            # IN_PROGRESS / INTERRUPTED crash leftover -> delete by exact id, redo
            if existing.get("id") is not None:
                self.delete_launch(la.project, existing["id"])

        # --- create launch (non-idempotent; adopt on lost response) ------
        attrs = list(la.attributes) + [{"key": "demo", "value": "analyzer-ng"}]
        body = {
            "name": la.name,
            "startTime": lstart,
            "mode": "DEFAULT",
            "attributes": attrs,
            "description": f"[{la.date}] phase {la.phase} demo launch",
        }
        try:
            r = self._send(
                "POST",
                f"{self.api}/api/v2/{la.project}/launch",
                json=body,
                find_existing=lambda: self.find_launch(la.project, la.name, lstart),
            )
            r.raise_for_status()
            luuid = r.json()["id"]
        except _LandedError as e:
            luuid = e.entity["uuid"]  # the create landed but the ack was lost

        lid = self.numeric_launch(la.project, luuid)  # for item-adoption lookups

        reported = []
        log_jobs = []
        sec = 1
        for pi in la.items:
            it = pi.item
            istart = _ms(la.date, la.hour, sec)
            ib = {
                "name": it.test_name,
                "startTime": istart,
                "type": "STEP",
                "launchUuid": luuid,
                "attributes": self._item_attrs(it),
            }
            try:
                ir = self._send(
                    "POST",
                    f"{self.api}/api/v2/{la.project}/item",
                    json=ib,
                    find_existing=lambda n=it.test_name, s=istart: self.find_item(
                        la.project, lid, n, s
                    ),
                )
                ir.raise_for_status()
                iuuid = ir.json()["id"]
            except _LandedError as e:
                iuuid = e.entity  # item create landed; adopt its uuid
            lt = istart + 1
            for lg in it.logs:
                log_jobs.append((la.project, iuuid, luuid, lt, lg.level, lg.message))
                lt += 500
            status = "PASSED" if it.status == "passed" else "FAILED"
            fin = {"launchUuid": luuid, "endTime": istart + 30, "status": status}
            if status == "FAILED":
                fin["issue"] = {"issueType": "ti001", "autoAnalyzed": False}
            self._send("PUT", f"{self.api}/api/v2/{la.project}/item/{iuuid}", json=fin)
            reported.append((iuuid, pi))
            sec += 2

        self._post_logs(log_jobs)
        self._send(
            "PUT",
            f"{self.api}/api/v2/{la.project}/launch/{luuid}/finish",
            json={"endTime": _ms(la.date, la.hour, sec + 5)},
        )
        return {"luuid": luuid, "lid": lid, "project": la.project, "items": reported}

    def _item_attrs(self, it) -> list[dict]:
        attrs = [{"key": "archetype", "value": it.archetype_id}]
        for s in it.scenario_refs:
            attrs.append({"key": "scenario", "value": s})
        if it.adv_case:
            attrs.append({"key": "adv_case", "value": it.adv_case})
        if it.ground_truth:
            attrs.append({"key": "ground_truth", "value": it.ground_truth})
        return attrs

    def _post_logs(self, jobs):
        if not jobs:
            return

        def one(job):
            project, iuuid, luuid, t, level, msg = job
            body = {
                "itemUuid": iuuid,
                "launchUuid": luuid,
                "time": t,
                "level": level.upper(),
                "message": msg,
            }
            try:
                self._send("POST", f"{self.api}/api/v2/{project}/log", json=body)
            except Exception as e:  # pragma: no cover
                print(f"  log err (dropped): {e}", file=sys.stderr)

        with ThreadPoolExecutor(max_workers=self.log_workers) as ex:
            list(ex.map(one, jobs))

    # -- numeric ids / feedback / analysis --------------------------------
    def numeric_launch(self, project, luuid, retries=8) -> int | None:
        for _ in range(retries):
            r = self._send("GET", f"{self.api}/api/v1/{project}/launch/uuid/{luuid}")
            if r.ok and r.json().get("id"):
                return r.json()["id"]
            time.sleep(1)
        return None

    def numeric_item(self, project, iuuid, retries=8) -> int | None:
        for _ in range(retries):
            r = self._send("GET", f"{self.api}/api/v1/{project}/item/uuid/{iuuid}")
            if r.ok and r.json().get("id"):
                return r.json()["id"]
            time.sleep(1)
        return None

    def defect_update(self, project, issues: list[dict]):
        if not issues:
            return
        self._send("PUT", f"{self.api}/api/v1/{project}/item", json={"issues": issues})

    def replay_history_labels(self, project, reported, roles=("H",)):
        issues = []
        for iuuid, pi in reported:
            if pi.role not in roles:
                continue
            gt = pi.item.ground_truth
            if not gt or gt == "ti":
                continue
            nid = self.numeric_item(project, iuuid)
            if nid is None:
                continue
            issues.append(
                {
                    "testItemId": nid,
                    "issue": {
                        "issueType": config.ISSUE_LOCATOR[gt],
                        "autoAnalyzed": False,
                        "ignoreAnalyzer": False,
                        "comment": f"demo triage {pi.item.archetype_id}",
                    },
                }
            )
        for i in range(0, len(issues), 50):
            self.defect_update(project, issues[i : i + 50])
        return len(issues)

    def analyze(self, project, launch_id):
        self._send(
            "POST",
            f"{self.api}/api/v1/{project}/launch/analyze",
            json={
                "launchId": launch_id,
                "analyzerMode": "ALL",
                "analyzerTypeName": "autoAnalyzer",
                "analyzeItemsMode": ["TO_INVESTIGATE"],
            },
        )

    def suggest(self, project, item_id):
        r = self._send("GET", f"{self.api}/api/v1/{project}/item/suggest/{item_id}")
        return r.json() if r.ok else None

    # -- route calls (Phase 5, S44) ---------------------------------------
    def cluster(self, project, launch_id):
        r = self._send(
            "POST",
            f"{self.api}/api/v1/{project}/launch/cluster",
            json={
                "launchId": launch_id,
                "project": project,
                "analyzerMode": "ALL",
                "removeNumbers": False,
            },
        )
        return r.status_code, (r.text[:200] if r.text else "")

    def suggest_patterns(self, project, launch_id):
        r = self._send("GET", f"{self.api}/api/v1/{project}/launch/{launch_id}/suggest_patterns")
        return r.status_code, (r.text[:200] if r.text else "")

    # -- verification helpers ---------------------------------------------
    def item_log_count(self, project, item_id):
        r = self._send(
            "GET",
            f"{self.api}/api/v1/{project}/log",
            params={"filter.eq.item": item_id, "page.size": 1},
        )
        if r.ok:
            return r.json().get("page", {}).get("totalElements")
        return None

    def list_launch_items(self, project, launch_id) -> dict:
        """Return {(name, start_ms): numeric_id} for every leaf item in a launch."""
        out: dict = {}
        by_name: dict = {}
        page = 1
        while True:
            r = self._send(
                "GET",
                f"{self.api}/api/v1/{project}/item",
                params={"filter.eq.launchId": launch_id, "page.size": 100, "page.page": page},
            )
            if not r.ok:
                break
            d = r.json()
            content = d.get("content", [])
            for it in content:
                nm = it.get("name")
                sms = _to_ms(it.get("startTime"))
                nid = it.get("id")
                out[(nm, sms)] = nid
                by_name.setdefault(nm, []).append(nid)
            pg = d.get("page", {})
            if not content or page >= pg.get("totalPages", 1):
                break
            page += 1
        out["__by_name__"] = by_name
        return out

    def list_launch_items_full(self, project, launch_id) -> list[dict]:
        """Every leaf item in a launch as {id, name, ground_truth, issue_type}.

        ground_truth is read from the item's stamped attribute -- authoritative
        and independent of any client-side plan ordering.
        """
        out = []
        page = 1
        while True:
            r = self._send(
                "GET",
                f"{self.api}/api/v1/{project}/item",
                params={"filter.eq.launchId": launch_id, "page.size": 100, "page.page": page},
            )
            if not r.ok:
                break
            d = r.json()
            content = d.get("content", [])
            for it in content:
                gt = None
                for a in it.get("attributes") or []:
                    if a.get("key") == "ground_truth":
                        gt = a.get("value")
                out.append(
                    {
                        "id": it.get("id"),
                        "name": it.get("name"),
                        "ground_truth": gt,
                        "issue_type": (it.get("issue") or {}).get("issueType"),
                        "status": it.get("status"),
                    }
                )
            pg = d.get("page", {})
            if not content or page >= pg.get("totalPages", 1):
                break
            page += 1
        return out

    def project_launch_count(self, project):
        r = self._send("GET", f"{self.api}/api/v1/{project}/launch", params={"page.size": 1})
        return r.json().get("page", {}).get("totalElements") if r.ok else None

    def project_item_count(self, project):
        r = self._send(
            "GET",
            f"{self.api}/api/v1/{project}/item",
            params={"page.size": 1, "filter.eq.hasChildren": "false"},
        )
        return r.json().get("page", {}).get("totalElements") if r.ok else None
