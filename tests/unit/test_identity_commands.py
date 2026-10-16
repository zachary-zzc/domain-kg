"""Group changes require an explicit, complete set of original IDs and revision proofs."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.graph.identity_commands import IdentityChange
from domain_kg.graph.identity_models import IdentityGroup


def command(**changes):
    alpha, beta, material, kg = (uuid4() for _ in range(4))
    value = dict(
        kg_id=kg,
        entity_id=alpha,
        action="merge",
        member_ids=(alpha, beta),
        expected_revision=0,
        member_revisions=((beta, 0),),
        source_ids=(material,),
        reason="Reviewed identity",
        idempotency_key="merge-explicit-origins",
    )
    value.update(changes)
    return value


def test_merge_requires_each_original_member_revision():
    value = command()
    result = IdentityChange(**value)
    assert result.state.groups[0].canonical_id == result.entity_id
    assert set(dict(result.expected_targets)) == set(result.member_ids)
    with pytest.raises(ValidationError):
        IdentityChange(**(value | {"member_revisions": ()}))
    with pytest.raises(ValidationError):
        IdentityChange(**(value | {"member_ids": (value["entity_id"],)}))


def test_preview_can_discover_current_revisions_but_cannot_commit_without_them():
    value = command(dry_run=True, member_revisions=())
    assert IdentityChange(**value).dry_run
    with pytest.raises(ValidationError):
        IdentityChange(**(value | {"dry_run": False}))


def test_split_partitions_are_disjoint_and_cover_the_primary_original():
    value = command()
    alpha, beta = value["member_ids"]
    partitions = (
        IdentityGroup(canonical_id=alpha, member_ids=(alpha,)),
        IdentityGroup(canonical_id=beta, member_ids=(beta,)),
    )
    result = IdentityChange(
        **(value | {"action": "split", "member_ids": (), "partitions": partitions})
    )
    assert result.members == {alpha, beta}
    with pytest.raises(ValidationError):
        IdentityChange(
            **(value | {"action": "split", "member_ids": (), "partitions": partitions[:1]})
        )
    with pytest.raises(ValidationError):
        IdentityChange(**(value | {"action": "split", "partitions": partitions}))


def test_identity_operation_never_infers_source_ownership_or_accepts_actor_arguments():
    value = command()
    with pytest.raises(ValidationError):
        IdentityChange(**(value | {"source_ids": ()}))
    with pytest.raises(ValidationError):
        IdentityChange(**(value | {"actor": "owner"}))
    public = IdentityChange(**(value | {"curation_scope": "kg_public", "source_ids": ()}))
    assert public.curation_scope == "kg_public"
    with pytest.raises(ValidationError):
        IdentityChange(**(value | {"curation_scope": "kg_public"}))


def test_common_curation_command_accepts_typed_identity_operation():
    from domain_kg.revisions.models import CurationCommand

    value = command()
    accepted = CurationCommand.model_validate(value)
    assert IdentityChange.model_validate(accepted.model_dump()).members == set(value["member_ids"])


def test_visibility_command_keeps_its_existing_idempotency_serialization():
    from domain_kg.revisions.models import CurationCommand

    value = command()
    legacy = {
        key: value[key]
        for key in (
            "kg_id",
            "entity_id",
            "expected_revision",
            "source_ids",
            "reason",
            "idempotency_key",
        )
    }
    legacy.update(action="suppress", curation_scope="source", dry_run=False)
    parsed = CurationCommand.model_validate(legacy)
    expected = dict(
        legacy,
        kg_id=str(legacy["kg_id"]),
        entity_id=str(legacy["entity_id"]),
        source_ids=list(map(str, legacy["source_ids"])),
    )
    assert parsed.model_dump(mode="json") == expected
    with pytest.raises(ValidationError):
        CurationCommand.model_validate(legacy | {"member_ids": value["member_ids"]})
