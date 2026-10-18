"""A placement fingerprint must not depend on a set's iteration order."""

from uuid import UUID, uuid4


def test_storage_profile_fingerprint_is_stable_for_colliding_org_set_order():
    from domain_kg.catalog.models import StorageProfile
    from domain_kg.revisions.writes import placement_hash

    identifiers = [UUID(int=1 + offset * (2**61 - 1)) for offset in range(3)]
    base = dict(
        id=uuid4(),
        revision=0,
        mongo_id=uuid4(),
        neo4j_id=uuid4(),
        qdrant_id=uuid4(),
        object_store_id=uuid4(),
    )
    first = StorageProfile(**base, allowed_org_ids=identifiers)
    second = StorageProfile(**base, allowed_org_ids=list(reversed(identifiers)))
    assert first == second
    assert placement_hash(first) == placement_hash(second)
