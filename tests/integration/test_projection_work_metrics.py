"""Small real Mongo snapshots cannot expose other namespaces or stale access."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pymongo.asynchronous.cursor import AsyncCursor
from pymongo.errors import OperationFailure

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action

pytestmark = pytest.mark.storage


async def test_work_metrics_scoped_stages_and_bounded_snapshot(team):
    from domain_kg.operations.work_metrics import ProjectionWorkMetrics

    _, policy, principals, _, org, kg = team
    access = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    ns = {"org_id": str(org), "kg_id": str(kg)}
    timestamp = datetime.now(UTC) - timedelta(seconds=15)
    timestamp = timestamp.replace(microsecond=timestamp.microsecond // 1000 * 1000)

    def row(stage="queued", kind="ingest", **extra):
        return (
            ns
            | {
                "id": str(uuid4()),
                "kind": kind,
                "stage": stage,
                "created_at": timestamp,
                "private_text": "must-not-appear",
                "actor": principals["owner"].model_dump(mode="json"),
                "idempotency_key": str(uuid4()),
            }
            | extra
        )

    rows = [
        row(stage)
        for stage in ("queued", "projecting", "failed", "repair_required", "ready", "cancelled")
    ]
    rows += [row(kind="backup"), row(org_id=str(uuid4())), row(kg_id=str(uuid4()))]
    await policy.database.jobs.insert_many(rows)
    result = await ProjectionWorkMetrics(policy).get(access, kg)
    assert result.pending_jobs == 2 and result.failed_jobs == 1 and result.repair_required_jobs == 1
    assert result.oldest_pending_at == timestamp and result.oldest_pending_seconds >= 15
    assert result.sampled_jobs == 4 and not result.truncated
    assert "must-not-appear" not in result.model_dump_json()
    await policy.database.jobs.insert_many([row() for _ in range(1000)])
    overflow = await ProjectionWorkMetrics(policy).get(access, kg)
    assert overflow.truncated and overflow.sampled_jobs == 1000
    assert overflow.pending_jobs + overflow.failed_jobs + overflow.repair_required_jobs == 1000
    for role in ("reader", "editor"):
        actor = await policy.resolve_access(principals[role], [kg], Action.READ)
        with pytest.raises(DomainError):
            await ProjectionWorkMetrics(policy).get(actor, kg)
    with pytest.raises(DomainError):
        await ProjectionWorkMetrics(policy).get(access, uuid4())


async def test_work_metrics_revoke_and_corrupt_rows(team, monkeypatch):
    from domain_kg.operations.work_metrics import ProjectionWorkMetrics

    _, policy, principals, members, org, kg = team
    access = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    original = AsyncCursor.to_list

    async def revoke(cursor, *args, **kwargs):
        rows = await original(cursor, *args, **kwargs)
        if cursor.collection.name == "jobs":
            await policy.database.members.update_one(
                {"id": str(members["owner"].id)}, {"$set": {"suspended": True}}
            )
        return rows

    monkeypatch.setattr(AsyncCursor, "to_list", revoke)
    with pytest.raises(DomainError):
        await ProjectionWorkMetrics(policy).get(access, kg)
    monkeypatch.setattr(AsyncCursor, "to_list", original)
    await policy.database.members.update_one(
        {"id": str(members["owner"].id)}, {"$set": {"suspended": False}}
    )
    access = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)

    async def corrupt(cursor, *args, **kwargs):
        if cursor.collection.name != "jobs":
            return await original(cursor, *args, **kwargs)
        return [
            {
                "org_id": str(org),
                "kg_id": str(uuid4()),
                "id": str(uuid4()),
                "kind": "ingest",
                "stage": "queued",
                "created_at": datetime.now(UTC),
            }
        ]

    monkeypatch.setattr(AsyncCursor, "to_list", corrupt)
    with pytest.raises(DomainError) as invalid:
        await ProjectionWorkMetrics(policy).get(access, kg)
    assert invalid.value.code == ErrorCode.PROJECTION_INCONSISTENT

    async def unavailable(cursor, *args, **kwargs):
        if cursor.collection.name != "jobs":
            return await original(cursor, *args, **kwargs)
        raise OperationFailure("private backend detail")

    monkeypatch.setattr(AsyncCursor, "to_list", unavailable)
    with pytest.raises(DomainError) as backend:
        await ProjectionWorkMetrics(policy).get(access, kg)
    assert backend.value.code == ErrorCode.BACKEND_UNAVAILABLE
    assert "private backend detail" not in backend.value.message
