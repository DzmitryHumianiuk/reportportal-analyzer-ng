"""Drain3 manager tests (spec 03 §2, acceptance §11 Drain3/templates).

Covers masking, determinism of template hashes, CAS persistence round-trip and
the state-loss rebuild recovery rate. All network-free (in-memory fake store).
"""

from __future__ import annotations

from analyzer_ng.ml.drain import DrainManager, build_config, load_manager, mask_text, save_manager
from analyzer_ng.ml.hashing import xxh3_64_signed


class FakeDrainStore:
    """In-memory Drain3StateStore (spec 02 §2.3 CAS + template mirror)."""

    def __init__(self) -> None:
        self.state: bytes | None = None
        self.version = 0
        self.templates: dict[int, dict] = {}

    def load(self, project_id: int) -> tuple[bytes, int] | None:
        return (self.state, self.version) if self.state is not None else None

    def save(self, project_id: int, state: bytes, expected_version: int, config: dict) -> bool:
        if expected_version != self.version:
            return False
        self.state = state
        self.version += 1
        return True

    def upsert_templates(self, project_id: int, templates) -> int:
        for t in templates:
            self.templates[t["template_id"]] = t
        return len(templates)


def _corpus(n: int = 1000) -> list[str]:
    bases = [
        "Connection refused to service user with id {}",
        "HTTP status code {} returned from gateway",
        "NullPointerException in module handler number {}",
        "Timeout after {} seconds waiting for response",
        "Disk usage at {} percent on node worker",
        "Retry attempt {} failed for the payment request",
        "Cache miss for key entry index {} in region east",
        "Deadlock detected on transaction batch {} rollback",
        "Invalid token provided for account session {} denied",
        "File not found while loading resource bundle {} default",
    ]
    return [bases[i % len(bases)].format(i) for i in range(n)]


def _hash_sets(manager: DrainManager, messages: list[str]) -> list[list[str]]:
    return [manager.match_message(m) for m in messages]


def test_mask_text_applies_all_rules():
    text = (
        "GET https://api.example.com/v1/x from 10.0.0.1:8080 "
        "uuid 550e8400-e29b-41d4-a716-446655440000 hex 0xdeadbeef1234 "
        "path /var/log/app.log num 42"
    )
    masked = mask_text(text)
    assert "<URL>" in masked
    assert "<IP>" in masked
    assert "<UUID>" in masked
    assert "<HEX>" in masked
    assert "<PATH>" in masked
    assert "<NUM>" in masked


def test_same_stream_twice_identical_hashes():
    corpus = _corpus(200)
    m1 = DrainManager()
    m2 = DrainManager()
    for m in corpus:
        m1.add_message(m)
    for m in corpus:
        m2.add_message(m)
    set1 = {r["pattern"] for r in m1.cluster_mirror_rows()}
    set2 = {r["pattern"] for r in m2.cluster_mirror_rows()}
    assert set1 == set2
    assert len(set1) > 1  # sanity: templates actually formed


def test_masking_makes_volatile_values_collapse():
    m = DrainManager()
    h1 = m.add_message("User 123 failed to authenticate against realm prod")
    h2 = m.add_message("User 987654 failed to authenticate against realm prod")
    assert [x.hash_hex for x in h1] == [x.hash_hex for x in h2]


def test_cas_persistence_roundtrip_no_new_hashes():
    corpus = _corpus(300)
    store = FakeDrainStore()
    m1, version = load_manager(store, project_id=7)
    for msg in corpus:
        m1.add_message(msg)
    assert save_manager(store, 7, m1, version) is True
    baseline = _hash_sets(m1, corpus)

    # Restart: load from persisted state.
    m2, _ = load_manager(store, project_id=7)
    restored = _hash_sets(m2, corpus)
    assert restored == baseline
    # And re-processing does not mint any new template hashes.
    original_hashes = {h for row in baseline for h in row}
    reprocessed = {x.hash_hex for msg in corpus for x in m2.add_message(msg)}
    assert reprocessed <= original_hashes


def test_mirror_identity_is_content_hash_not_cluster_id():
    """spec 03 §2.3: mirror rows are keyed by template_hash, not the volatile cluster_id."""
    a = "Payment gateway declined transaction for merchant account east"
    b = "Inventory reservation failed for warehouse west region shelf"

    m1 = DrainManager()
    ha = m1.add_message(a)[0]

    # Fresh miner, different insertion order → Drain assigns `a` a DIFFERENT
    # cluster_id than it had in m1 (cluster_id is volatile).
    m2 = DrainManager()
    m2.add_message(b)  # claims the cluster_id that `a` held in m1
    ha2 = m2.add_message(a)[0]

    assert ha2.cluster_id != ha.cluster_id  # cluster_id churns
    assert ha2.hash_signed == ha.hash_signed  # content-hash identity is stable

    # Every mirror row's template_id equals xxh3 of its pattern, and no two
    # distinct templates share a template_id (no cluster_id-reuse collisions).
    rows = m2.cluster_mirror_rows()
    for r in rows:
        assert r["template_id"] == xxh3_64_signed(r["pattern"])
        assert r["template_id"] == r["template_hash"]
    assert len({r["template_id"] for r in rows}) == len(rows)


def test_mirror_identity_survives_lru_eviction_and_rebuild():
    """A template evicted under LRU (then re-seen / rebuilt) keeps the same template_id."""
    cfg = build_config(max_clusters=4)  # tiny LRU to force eviction
    m = DrainManager(config=cfg)
    # Structurally distinct messages so each forms its own cluster (no widening).
    templates = [
        "Connection refused to authentication service on primary",
        "HTTP gateway returned server error for checkout",
        "Null pointer in the inventory module handler",
        "Read timeout while waiting for downstream response",
        "Disk usage exceeded threshold on the worker node",
        "Retry limit reached for the payment settlement job",
        "Cache miss forced a reload of the pricing catalog",
        "Deadlock detected during the ledger reconciliation batch",
    ]
    first = m.add_message(templates[0])[0]
    for t in templates[1:]:  # evicts templates[0] (LRU, max 4 clusters)
        m.add_message(t)
    reseen = m.add_message(templates[0])[0]  # re-created with a fresh cluster_id
    assert reseen.hash_signed == first.hash_signed  # identity unchanged by eviction

    # Rebuild a fresh tree from the persisted template texts → same identity.
    rebuilt = DrainManager()
    rebuilt.rebuild_from_templates([r["pattern"] for r in m.cluster_mirror_rows()])
    by_pattern = {r["pattern"]: r["template_id"] for r in rebuilt.cluster_mirror_rows()}
    for r in m.cluster_mirror_rows():
        assert by_pattern[r["pattern"]] == r["template_id"]


def test_state_loss_rebuild_recovery_at_least_95pct():
    corpus = _corpus(1000)
    m1 = DrainManager()
    for msg in corpus:
        m1.add_message(msg)
    baseline = _hash_sets(m1, corpus)

    # Rebuild a fresh tree from stored template texts, most-frequent first (§2.3).
    rows = sorted(m1.cluster_mirror_rows(), key=lambda r: r["match_count"], reverse=True)
    m2 = DrainManager()
    m2.rebuild_from_templates([r["pattern"] for r in rows])
    rebuilt = _hash_sets(m2, corpus)

    recovered = sum(1 for a, b in zip(baseline, rebuilt, strict=True) if a == b)
    assert recovered / len(corpus) >= 0.95
