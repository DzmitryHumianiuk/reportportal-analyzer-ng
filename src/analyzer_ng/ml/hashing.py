"""Deterministic 64-bit hashing helpers (spec 03 §2.3, §3.2–§3.3).

All identity hashes in the pipeline use xxHash's XXH3 64-bit variant (``xxhash``,
BSD-2). The results are stored in Postgres ``bigint`` columns, which are
**signed**, so :func:`xxh3_64_signed` folds the unsigned 64-bit digest into the
signed range. Hashing is over UTF-8 bytes with no seed, which makes the digest
identical across processes, runs and machines (the determinism contract).
"""

from __future__ import annotations

import xxhash

_UINT64_SPAN = 1 << 64
_INT64_MAX = (1 << 63) - 1


def to_signed64(value: int) -> int:
    """Fold an unsigned 64-bit integer into the signed ``bigint`` range."""
    value &= _UINT64_SPAN - 1
    return value - _UINT64_SPAN if value > _INT64_MAX else value


def xxh3_64_unsigned(text: str) -> int:
    """Unsigned XXH3-64 digest of ``text`` (UTF-8, no seed)."""
    return xxhash.xxh3_64_intdigest(text.encode("utf-8"))


def xxh3_64_signed(text: str) -> int:
    """Signed (``bigint``-safe) XXH3-64 digest of ``text``."""
    return to_signed64(xxh3_64_unsigned(text))
