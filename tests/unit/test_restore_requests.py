"""A restore request selects approved references, never grants or target IDs."""

from uuid import uuid4

import pytest
from pydantic import ValidationError


def command():
    return dict(
        kg_id=uuid4(),
        expected_published_revision=0,
        backup_job_id=uuid4(),
        placement_id=uuid4(),
        provider_profile_id=uuid4(),
        name="Recovered KG",
        idempotency_key="restore-request",
    )


def test_restore_command_rejects_target_identity_and_credential_fields():
    from domain_kg.operations.models import RestoreCommand

    fields = command()
    assert RestoreCommand(**fields).kind == "restore"
    for key in ("target_kg_id", "owner_id", "grants", "provider_api_key", "database_dsn"):
        with pytest.raises(ValidationError):
            RestoreCommand(**fields, **{key: str(uuid4())})


def test_restore_target_keeps_a_distinct_same_org_namespace_and_approved_profile():
    from domain_kg.catalog.models import StorageProfile
    from domain_kg.models import Namespace, ScopedRef
    from domain_kg.operations.restore_targets import RestoreTarget

    org, source, target = uuid4(), uuid4(), uuid4()
    profile = StorageProfile(
        id=uuid4(),
        revision=0,
        mongo_id=uuid4(),
        neo4j_id=uuid4(),
        qdrant_id=uuid4(),
        object_store_id=uuid4(),
        allowed_org_ids={org},
    )
    fields = dict(
        org_id=org,
        kg_id=source,
        id=uuid4(),
        job=ScopedRef(org_id=org, kg_id=source, id=uuid4()),
        target=Namespace(org_id=org, kg_id=target),
        generation=uuid4(),
        placement=profile,
    )
    assert RestoreTarget(**fields).state == "allocated"
    for invalid in (Namespace(org_id=org, kg_id=source), Namespace(org_id=uuid4(), kg_id=target)):
        with pytest.raises(ValidationError):
            RestoreTarget(**(fields | {"target": invalid}))
    with pytest.raises(ValidationError):
        RestoreTarget(
            **(fields | {"placement": profile.model_copy(update={"allowed_org_ids": frozenset()})})
        )
