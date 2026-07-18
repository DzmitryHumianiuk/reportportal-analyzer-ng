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
        with psycopg.connect(
            self._dsn, autocommit=True, connect_timeout=_CONNECT_TIMEOUT
        ) as conn:
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
