"""Database bootstrap (spec 02 §1).

Runs in the container entrypoint before AMQP consumers start:

1. Connect to the target database; if it does not exist and
   ``ANALYZER_PG_CREATE_DB=true`` create it via the maintenance DB. Whether the
   database is missing is decided authoritatively by probing ``pg_database`` on
   the ``postgres`` maintenance DB (not by parsing localized libpq text).
2. Fail fast on operator errors — PostgreSQL < 16, pgvector absent or < 0.8 —
   with an actionable message and exit code 3 (no retry loop; a crash-loop with
   a clear message is the correct docker-compose behavior).
3. ``CREATE SCHEMA IF NOT EXISTS analyzer`` + the extension block (idempotent),
   run under the migration advisory lock so simultaneous cold starts do not race
   PostgreSQL's ``IF NOT EXISTS`` forms.
4. Hand the open connection to the migration runner (spec 02 §3).

Transient connection failures DO retry (30 attempts, 2 s apart, then exit 4).
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import urlparse

import psycopg
from psycopg import conninfo, sql

from analyzer_ng.db.migrate import MigrationError, apply_migrations, migration_lock

logger = logging.getLogger(__name__)

# Exit codes (spec 02 §1).
EXIT_FATAL = 3  # operator error: bad server/extension/privilege
EXIT_CONNECT = 4  # transient connection failures exhausted

# sqlstate codes.
_INVALID_CATALOG_NAME = "3D000"  # database does not exist

# ascii 'anzcrdb0' as a signed int64: a distinct advisory key that serializes
# concurrent CREATE DATABASE without coupling to the migration lock.
CREATE_DB_ADVISORY_KEY = 0x616E7A6372646230

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


_PW_KV_RE = re.compile(r"(?i)(password\s*=\s*)('[^']*'|\"[^\"]*\"|\S+)")


def redact_dsn(dsn: str) -> str:
    """Strip credentials from a DSN before it reaches a log/exception message.

    A DSN routinely carries a password — either as URL userinfo
    (``postgresql://user:pass@host/db``, redacted like
    :func:`analyzer_ng.amqp.client.remove_credentials_from_url`) or as a
    ``password=`` keyword. Both are removed so a bootstrap error can never leak the
    secret through the log sink (spec §9.1).
    """
    parsed = urlparse(dsn)
    if parsed.netloc:
        new_netloc = re.sub("^[^:]+:[^@]*@", "", parsed.netloc)
        if new_netloc != parsed.netloc:
            dsn = dsn.replace(parsed.netloc, new_netloc)
    return _PW_KV_RE.sub(r"\1***", dsn)


def _is_missing_database(exc: psycopg.OperationalError, dbname: str | None) -> bool:
    """Best-effort libpq-text fallback used only when the maintenance DB is
    unreachable (so we cannot authoritatively probe ``pg_database``).

    psycopg3 leaves ``sqlstate`` unset on connection-time failures; the
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
        raise BootstrapError(
            f"cannot determine target database name from DSN {redact_dsn(dsn)!r}", EXIT_FATAL
        )
    params["dbname"] = maintenance_db
    coerced = {key: str(value) for key, value in params.items() if value is not None}
    return conninfo.make_conninfo(**coerced), str(target_db)


def connect_with_retries(
    dsn: str,
    *,
    autocommit: bool = False,
    attempts: int = 30,
    delay: float = 2.0,
) -> psycopg.Connection:
    """Connect, retrying transient failures (spec 02 §1.1: 30x2s, then exit 4)."""
    last_error: psycopg.OperationalError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(dsn, autocommit=autocommit)
        except psycopg.OperationalError as exc:
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


def _target_database_absent(
    connect_error: psycopg.OperationalError, maintenance_dsn: str, target_db: str
) -> bool | None:
    """Decide whether ``target_db`` is absent by probing ``pg_database``.

    Returns True (absent), False (present), or None (undecidable — the
    maintenance DB is unreachable, e.g. server down or the role lacks CONNECT on
    ``postgres``; the caller falls back to the libpq-text heuristic). This is the
    locale-proof primary detection the review asked for.
    """
    try:
        admin = psycopg.connect(maintenance_dsn, autocommit=True)
    except psycopg.OperationalError:
        return True if _is_missing_database(connect_error, target_db) else None
    try:
        present = admin.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (target_db,)
        ).fetchone()
        return present is None
    finally:
        admin.close()


