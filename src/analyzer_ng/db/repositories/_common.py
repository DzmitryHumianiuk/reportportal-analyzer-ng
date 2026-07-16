"""Shared base + value-encoding helpers for the psycopg3 store layer.

The stores run against the synchronous ``psycopg_pool.ConnectionPool`` opened at
startup (spec 01 §2 ``pool.py``); the service is threaded/blocking by design
(spec 01 §2.1: the workload is CPU-bound, so asyncio buys nothing). Each store
is one file / one responsibility (RetrievalStore, KBStore, LabelStore,
StatsStore, Drain3StateStore, LlmCacheStore) and takes the pool.

Encoding helpers exist because psycopg's client-side binding sends a Python
``list[int]`` as ``smallint[]``/``integer[]`` (which breaks resolution of the
verbatim ``bigint[]`` operators — ``&&`` and ``analyzer.array_jaccard``), and a
``halfvec`` value is written as its text form. Both are rendered as PostgreSQL
literal strings, which the server coerces from ``unknown`` in context.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from psycopg import Connection, Cursor
from psycopg_pool import ConnectionPool


def require_row(cur: Cursor[Any]) -> tuple[Any, ...]:
    """Return the single row of a cursor, or raise if the query returned none.

    Used for ``RETURNING`` inserts and single-row aggregates that always yield a
    row — it narrows psycopg's ``fetchone() -> tuple | None`` for the type checker
    while surfacing a genuine "no row" as a clear error rather than a ``TypeError``.
    """
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("expected exactly one row, got none")
    return row


def halfvec_literal(vec: Sequence[float]) -> str:
    """A pgvector text literal ``[a,b,c]`` for a ``halfvec`` column/param."""
    return "[" + ",".join(repr(float(x)) for x in vec) + "]"


def bigint_array_literal(values: Sequence[int]) -> str:
    """A PostgreSQL array literal ``{1,2,3}`` for a ``bigint[]`` query param.

    Used for the verbatim ``&&`` / ``array_jaccard`` operands: passing a Python
    list makes psycopg pick a narrower element type and the operator/function
    fails to resolve against ``bigint[]``.
    """
    return "{" + ",".join(str(int(v)) for v in values) + "}"


def python_jaccard(a: Sequence[int], b: Sequence[int]) -> float:
    """Set Jaccard over two id sequences (mirrors ``analyzer.array_jaccard``)."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def parse_vector(text: str | None) -> list[float] | None:
    """Parse a pgvector text form ``[a,b,c]`` into a list of floats."""
    if text is None:
        return None
    return [float(x) for x in text.strip().lstrip("[").rstrip("]").split(",") if x]


class StoreBase:
    """Holds the connection pool and hands out pooled connections."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    @contextmanager
    def _conn(self) -> Iterator[Connection]:
        """A pooled connection in autocommit mode (each store method is atomic
        on its own; multi-statement methods open an explicit transaction)."""
        with self._pool.connection() as conn:
            prior = conn.autocommit
            if not prior:
                conn.autocommit = True
            try:
                yield conn
            finally:
                if conn.autocommit != prior:
                    conn.autocommit = prior
