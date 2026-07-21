-- label_event orphan reaper — tombstone table (tech-debt #6).
--
-- `label_event` (spec 02 §2.7) is the append-only learning log and is deliberately
-- PRESERVED by every delete path (delete_project, item_remove, launch_remove,
-- remove_by_*): RP's project-wide "Generate index" is a delete->rebuild that drives
-- the same `delete` route as a genuine project deletion, and wiping label_event on a
-- reindex would silently destroy the project's GBM-training / KB-purity history.
--
-- The cost of that preservation is that a GENUINE deletion (project/item/launch
-- removed forever, never re-indexed) leaves label_event rows with no surviving
-- test_item — orphans that accumulate and were never cleaned. This table backs a
-- nightly reaper that closes that leak WITHOUT ever touching live history during a
-- reindex.
--
-- Design: grace is measured from when the reaper FIRST observed an item orphaned —
-- never from label_event.ts, which can be arbitrarily old for a still-live item and
-- would let a reaper run that lands inside the minutes-long reindex window purge an
-- old-but-live label. Each nightly pass:
--   1. MARK   — insert a tombstone (first_orphaned_at = now()) for every
--               (project_id,item_id) present in label_event but with NO test_item.
--   2. UNMARK — delete tombstones whose item has a test_item again (a reindex
--               re-created it, typically within minutes → the next night, long
--               before the grace elapses).
--   3. SWEEP  — delete label_event rows (and their tombstone) only for items
--               tombstoned longer than the grace AND still with no test_item.
-- A normal reindex therefore never reaches SWEEP: its tombstone is cleared by UNMARK
-- on the first pass after the items reappear. Only an item orphaned continuously for
-- the full grace (genuine deletion, never rebuilt) is purged.
CREATE TABLE label_event_orphan (
    project_id        bigint       NOT NULL,
    item_id           bigint       NOT NULL,
    first_orphaned_at timestamptz  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, item_id)
);
