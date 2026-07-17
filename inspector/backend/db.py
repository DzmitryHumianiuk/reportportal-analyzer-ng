"""Read-only PostgreSQL access for the inspector.

A tiny wrapper over psycopg3. Every connection runs with
``default_transaction_read_only = on`` so the inspector can never mutate the
analyzer's database, even by accident. Callers pass parameters positionally;
all queries in :mod:`.payloads` are parameterized (no string interpolation of
user input) and LIMIT-bounded.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg.rows import dict_row


class Database:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn

    @contextmanager
    def connect(self) -> Iterator[psycopg.Connection]:
        conn = psycopg.connect(self._dsn, autocommit=False)
        try:
            conn.execute("SET default_transaction_read_only = on")
            conn.execute("SET statement_timeout = '15s'")
            yield conn
            conn.rollback()  # never commit — read-only companion
        finally:
            conn.close()

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return list(cur.fetchall())

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        result = self.rows(sql, params)
        return result[0] if result else None

    def ping(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False