def _create_database(maintenance_dsn: str, target_db: str, *, attempts: int, delay: float) -> None:
    """Create ``target_db`` under an advisory lock (concurrent-start safe)."""
    admin = connect_with_retries(maintenance_dsn, autocommit=True, attempts=attempts, delay=delay)
    try:
        admin.execute("SELECT pg_advisory_lock(%s)", (CREATE_DB_ADVISORY_KEY,))
        try:
            if admin.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (target_db,)
            ).fetchone():
                return  # a concurrent starter created it while we waited for the lock
            logger.info("Target database %r missing; creating it", target_db)
            try:
                admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(target_db)))
            except psycopg.errors.DuplicateDatabase:
                logger.info("Database %r already created concurrently", target_db)
            except psycopg.errors.InsufficientPrivilege as exc:
                raise BootstrapError(
                    f"role lacks CREATEDB to create database {target_db!r}. Grant CREATEDB, or "
                    "pre-create the database (db/bootstrap/create_role_and_db.sql) and start with "
                    "ANALYZER_PG_CREATE_DB=false",
                    EXIT_FATAL,
                ) from exc
        finally:
            admin.execute("SELECT pg_advisory_unlock(%s)", (CREATE_DB_ADVISORY_KEY,))
    finally:
        admin.close()


def ensure_database(
    dsn: str,
    *,
    create_db: bool = True,
    attempts: int = 30,
    delay: float = 2.0,
) -> psycopg.Connection:
    """Connect to the target DB, creating it if missing (spec 02 §1.2 step 1).

    A failed target connection is disambiguated by probing ``pg_database`` on the
    maintenance DB, so a missing database is recognized on the first failure
    (never after burning the retry budget) and independently of server locale.
    """
    maintenance_dsn, target_db = _maintenance_dsn(dsn)
    last_error: psycopg.OperationalError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return psycopg.connect(dsn)
        except psycopg.OperationalError as exc:
            last_error = exc
            absent = _target_database_absent(exc, maintenance_dsn, target_db)
            if absent is True:
                if not create_db:
                    raise BootstrapError(
                        f"target database {target_db!r} does not exist and "
                        "ANALYZER_PG_CREATE_DB=false; create it out of band "
                        "(see db/bootstrap/create_role_and_db.sql)",
                        EXIT_FATAL,
                    ) from exc
                _create_database(maintenance_dsn, target_db, attempts=attempts, delay=delay)
                continue  # reconnect to the freshly-created database
            # DB exists (transient target failure) or undecidable: retry.
            if attempt < attempts:
                logger.warning(
                    "PostgreSQL not reachable (attempt %d/%d): %s", attempt, attempts, exc
                )
                time.sleep(delay)
    raise BootstrapError(
        f"could not connect to PostgreSQL after {attempts} attempts: {last_error}",
        EXIT_CONNECT,
    )


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


def _connect_and_check(
    dsn: str, *, create_db: bool, attempts: int, delay: float
) -> psycopg.Connection:
    """Connect (creating the DB if needed) and run the read-only fail-fast checks."""
    conn = ensure_database(dsn, create_db=create_db, attempts=attempts, delay=delay)
    conn.autocommit = True
    check_server_and_extensions(conn)  # read-only; safe under concurrency, no lock needed
    return conn


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
    owns closing it. Schema/extension creation runs under the migration advisory
    lock so simultaneous cold starts do not race the ``IF NOT EXISTS`` forms.
    """
    conn = _connect_and_check(dsn, create_db=create_db, attempts=attempts, delay=delay)
    with migration_lock(conn):
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
    """Bootstrap then apply migrations; return versions applied this run.

    Schema/extension creation and migration share one advisory-lock critical
    section, so two simultaneous cold starts against an empty database converge
    without racing ``CREATE SCHEMA/EXTENSION IF NOT EXISTS`` (spec acceptance #4).
    """
    conn = _connect_and_check(dsn, create_db=create_db, attempts=attempts, delay=delay)
    with conn, migration_lock(conn):
        ensure_schema_and_extensions(conn, schema)
        return apply_migrations(conn, schema=schema, lock=False)


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
