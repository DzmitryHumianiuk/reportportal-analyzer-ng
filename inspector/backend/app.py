"""FastAPI application: read-only API + static SPA for the analyzer-ng inspector.

The SPA is served from ``inspector/static``; the API lives under ``/api``. When
deployed behind the ingress at ``/inspector`` (which does **not** strip the
prefix), the whole app is *mounted* under that prefix so requests the pod
receives as ``/inspector/...`` match — no path rewriting at the proxy. The
injected ``<base href>`` makes every relative SPA URL resolve under the prefix.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.applications import Starlette
from starlette.responses import JSONResponse as StarletteJSON
from starlette.routing import Mount, Route

from . import payloads
from .config import Config
from .db import Database

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(config: Config | None = None) -> Starlette | FastAPI:
    """Return the ASGI app. If a root_path is configured, mount the inner app
    under it and expose ``/healthz`` at the top level for container probes."""
    cfg = config or Config.from_env()
    inner = _build_inner(cfg)
    if not cfg.root_path:
        return inner

    async def _probe(_request):  # noqa: ANN001, ANN202 - starlette handler signature
        return StarletteJSON(
            {"status": "ok", "service": "analyzer-ng-inspector", "mounted_at": cfg.root_path}
        )

    # Top-level /healthz for container/k8s probes (hit directly, not via ingress),
    # then the whole inner app mounted under the ingress prefix.
    return Starlette(routes=[Route("/healthz", _probe), Mount(cfg.root_path, app=inner)])


def _build_inner(cfg: Config) -> FastAPI:
    db = Database(cfg.pg_dsn)
    limit = cfg.query_limit

    app = FastAPI(
        title="analyzer-ng inspector",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    app.state.config = cfg
    app.state.db = db

    # ---- Health ----
    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": "analyzer-ng-inspector", "version": "0.1.0"}

    @app.get("/api/health")
    def api_health() -> dict[str, Any]:
        ok = db.ping()
        return {"db_reachable": ok, "hybrid_note": "read-only companion"}

    # ---- Pickers ----
    @app.get("/api/projects")
    def api_projects() -> dict[str, Any]:
        return {"projects": payloads.list_projects(db, limit)}

    @app.get("/api/launches")
    def api_launches(project: int = Query(...)) -> dict[str, Any]:
        return {"launches": payloads.list_launches(db, project, limit)}

    @app.get("/api/items")
    def api_items(project: int = Query(...), launch: int = Query(...)) -> dict[str, Any]:
        return {"items": payloads.list_items(db, project, launch, limit)}

    # ---- Item Journey ----
    @app.get("/api/item/{project}/{item_id}/journey")
    def api_journey(project: int, item_id: int) -> Any:
        data = payloads.item_journey(db, project, item_id)
        if data is None:
            raise HTTPException(status_code=404, detail="item not found")
        return data

    # ---- Explorers ----
    @app.get("/api/templates")
    def api_templates(
        project: int = Query(...), q: str | None = Query(default=None)
    ) -> dict[str, Any]:
        return payloads.templates(db, project, q, limit)

    @app.get("/api/modes3d")
    def api_modes3d(project: int = Query(...)) -> dict[str, Any]:
        return payloads.modes3d(db, project, limit)

    @app.get("/api/groups")
    def api_groups(
        project: int = Query(...), launch: int | None = Query(default=None)
    ) -> dict[str, Any]:
        return payloads.groups(db, project, launch, limit)

    @app.get("/api/timeline")
    def api_timeline(project: int = Query(...)) -> dict[str, Any]:
        return payloads.timeline(db, project, limit)

    @app.get("/api/summary")
    def api_summary(project: int = Query(...)) -> dict[str, Any]:
        return payloads.summary(db, project)

    # ---- Optional live analyzer health proxy ----
    @app.get("/api/analyzer-health")
    def api_analyzer_health() -> Any:
        if not cfg.analyzer_health_url:
            return JSONResponse({"configured": False}, status_code=200)
        try:
            import urllib.request

            with urllib.request.urlopen(cfg.analyzer_health_url, timeout=3) as resp:  # noqa: S310
                import json

                body = json.loads(resp.read().decode("utf-8"))
            return {"configured": True, "reachable": True, "health": body}
        except Exception as exc:  # pragma: no cover - network dependent
            return {"configured": True, "reachable": False, "error": str(exc)}

    # ---- Static SPA (mounted last so /api wins) ----
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        # Inject <base> so every relative URL (script src, fetch) resolves under
        # the ingress mount point without any path rewriting at the proxy.
        base = (cfg.root_path + "/") if cfg.root_path else "/"
        index_rendered = index_html.replace("<!--BASE-->", f'<base href="{base}">')

        from fastapi.responses import HTMLResponse

        @app.get("/", response_class=HTMLResponse)
        def index() -> HTMLResponse:
            return HTMLResponse(index_rendered)

    return app


app = create_app()
