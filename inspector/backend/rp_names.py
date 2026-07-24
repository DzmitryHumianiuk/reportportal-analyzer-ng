"""Resolve real names from ReportPortal's OWN PostgreSQL (OPTIONAL).

The analyzer database stores only locator strings (``pb001``, ``si001`` …) and
numeric ``project_id``s. The human-facing names live in ReportPortal's own
database:

* ``project(id, name)`` — e.g. ``1 -> "superadmin_personal"``.
* ``issue_type(locator, issue_name, abbreviation, hex_color)`` joined via
  ``issue_type_project`` to a project — e.g. project 1, ``pb001 ->
  {"Product Bug", "PB", "#d32f2f"}``.

This module loads those two maps once and caches them with a short TTL. It is
strictly OPTIONAL: when ``INSPECTOR_RP_PG_DSN`` is unset, unreachable, or a name
is missing, callers fall back to the raw locator / numeric id and the UI shows a
data-driven ``RP names: unavailable`` status note — never an invented name.

The connection is opened read-only with a short timeout so a slow/absent RP
database can never wedge an inspector request.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row

_DEFAULT_TTL = 60.0
_CONNECT_TIMEOUT = 3
_STATEMENT_TIMEOUT_MS = 4000

# The two read-only lookups. issue_type columns in the RP schema are
# ``issue_name`` (the long name) and ``abbreviation`` (the short name).
_PROJECT_SQL = "SELECT id, name FROM project"
_ISSUE_TYPE_SQL = (
    "SELECT itp.project_id, it.locator, it.issue_name, it.abbreviation, it.hex_color "
    "FROM issue_type_project itp "
    "JOIN issue_type it ON it.id = itp.issue_type_id"
)
# Per-item launch + ancestor path (an ltree of item ids whose last label is the
# item's own id). Used to build the RP UI deep-link. item->launch/path is
# immutable in RP, so results are memoized permanently (no TTL).
_ITEM_PATH_SQL = (
    "SELECT item_id, launch_id, path::text AS path FROM test_item WHERE item_id = ANY(%s)"
)


@dataclass(frozen=True)
class RPStatus:
    """Honest, data-driven status of RP name resolution for the UI header."""

    configured: bool
    reachable: bool
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {"configured": self.configured, "reachable": self.reachable, "note": self.note}


class RPNameResolver:
    """Loads and caches per-project name maps from ReportPortal's PostgreSQL.

    All public methods are safe to call whether or not a DSN is configured and
    whether or not RP is reachable; they degrade to empty maps + an honest
    status. The cache is refreshed lazily on access once older than ``ttl``.
    """

    def __init__(self, dsn: str | None, *, ttl: float = _DEFAULT_TTL) -> None:
        self._dsn = dsn
        self._ttl = ttl
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self._projects: dict[int, str] = {}
        self._defects: dict[int, dict[str, dict[str, Any]]] = {}
        # item_id -> (launch_id, [path segments]); immutable, memoized forever.
        self._item_meta: dict[int, tuple[int, list[str]]] = {}
        self._reachable = False
        self._error: str | None = None

    # -- loading ---------------------------------------------------------- #
    @property
    def configured(self) -> bool:
        return bool(self._dsn)

    def _load(self) -> None:
        """(Re)load both maps from RP. Raises on any connection/query error."""
        projects: dict[int, str] = {}
        defects: dict[int, dict[str, dict[str, Any]]] = {}
        with psycopg.connect(self._dsn, autocommit=True, connect_timeout=_CONNECT_TIMEOUT) as conn:
            conn.execute("SET default_transaction_read_only = on")
            conn.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT_MS}ms'")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(_PROJECT_SQL)
                for r in cur.fetchall():
                    projects[int(r["id"])] = r["name"]
                cur.execute(_ISSUE_TYPE_SQL)
                for r in cur.fetchall():
                    pid = int(r["project_id"])
                    defects.setdefault(pid, {})[r["locator"]] = {
                        "locator": r["locator"],
                        "name": r["issue_name"],
                        "short_name": r["abbreviation"],
                        "color": r["hex_color"],
                    }
        self._projects = projects
        self._defects = defects
        self._reachable = True
        self._error = None

    def _ensure_fresh(self) -> None:
        if not self._dsn:
            return
        now = time.monotonic()
        if self._loaded_at and (now - self._loaded_at) < self._ttl:
            return
        with self._lock:
            now = time.monotonic()
            if self._loaded_at and (now - self._loaded_at) < self._ttl:
                return
            try:
                self._load()
            except Exception as exc:  # unreachable / auth / schema — degrade gracefully
                self._reachable = False
                self._error = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
                # Keep any previously cached maps so a transient blip still resolves.
            finally:
                self._loaded_at = time.monotonic()

    # -- lookups ---------------------------------------------------------- #
    def status(self) -> RPStatus:
        self._ensure_fresh()
        if not self._dsn:
            return RPStatus(False, False, "RP names: not configured")
        if self._reachable:
            return RPStatus(True, True, "RP names: live")
        detail = f" ({self._error})" if self._error else ""
        return RPStatus(True, False, f"RP names: unavailable{detail}")

    def project_name(self, project_id: int) -> str | None:
        self._ensure_fresh()
        return self._projects.get(int(project_id))

    def defects(self, project_id: int) -> dict[str, dict[str, Any]]:
        self._ensure_fresh()
        return self._defects.get(int(project_id), {})

    # -- RP UI deep links ------------------------------------------------- #
    def launch_link(self, project_id: int, launch_id: Any) -> str | None:
        """Host-relative RP UI link to a launch, or None when unresolvable.

        The inspector is served on the same host as the RP UI, so a root-relative
        ``/ui/#...`` link opens the launch without any host/config knowledge.
        Returns None (never a guessed link) when RP is not configured/reachable
        or the project name is unknown — the frontend then shows plain text.
        """
        if not self._dsn or launch_id is None:
            return None
        name = self.project_name(project_id)
        if not name:
            return None
        return f"/ui/#{name}/launches/all/{launch_id}"

    def _fetch_item_meta(self, item_ids: list[int]) -> None:
        """Fetch launch_id + path for uncached item ids from RP. Raises on error."""
        missing = [i for i in item_ids if i not in self._item_meta]
        if not missing:
            return
        with psycopg.connect(self._dsn, autocommit=True, connect_timeout=_CONNECT_TIMEOUT) as conn:
            conn.execute("SET default_transaction_read_only = on")
            conn.execute(f"SET statement_timeout = '{_STATEMENT_TIMEOUT_MS}ms'")
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute(_ITEM_PATH_SQL, (missing,))
                for r in cur.fetchall():
                    iid = int(r["item_id"])
                    segs = [s for s in (r["path"] or "").split(".") if s]
                    # RP's ltree path ends with the item's own id; guard anyway so
                    # the deep link always terminates at the item being linked.
                    if not segs or segs[-1] != str(iid):
                        segs.append(str(iid))
                    self._item_meta[iid] = (r["launch_id"], segs)

    def item_links(self, project_id: int, item_ids: Any) -> dict[int, str]:
        """Map item_id -> RP UI deep link for the given ids (batched, cached).

        Builds ``/ui/#<project>/launches/all/<launchId>/<path…>/log`` per item.
        Missing ids (unknown to RP) and every failure path degrade to an omitted
        entry, so the frontend falls back to plain-text ids — never a dead link.
        """
        if not self._dsn:
            return {}
        name = self.project_name(project_id)  # triggers _ensure_fresh
        if not name:
            return {}
        ids: list[int] = []
        seen: set[int] = set()
        for i in item_ids or ():
            if i is None:
                continue
            try:
                iv = int(i)
            except (TypeError, ValueError):
                continue
            if iv not in seen:
                seen.add(iv)
                ids.append(iv)
        if not ids:
            return {}
        try:
            with self._lock:
                self._fetch_item_meta(ids)
        except Exception:  # unreachable / timeout — honest fallback, no links
            return {}
        out: dict[int, str] = {}
        for iv in ids:
            meta = self._item_meta.get(iv)
            if meta is None:
                continue
            launch_id, segs = meta
            out[iv] = f"/ui/#{name}/launches/all/{launch_id}/{'/'.join(segs)}/log"
        return out

    def block(self, project_id: int) -> dict[str, Any]:
        """The per-project ``rp`` block attached to every label-bearing payload.

        Contains everything the frontend needs to render real names + colors and
        an honest status note, with the raw locators kept as secondary data.
        """
        status = self.status()
        return {
            "project_id": project_id,
            "project_name": self.project_name(project_id),
            "status": status.as_dict(),
            "defects": self.defects(project_id),
        }
