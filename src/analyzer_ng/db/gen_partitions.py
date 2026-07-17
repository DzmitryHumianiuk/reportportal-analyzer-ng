"""Dev-time generator for the hash-partition DDL blocks (spec 02 §2.4/§2.5/§6).

``test_item`` and ``failure_signature`` are hash-partitioned by ``project_id``
into ``ANALYZER_PG_PARTITIONS`` (default 16) partitions. Writing 16 ``CREATE
TABLE ... PARTITION OF`` statements plus 16 autovacuum ``ALTER TABLE`` statements
by hand — twice — invites copy errors (the spec calls this out), so this module
emits them and ``0001_init.sql`` embeds the output verbatim.

Run ``python -m analyzer_ng.db.gen_partitions`` to print the blocks.

The partition count is fixed at generation time: changing it later requires a
manual repartition (spec 02 §6, "unsupported in v1").
"""

from __future__ import annotations

# Hot partitioned tables (spec 02 §2.4, §2.5).
PARTITIONED_TABLES = ("test_item", "failure_signature")

DEFAULT_PARTITIONS = 16

# Autovacuum tuning for hot partitions (spec 02 §6).
AUTOVACUUM_VACUUM_SCALE_FACTOR = 0.05
AUTOVACUUM_ANALYZE_SCALE_FACTOR = 0.02


def partition_ddl(table: str, modulus: int = DEFAULT_PARTITIONS) -> str:
    """Return the CREATE-partition + autovacuum ALTER block for one table."""
    lines: list[str] = []
    for remainder in range(modulus):
        part = f"{table}_p{remainder:02d}"
        lines.append(
            f"CREATE TABLE {part} PARTITION OF {table}\n"
            f"    FOR VALUES WITH (MODULUS {modulus}, REMAINDER {remainder});"
        )
    lines.append("")
    for remainder in range(modulus):
        part = f"{table}_p{remainder:02d}"
        lines.append(
            f"ALTER TABLE {part} SET (\n"
            f"    autovacuum_vacuum_scale_factor = {AUTOVACUUM_VACUUM_SCALE_FACTOR},\n"
            f"    autovacuum_analyze_scale_factor = {AUTOVACUUM_ANALYZE_SCALE_FACTOR}\n"
            f");"
        )
    return "\n".join(lines)


def main(modulus: int = DEFAULT_PARTITIONS) -> None:
    for table in PARTITIONED_TABLES:
        print(f"-- {modulus} partitions of {table}:")
        print(partition_ddl(table, modulus))
        print()


if __name__ == "__main__":
    import os

    main(int(os.environ.get("ANALYZER_PG_PARTITIONS", DEFAULT_PARTITIONS)))
