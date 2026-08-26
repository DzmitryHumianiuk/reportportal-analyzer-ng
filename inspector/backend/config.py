"""Environment-driven configuration for the inspector service.

Everything is read from the process environment so the same image runs unchanged
in local dev, docker-compose and minikube. No secrets are baked in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# The built frontend (Vite output). Gitignored and absent on a fresh clone, so
# every reader of this path guards with ``exists()``.
DEFAULT_STATIC_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration."""

    pg_dsn: str
    http_port: int
    root_path: str
    analyzer_health_url: str | None
    queries_path: str | None
    query_limit: int
    rp_pg_dsn: str | None
    # Directory holding the built SPA (index.html + assets/). A field rather
    # than a module constant so tests can point the app at a temporary build.
    static_dist: Path = field(default=DEFAULT_STATIC_DIST)

    @staticmethod
    def from_env() -> Config:
        dsn = os.environ.get(
            "INSPECTOR_PG_DSN",
            # Local-dev default: matches the port-forward used during development.
            "postgresql://analyzer:analyzer@127.0.0.1:5432/analyzer",
        )
        # OPTIONAL: ReportPortal's own PostgreSQL. When set, the inspector resolves
        # real project names and defect-type long names/colors from RP's schema.
        # Unset/unreachable → the UI falls back to raw locators (see rp_names).
        rp_dsn = os.environ.get("INSPECTOR_RP_PG_DSN") or None
        port = int(os.environ.get("INSPECTOR_HTTP_PORT", "5005"))
        # root_path lets uvicorn/FastAPI serve correctly behind `/inspector` ingress.
        root_path = os.environ.get("INSPECTOR_ROOT_PATH", "").rstrip("/")
        health = os.environ.get("ANALYZER_HEALTH_URL") or None
        queries_path = os.environ.get("INSPECTOR_QUERIES_PATH") or None
        limit = int(os.environ.get("INSPECTOR_QUERY_LIMIT", "500"))
        dist_env = os.environ.get("INSPECTOR_STATIC_DIST") or None
        dist = Path(dist_env) if dist_env else DEFAULT_STATIC_DIST
        return Config(
            pg_dsn=dsn,
            http_port=port,
            root_path=root_path,
            analyzer_health_url=health,
            queries_path=queries_path,
            query_limit=limit,
            rp_pg_dsn=rp_dsn,
            static_dist=dist,
        )
