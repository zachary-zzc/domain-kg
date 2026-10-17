"""Restore identity partitions and shared node counters without replaying merge intent."""

from uuid import UUID, uuid4, uuid5

import pytest
from test_restore_curations import history
from test_restore_remapping import remapper

from domain_kg.errors import DomainError
from domain_kg.graph.identity_records import identity_directive
from domain_kg.operations.restore_documents import RestoreDocuments


def identity_history():
    values, row = history()
    first, second = values[4].subject.id, values[4].target.id
    row.update(
        kind="identity",
        operation="merge",
        identity_sequence=1,
        members=[str(first), str(second)],
        target_revisions=[
            dict(
                target_id=str(entity),
                state_id=str(uuid5(entity, f"curation-scope/{row['scope_key']}")),
                expected_revision=0,
            )
            for entity in (first, second)
        ],
        before={
            "groups": [
                dict(canonical_id=str(entity), member_ids=[str(entity)])
                for entity in (first, second)
            ]
        },
        after={"groups": [dict(canonical_id=str(first), member_ids=[str(first), str(second)])]},
        retracts=None,
    )
    return values, row


def test_identity_history_remaps_partitions_and_every_shared_counter_reference():
    from domain_kg.operations.restore_identity import RestoreIdentity

    values, row = identity_history()
    mapping = remapper(values)
    restored = RestoreIdentity(RestoreDocuments(mapping, originals=())).revision(row)
    assert identity_directive(restored) == mapping.manual_identity(identity_directive(row))
    assert restored["actor"] == row["actor"] and restored["provenance"] == row["provenance"]
    assert set(restored["members"]) != set(row["members"])
    for counter in restored["target_revisions"]:
        assert counter["state_id"] == str(
            uuid5(UUID(counter["target_id"]), f"curation-scope/{row['scope_key']}")
        )
        assert counter["expected_revision"] == 0


@pytest.mark.parametrize("corruption", ["member", "counter", "partition"])
def test_identity_history_rejects_unknown_entities_and_inconsistent_partitions(corruption):
    from domain_kg.operations.restore_identity import RestoreIdentity

    values, row = identity_history()
    if corruption == "member":
        row["members"][1] = str(uuid4())
    elif corruption == "counter":
        row["target_revisions"][1]["state_id"] = str(uuid4())
    else:
        row["before"] = {"groups": []}
    with pytest.raises(DomainError):
        RestoreIdentity(RestoreDocuments(remapper(values), originals=())).revision(row)
