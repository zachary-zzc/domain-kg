"""The configured byte bound is enforced before opening oversized export content."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.queue import now
from domain_kg.materials.service import digest
from domain_kg.models import ScopedRef
from domain_kg.operations.export_models import ExportManifest, ExportObject
from domain_kg.operations.models import SnapshotPointer
from domain_kg.operations.retention_inventory import RetentionInventory
from domain_kg.operations.retention_policy import RetentionPolicy
from domain_kg.operations.retention_targets import RetainedTarget, RetentionPlan


def plan_fixture():
    org, kg, operation, attempt, job = (uuid4() for _ in range(5))
    ns = dict(org_id=org, kg_id=kg)
    at = now()
    descriptor = ExportManifest(
        **ns,
        export_id=operation,
        attempt_id=attempt,
        job_id=job,
        snapshot=SnapshotPointer(
            **ns, generation=uuid4(), revision=0, manifest_id=None, visibility_revision=0
        ),
        policy_revision=0,
        filters_hash="a" * 64,
        format="jsonl",
        node_count=0,
        edge_count=0,
        created_at=at - timedelta(days=3),
        expires_at=at - timedelta(days=2),
        content=ExportObject(
            **ns,
            id=uuid4(),
            export_id=operation,
            attempt_id=attempt,
            sha256="a" * 64,
            size_bytes=4,
            media_type="application/jsonl",
        ),
    )
    target = RetainedTarget(
        kind="export",
        operation_id=operation,
        attempt_id=attempt,
        job_id=job,
        descriptor=descriptor,
        content_hash=digest(descriptor.model_dump(mode="json")),
    )
    policy = RetentionPolicy(max_verify_bytes=1)
    plan = RetentionPlan(
        **ns,
        id=uuid4(),
        job=ScopedRef(**ns, id=uuid4()),
        storage_profile_id=uuid4(),
        resources="exports",
        policy=policy,
        created_at=at,
        cutoff=policy.cutoff(at),
        targets=(target,),
    )
    return plan, target


async def test_oversized_export_never_opens_content_or_seals_an_inventory():
    plan, target = plan_fixture()
    inventories = SimpleNamespace(create_index=AsyncMock(), find_one=AsyncMock(return_value=None))
    objects = SimpleNamespace(create_index=AsyncMock())
    database = SimpleNamespace(retention_inventories=inventories, retention_objects=objects)
    store = SimpleNamespace(open=Mock(side_effect=AssertionError("must not open large content")))
    with pytest.raises(DomainError) as rejected:
        await RetentionInventory(SimpleNamespace(database=database), plan, target).prepare(
            store, AsyncMock()
        )
    assert rejected.value.code == ErrorCode.QUOTA_EXCEEDED
    store.open.assert_not_called()


def test_retention_plan_rejects_changed_namespace_selection_and_expiry():
    plan, _ = plan_fixture()
    for changes in (
        {"kg_id": str(uuid4())},
        {"resources": "backups"},
        {"cutoff": plan.created_at.isoformat()},
    ):
        with pytest.raises(ValidationError):
            RetentionPlan.model_validate(plan.model_dump(mode="json") | changes)
