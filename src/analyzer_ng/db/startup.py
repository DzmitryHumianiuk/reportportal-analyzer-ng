"""Database bootstrap (spec 02 §1).

Runs in the container entrypoint before AMQP consumers start:

1. Connect to the target database; on ``3D000`` (does not exist) and
   ``ANALYZER_PG_CREATE_DB=true`` create it via the maintenance DB.
2. Fail fast on operator errors — PostgreSQL < 16, pgvector absent or < 0.8 —
   with an actionable message and exit code 3 (no retry loop; a crash-loop with
   a clear message is the correct docker-compose behavior).
3. ``CREATE SCHEMA IF NOT EXISTS analyzer`` + the extension block (idempotent).
4. Hand the open connection to the migration runner (spec 02 §3).

Transient connection failures DO retry (30 attempts, 2 s apart, then exit 4).
"""

from __future__ import annotations

import logging
import time

import psycopg
from psycopg import conninfo, sql

from analyzer_ng.db.migrate import MigrationError, apply_migrations

logger = logging.getLogger(__name__)

# Exit codes (spec 02 §1).
EXIT_FATAL = 3  # operator error: bad server/extension/privilege
EXIT_CONNECT = 4  # transient connection failures exhausted

# sqlstate codes.
_INVALID_CATALOG_NAME = "3D000"  # database does not exist
_INSUFFICIENT_PRIVILEGE = "42501"
_DUPLICATE_DATABASE = "42P04"

# Minimum supported versions (spec 02 §1.1).
MIN_SERVER_VERSION_NUM = 160000
MIN_PGVECTOR_VERSION = (0, 8, 0)

# Exact fail-fast messages (spec 02 §1.1) — asserted by the acceptance tests.
FATAL_PGVECTOR_MISSING = (
    "FATAL: pgvector is not installed on this PostgreSQL server. analyzer-ng requires "
    "the 'vector' extension (>= 0.8). Use the pgvector/pgvector:pg16 image or install "
    "the postgresql-16-pgvector package, then restart."
)


