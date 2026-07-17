"""PostgreSQL connection pool factory + health probe (spec 01 §1.2, §9.2).

A thin wrapper over ``psycopg_pool.ConnectionPool``. The service opens one pool
at startup; the health endpoint uses :func:`check_pg` to report the ``pg`` status
and ``analyzer_pg_pool_in_use`` reads pool stats.
"""

from __future__ import annotations

import logging

from psycopg_pool import ConnectionPool

from analyzer_ng.config import AppConfig

logger = logging.getLogger(__name__)


def open_pool(config: AppConfig, *, open_now: bool = True) -> ConnectionPool:
    """Create (and optionally open) the connection pool with configured bounds."""
    pool = ConnectionPool(
        conninfo=config.pg_dsn_effective,
        min_size=config.analyzer_pg_pool_min,
        max_size=config.analyzer_pg_pool_max,
        open=False,
        name="analyzer-ng",
    )
    if open_now:
        pool.open()
    return pool


def check_pg(pool: ConnectionPool | None) -> bool:
    """Return ``True`` iff a ``SELECT 1`` succeeds on a pooled connection."""
    if pool is None:
        return False
    try:
        with pool.connection(timeout=5.0) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception as exc:  # noqa: BLE001 — health probe must never raise
        logger.warning("PostgreSQL health check failed: %s", exc)
        return False


def pool_in_use(pool: ConnectionPool | None) -> int:
    """Number of connections currently checked out of the pool (for /metrics)."""
    if pool is None:
        return 0
    try:
        stats = pool.get_stats()
        return int(stats.get("pool_size", 0)) - int(stats.get("pool_available", 0))
    except Exception:  # noqa: BLE001
        return 0
