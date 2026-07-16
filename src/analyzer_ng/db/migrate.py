"""Migration runner (spec 02 §3).

Applies the ordered SQL files under ``db/migrations/`` exactly once, under a
project-wide advisory lock so concurrent container starts converge to a single
application. Each applied file is recorded in ``analyzer.schema_migrations``
with a sha256 checksum of its (LF-normalized) bytes; on every start the recorded
checksums are re-verified so a released migration file can never be silently
edited (exit 5).

Files whose header contains ``-- analyzer:no-transaction`` run statement-by-
statement in autocommit (for ``CREATE INDEX CONCURRENTLY`` and friends); all
others run inside a single transaction that also inserts the ledger row.

This runner is synchronous psycopg3: it runs once at container start, before the
async AMQP consumers and the connection pool come up (spec 01 §6).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg
from psycopg import sql

logger = logging.getLogger(__name__)

# ascii 'anzmigr0' as a signed int64 — the project-wide migration advisory lock
# key (spec 02 §3.2). Serializes concurrent container starts.
ADVISORY_LOCK_KEY = 0x616E7A6D69677230

# Header marker for files that must run outside a transaction (spec 02 §3.2).
NO_TRANSACTION_MARKER = "-- analyzer:no-transaction"

DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

_FILENAME_RE = re.compile(r"^(\d+)_.+\.sql$")
_DOLLAR_TAG_RE = re.compile(r"\$[A-Za-z_0-9]*\$")


class MigrationError(RuntimeError):
    """A migration could not be applied or a released file changed (exit 5)."""

    exit_code = 5


@dataclass(frozen=True)
class Migration:
    """One discovered migration file."""

    version: int
    filename: str
    path: Path
    checksum: str
    sql: str
    no_transaction: bool


def normalize_bytes(data: bytes) -> bytes:
    """CRLF->LF normalize so checksums are stable across git autocrlf checkouts."""
    return data.replace(b"\r\n", b"\n")


def compute_checksum(data: bytes) -> str:
    """sha256 hex of the LF-normalized file bytes (spec 02 §3.1)."""
    return hashlib.sha256(normalize_bytes(data)).hexdigest()


def discover_migrations(migrations_dir: Path = DEFAULT_MIGRATIONS_DIR) -> list[Migration]:
    """Return migrations sorted by version, erroring on gaps or duplicates."""
    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = _FILENAME_RE.match(path.name)
        if match is None:
            raise MigrationError(
                f"migration file {path.name!r} does not match NNNN_name.sql; "
                "rename it (version = leading zero-padded integer)"
            )
        raw = path.read_bytes()
        text = normalize_bytes(raw).decode("utf-8")
        migrations.append(
            Migration(
                version=int(match.group(1)),
                filename=path.name,
                path=path,
                checksum=compute_checksum(raw),
                sql=text,
                no_transaction=_has_no_transaction_marker(text),
            )
        )

    migrations.sort(key=lambda m: m.version)
    for index, migration in enumerate(migrations):
        if index > 0:
            previous = migrations[index - 1].version
            if migration.version == previous:
                raise MigrationError(
                    f"duplicate migration version {migration.version} "
                    f"({migrations[index - 1].filename} and {migration.filename})"
                )
            if migration.version != previous + 1:
                raise MigrationError(
                    f"gap in migration versions: {previous} -> {migration.version} "
                    f"({migration.filename}); versions must be contiguous"
                )
    return migrations


def _has_no_transaction_marker(text: str) -> bool:
    """True if any comment line in the (short) header carries the marker."""
    for line in text.splitlines():
        if line.strip() == NO_TRANSACTION_MARKER:
            return True
        # Stop scanning at the first non-comment, non-blank line: the marker is
        # a header directive, not something buried in the body.
        stripped = line.strip()
        if stripped and not stripped.startswith("--"):
            break
    return False


def split_statements(sql_text: str) -> list[str]:
    """Split a SQL script into statements on top-level semicolons.

    Dollar-quoted bodies (``$$ ... $$``, ``$tag$ ... $tag$``) and single-quoted
    string literals are treated as opaque, so semicolons inside a function body
    or a literal do not split the statement. Line comments (``--``) are dropped.
    This avoids relying on the driver's multi-statement handling and keeps the
    no-transaction path (one execute per statement) correct.
    """
    statements: list[str] = []
    buffer: list[str] = []
    i = 0
    n = len(sql_text)
    in_single_quote = False
    dollar_tag: str | None = None

    while i < n:
        ch = sql_text[i]

        if dollar_tag is not None:
            if sql_text.startswith(dollar_tag, i):
                buffer.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                buffer.append(ch)
                i += 1
            continue

        if in_single_quote:
            buffer.append(ch)
            if ch == "'":
                if i + 1 < n and sql_text[i + 1] == "'":  # '' escape
                    buffer.append("'")
                    i += 2
                    continue
                in_single_quote = False
            i += 1
            continue

        if ch == "-" and i + 1 < n and sql_text[i + 1] == "-":
            newline = sql_text.find("\n", i)
            i = n if newline == -1 else newline
            continue

        if ch == "'":
            in_single_quote = True
            buffer.append(ch)
            i += 1
            continue

        if ch == "$":
            tag_match = _DOLLAR_TAG_RE.match(sql_text, i)
            if tag_match is not None:
                dollar_tag = tag_match.group(0)
                buffer.append(dollar_tag)
                i += len(dollar_tag)
                continue

        if ch == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            i += 1
            continue

        buffer.append(ch)
        i += 1

    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def _ensure_ledger(conn: psycopg.Connection, schema: str) -> None:
    """Create analyzer.schema_migrations if absent (spec 02 §2.1 / §3.2 step 3)."""
    conn.execute(
        sql.SQL(
            """
            CREATE TABLE IF NOT EXISTS {}.schema_migrations (
                version     integer      PRIMARY KEY,
                filename    text         NOT NULL,
                checksum    char(64)     NOT NULL,
                applied_at  timestamptz  NOT NULL DEFAULT now(),
                duration_ms integer      NOT NULL DEFAULT 0
            )
            """
        ).format(sql.Identifier(schema))
    )


def _load_applied(conn: psycopg.Connection, schema: str) -> dict[int, tuple[str, str]]:
    """Return {version: (filename, checksum)} recorded in the ledger."""
    cur = conn.execute(
        sql.SQL(
            "SELECT version, filename, checksum FROM {}.schema_migrations ORDER BY version"
        ).format(sql.Identifier(schema))
    )
    return {row[0]: (row[1], row[2]) for row in cur.fetchall()}


def _verify_applied(applied: dict[int, tuple[str, str]], by_version: dict[int, Migration]) -> None:
    """Every applied version must still have a file with the same checksum."""
    for version, (filename, checksum) in applied.items():
        migration = by_version.get(version)
        if migration is None:
            raise MigrationError(
                f"applied migration {version:04d} ({filename}) has no file on disk — "
                "restore it or the schema history is inconsistent"
            )
        if migration.checksum != checksum:
            raise MigrationError(
                f"migration file changed after being applied — restore it or write a new "
                f"migration (file {migration.filename}: recorded {checksum}, "
                f"found {migration.checksum})"
            )


def _apply_one(conn: psycopg.Connection, migration: Migration, schema: str) -> None:
    """Apply a single pending migration and record it in the ledger."""
    statements = split_statements(migration.sql)
    ledger_insert = sql.SQL(
        "INSERT INTO {}.schema_migrations (version, filename, checksum, duration_ms) "
        "VALUES (%s, %s, %s, %s)"
    ).format(sql.Identifier(schema))
    set_path = sql.SQL("SET LOCAL search_path = {}, public").format(sql.Identifier(schema))

    started = time.monotonic()
    if migration.no_transaction:
        # Autocommit, statement-by-statement (CREATE INDEX CONCURRENTLY etc.).
        for statement in statements:
            conn.execute(statement)  # type: ignore[arg-type]
        duration_ms = int((time.monotonic() - started) * 1000)
        conn.execute(
            ledger_insert,
            (migration.version, migration.filename, migration.checksum, duration_ms),
        )
    else:
        with conn.transaction():
            conn.execute(set_path)
            for statement in statements:
                conn.execute(statement)  # type: ignore[arg-type]
            duration_ms = int((time.monotonic() - started) * 1000)
            conn.execute(
                ledger_insert,
                (migration.version, migration.filename, migration.checksum, duration_ms),
            )


def apply_migrations(
    conn: psycopg.Connection,
    *,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    schema: str = "analyzer",
) -> list[int]:
    """Run all pending migrations under the advisory lock (spec 02 §3.2).

    Returns the versions applied on this call (empty on an up-to-date database).
    The connection is switched to autocommit; the caller owns closing it.
    """
    migrations = discover_migrations(migrations_dir)
    by_version = {m.version: m for m in migrations}

    conn.autocommit = True
    # Session-scoped, blocking: the second concurrent starter waits here, then
    # finds nothing pending. Released in the finally (or on disconnect).
    conn.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
    try:
        conn.execute(sql.SQL("SET search_path = {}, public").format(sql.Identifier(schema)))
        _ensure_ledger(conn, schema)
        applied = _load_applied(conn, schema)
        _verify_applied(applied, by_version)

        max_applied = max(applied) if applied else 0
        pending = [m for m in migrations if m.version > max_applied]
        applied_now: list[int] = []
        for migration in pending:
            logger.info("Applying migration %s", migration.filename)
            try:
                _apply_one(conn, migration, schema)
            except MigrationError:
                raise
            except psycopg.Error as exc:
                raise MigrationError(f"migration {migration.filename} failed: {exc}") from exc
            applied_now.append(migration.version)
        if applied_now:
            logger.info("Applied %d migration(s): %s", len(applied_now), applied_now)
        else:
            logger.info("Database schema is up to date (max version %d)", max_applied)
        return applied_now
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