class BootstrapError(RuntimeError):
    """A fatal bootstrap condition carrying the process exit code."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class _DatabaseDoesNotExist(RuntimeError):
    """Internal sentinel: the target database is absent (not a transient fault)."""


def _is_missing_database(exc: psycopg.OperationalError, dbname: str | None) -> bool:
    """True if ``exc`` means "database does not exist" (spec 02 §1.2, ``3D000``).

    psycopg3 does not populate ``sqlstate`` for connection-time failures, so we
    fall back to the libpq FATAL text (which names the missing database). The
    ``sqlstate`` check keeps working if a future psycopg starts setting it.
    """
    if dbname is None:
        return False
    if getattr(exc, "sqlstate", None) == _INVALID_CATALOG_NAME:
        return True
    return f'database "{dbname}" does not exist' in str(exc)


def _maintenance_dsn(dsn: str, maintenance_db: str = "postgres") -> tuple[str, str]:
    """Return (maintenance_dsn, target_dbname) derived from ``dsn``.

    The maintenance DSN reuses the same credentials/host but targets the
    ``postgres`` database so ``CREATE DATABASE`` can run there.
    """
    params = conninfo.conninfo_to_dict(dsn)
    target_db = params.get("dbname")
    if not target_db:
        raise BootstrapError(f"cannot determine target database name from DSN {dsn!r}", EXIT_FATAL)
    params["dbname"] = maintenance_db
    coerced = {key: str(value) for key, value in params.items() if value is not None}
    return conninfo.make_conninfo(**coerced), str(target_db)


def connect_with_retries(
    dsn: str,
    *,
    autocommit: bool = False,
    attempts: int = 30,
    delay: float = 2.0,
    missing_db: str | None = None,
) -> psycopg.Connection:
    """Connect, retrying transient failures (spec 02 §1.1: 30x2s, then exit 4).

    A "database does not exist" failure is not transient: it raises
    ``_DatabaseDoesNotExist`` immediately (never retried) so the caller can
    create the database. ``missing_db`` is the target name used to recognize it.
    """
    last_error: psycopg.OperationalError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(dsn, autocommit=autocommit)
        except psycopg.OperationalError as exc:
            if _is_missing_database(exc, missing_db):
                raise _DatabaseDoesNotExist(str(exc)) from exc
            last_error = exc
            if attempt < attempts:
                logger.warning(
                    "PostgreSQL not reachable (attempt %d/%d): %s", attempt, attempts, exc
                )
                time.sleep(delay)
    raise BootstrapError(
        f"could not connect to PostgreSQL after {attempts} attempts: {last_error}",
        EXIT_CONNECT,
    )


def ensure_database(
    dsn: str,
    *,
    create_db: bool = True,
    attempts: int = 30,
    delay: float = 2.0,
) -> psycopg.Connection:
    """Connect to the target DB, creating it if missing (spec 02 §1.2 step 1)."""
    maintenance_dsn, target_db = _maintenance_dsn(dsn)
    try:
        return connect_with_retries(dsn, attempts=attempts, delay=delay, missing_db=target_db)
    except _DatabaseDoesNotExist as exc:
        if not create_db:
            raise BootstrapError(
                f"target database {target_db!r} does not exist and "
                "ANALYZER_PG_CREATE_DB=false; create it out of band "
                "(see db/bootstrap/create_role_and_db.sql)",
                EXIT_FATAL,
            ) from exc

    logger.info("Target database %r missing; creating it", target_db)
    admin = connect_with_retries(maintenance_dsn, autocommit=True, attempts=attempts, delay=delay)
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
    except psycopg.errors.DuplicateDatabase:
        # A concurrent starter won the race; that is fine.
        logger.info("Database %r already created concurrently", target_db)
    except psycopg.errors.InsufficientPrivilege as exc:
        raise BootstrapError(
            f"role lacks CREATEDB to create database {target_db!r}. Grant CREATEDB, or "
            "pre-create the database (db/bootstrap/create_role_and_db.sql) and start with "
            "ANALYZER_PG_CREATE_DB=false",
            EXIT_FATAL,
        ) from exc
    finally:
        admin.close()

    return connect_with_retries(dsn, attempts=attempts, delay=delay)


def _parse_version(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for token in version.split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def check_server_and_extensions(conn: psycopg.Connection) -> None:
    """Fail fast on unsupported server or missing/old pgvector (spec 02 §1.1)."""
    version_row = conn.execute("SHOW server_version_num").fetchone()
    assert version_row is not None  # SHOW always returns a row
    server_version_num = int(version_row[0])
    if server_version_num < MIN_SERVER_VERSION_NUM:
        raise BootstrapError(
            f"FATAL: PostgreSQL 16+ required (found server_version_num={server_version_num})",
            EXIT_FATAL,
        )

    row = conn.execute(
        "SELECT default_version, installed_version "
        "FROM pg_available_extensions WHERE name = 'vector'"
    ).fetchone()
    if row is None:
        raise BootstrapError(FATAL_PGVECTOR_MISSING, EXIT_FATAL)

    default_version, installed_version = row
    version_str = installed_version or default_version
    if _parse_version(version_str) < MIN_PGVECTOR_VERSION:
        minimum = ".".join(str(p) for p in MIN_PGVECTOR_VERSION)
        raise BootstrapError(
            f"FATAL: pgvector {version_str} is too old; analyzer-ng requires the 'vector' "
            f"extension (>= {minimum}). Upgrade pgvector (pgvector/pgvector:pg16), then restart.",
            EXIT_FATAL,
        )


def ensure_schema_and_extensions(conn: psycopg.Connection, schema: str = "analyzer") -> None:
    """Create the schema and required extensions, idempotently (spec 02 §1.2)."""
    with conn.transaction():
        conn.execute(
            sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION CURRENT_USER").format(
                sql.Identifier(schema)
            )
        )
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")


def bootstrap(
    dsn: str,
    *,
    schema: str = "analyzer",
    create_db: bool = True,
    attempts: int = 30,
    delay: float = 2.0,
) -> psycopg.Connection:
    """Run the full bootstrap and return an open connection (spec 02 §1.2).

    The connection is left in autocommit with the schema and extensions ready;
    the caller passes it to :func:`analyzer_ng.db.migrate.apply_migrations` and
    owns closing it.
    """
    conn = ensure_database(dsn, create_db=create_db, attempts=attempts, delay=delay)
    conn.autocommit = True
    check_server_and_extensions(conn)
    ensure_schema_and_extensions(conn, schema)
    return conn


def bootstrap_and_migrate(
    dsn: str,
    *,
    schema: str = "analyzer",
    create_db: bool = True,
    attempts: int = 30,
    delay: float = 2.0,
) -> list[int]:
    """Bootstrap then apply migrations; return versions applied this run."""
    conn = bootstrap(dsn, schema=schema, create_db=create_db, attempts=attempts, delay=delay)
    with conn:
        return apply_migrations(conn, schema=schema)


def bootstrap_and_migrate_or_exit(
    dsn: str,
    *,
    schema: str = "analyzer",
    create_db: bool = True,
    attempts: int = 30,
    delay: float = 2.0,
) -> list[int]:
    """Entry point for the service: bootstrap + migrate, mapping errors to exits.

    Logs the fatal message (no traceback as the last line) and raises
    ``SystemExit`` with the spec's exit code (3 fatal, 4 connect, 5 migration).
    """
    try:
        return bootstrap_and_migrate(
            dsn, schema=schema, create_db=create_db, attempts=attempts, delay=delay
        )
    except BootstrapError as exc:
        logger.error("%s", exc)
        raise SystemExit(exc.exit_code) from None
    except MigrationError as exc:
        logger.error("%s", exc)
        raise SystemExit(exc.exit_code) from None
