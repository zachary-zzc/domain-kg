"""Partial repair, changed snapshots and revoked owners cannot advance acceptance."""
# ruff: noqa: F811

import pytest
from pymongo.asynchronous.collection import AsyncCollection
from test_maintenance_admission import accepted_human_edit
from test_publication import publishing  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.maintenance import MaintenanceService
from domain_kg.operations.models import MaintenanceCommand
from domain_kg.storage.qdrant import VectorProjection

pytestmark = pytest.mark.storage


@pytest.mark.parametrize(
    "fault", ["vector_write", "snapshot_changed", "commit_interrupted", "owner_revoked"]
)
async def test_failed_repair_preserves_the_accepted_view(publishing, team, monkeypatch, fault):
    source, alpha, curation = await accepted_human_edit(publishing)
    _, editor, queue, _, _, _ = publishing
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    before = await queue.database.kgs.find_one({"id": str(source.kg_id)})
    await OwnerOperations(queue).submit(
        owner,
        MaintenanceCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            curation_id=curation.revision_id,
            idempotency_key="failed-repair",
        ),
    )
    job = await queue.claim("maintenance-fault", kinds=("maintenance",))
    if fault == "commit_interrupted":
        insert = AsyncCollection.insert_one

        async def interrupted(collection, document, *args, **kwargs):
            if collection.name == "maintenance_results":
                raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic repair commit failure.")
            return await insert(collection, document, *args, **kwargs)

        monkeypatch.setattr(AsyncCollection, "insert_one", interrupted)
    else:
        write = VectorProjection.write

        async def changed(adapter, rows, vectors):
            if fault == "vector_write":
                raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic vector write failure.")
            await write(adapter, rows, vectors)
            if fault == "snapshot_changed":
                await queue.database.kgs.update_one(
                    {"id": str(source.kg_id)}, {"$inc": {"visibility_revision": 1}}
                )
            else:
                await queue.database.members.update_one(
                    {"org_id": ns["org_id"], "id": str(team[3]["owner"].id)},
                    {"$set": {"suspended": True}},
                )
                await queue.database.organizations.update_one(
                    {"id": ns["org_id"]}, {"$inc": {"policy_revision": 1}}
                )

        monkeypatch.setattr(VectorProjection, "write", changed)
    with pytest.raises(DomainError):
        await MaintenanceService(queue).run(job)
    after = await queue.database.kgs.find_one({"id": str(source.kg_id)})
    assert (after["published_revision"], after["published_manifest_id"]) == (
        2,
        before["published_manifest_id"],
    )
    assert await queue.database.maintenance_results.count_documents(ns) == 0
    assert (
        await queue.database.publication_manifests.count_documents(
            ns | {"maintenance_operation_id": str(job.target_id)}
        )
        == 0
    )
    assert (await queue.database.jobs.find_one(queue._scope(job)))["stage"] != "ready"
    current_editor = await queue.policy.resolve_access(
        team[2]["editor"], [source.kg_id], Action.READ
    )
    assert (
        await GraphService(queue).get_node(current_editor, source.kg_id, alpha)
    ).name == "Reviewed Alpha"

    if fault == "vector_write":
        monkeypatch.undo()
        await queue.fail(
            job, DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic partial attempt.")
        )
        await queue.retry(owner, job.id, idempotency_key="retry-partial-maintenance")
        retried = await queue.claim("recover-partial-maintenance", kinds=("maintenance",))
        assert retried.id == job.id and retried.attempt_id != job.attempt_id
        await MaintenanceService(queue).run(retried)
        assert await queue.accepted(retried)
        assert (await queue.database.kgs.find_one({"id": str(source.kg_id)}))[
            "published_revision"
        ] == 3
        assert (
            await GraphService(queue).get_node(editor, source.kg_id, alpha)
        ).name == "Reviewed Alpha"
        old = await queue.database.projection_attempts.find_one(
            ns | {"job.attempt_id": str(job.attempt_id)}
        )
        assert old["state"] != "accepted"
