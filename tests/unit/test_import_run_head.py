"""The initial import page needs only an actor-owned, policy-checked summary."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.materials.import_runs import ImportRuns


def head_service():
    access = SimpleNamespace(
        org_id=UUID(int=1),
        kg_ids=(UUID(int=2),),
        principal=SimpleNamespace(issuer="https://idp.example", subject="editor", kind="human"),
    )
    raw = {
        "id": str(UUID(int=3)),
        "created_at": datetime(2026, 10, 31, tzinfo=UTC),
        "files": [{"path": "private-filename.pdf"}] * 46,
    }
    policy = SimpleNamespace(revalidate=AsyncMock(return_value=access))
    collection = SimpleNamespace(find_one=AsyncMock(return_value=raw))
    queue = SimpleNamespace(get=AsyncMock(side_effect=AssertionError("No job reads in head")))
    service = ImportRuns(
        SimpleNamespace(policy=policy, database=SimpleNamespace(import_runs=collection)), queue
    )
    return service, access, raw, policy, collection, queue


async def test_import_head_uses_exact_actor_binding_without_loading_job_or_file_metadata():
    service, access, raw, policy, collection, queue = head_service()
    head = await service.head(access, UUID(int=2), UUID(int=3))
    assert head.model_dump(mode="json") == {
        "id": str(UUID(int=3)),
        "created_at": "2026-10-31T00:00:00Z",
        "file_count": 46,
    }
    collection.find_one.assert_awaited_once_with(
        {
            "org_id": str(access.org_id),
            "kg_id": str(UUID(int=2)),
            "issuer": access.principal.issuer,
            "subject": access.principal.subject,
            "kind": access.principal.kind,
            "id": raw["id"],
        }
    )
    assert policy.revalidate.await_count == 2
    assert all(call.kwargs == {"action": Action.READ} for call in policy.revalidate.await_args_list)
    queue.get.assert_not_awaited()


async def test_import_head_does_not_return_when_policy_changes_after_the_manifest_read():
    service, access, _, policy, _, _ = head_service()
    policy.revalidate.side_effect = [
        access,
        DomainError(ErrorCode.POLICY_CHANGED, "Policy changed during the read."),
    ]
    with pytest.raises(DomainError) as denied:
        await service.head(access, UUID(int=2), UUID(int=3))
    assert denied.value.code == ErrorCode.POLICY_CHANGED
