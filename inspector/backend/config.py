"""Environment-driven configuration for the inspector service.

Everything is read from the process environment so the same image runs unchanged
in local dev, docker-compose and minikube. No secrets are baked in.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration."""

    pg_dsn: str
    http_port: int
    root_path: str
    analyzer_health_url: str | None
    queries_path: str | None
    query_limit: int

    @staticmethod
    def from_env() -> Config:
        dsn = os.environ.get(
            "INSPECTOR_PG_DSN",
            # Local-dev default: matches the port-forward used during development.
            "postgresql://analyzer:analyzer@127.0.0.1:5432/analyzer",
        )
        port = int(os.environ.get("INSPECTOR_HTTP_PORT", "5005"))
        # root_path lets uvicorn/FastAPI serve correctly behind `/inspector` ingress.
        root_path = os.environ.get("INSPECTOR_ROOT_PATH", "").rstrip("/")
        health = os.environ.get("ANALYZER_HEALTH_URL") or None
        queries_path = os.environ.get("INSPECTOR_QUERIES_PATH") or None
        limit = int(os.environ.get("INSPECTOR_QUERY_LIMIT", "500"))
        return Config(
            pg_dsn=dsn,
            http_port=port,
            root_path=root_path,
            analyzer_health_url=health,
            queries_path=queries_path,
            query_limit=limit,
        )
