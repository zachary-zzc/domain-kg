"""Non-source work cannot borrow a source identity or another operation's artifacts."""
# ruff: noqa: F811

import hashlib
from uuid import uuid4

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_source_less_operation_artifacts_have_exact_curation_ownership(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
    )
    data = await graph.repository.open(scope)
    alpha = next(item.entity.id for item in data.observations if item.name == "Alpha")
    receipt = await RevisionService(queue, coordinator.providers).update_node(
        editor,
        NodeUpdate(
            kg_id=job.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(name="Reviewed Alpha"),
            reason="Artifact boundary fixture",
            idempotency_key="curation-artifact",
        ),
    )
    leased = await queue.claim("curation-artifact-worker")
    assert leased.id == receipt.job_id and leased.kind == "curate"
    await queue.authorize(leased)
    digest = hashlib.sha256(b"curation-operation-v1").hexdigest()
    checkpoint = await queue.store_artifact(leased, "embedding", digest, b'{"fixture":"sealed"}')
    await queue.checkpoint(leased, checkpoint)
    assert await queue.load_checkpoint(leased, "embedding", digest) == checkpoint
    raw = await queue.database.jobs.find_one({"id": str(leased.id)})
    assert "material_id" not in raw and raw["material_ids"] == [str(source.material_id)]
    assert await queue.database.job_artifacts.count_documents({"job_id": str(leased.id)}) == 0
    artifact = await queue.database.operation_artifacts.find_one(
        {"id": str(checkpoint.artifact_id)}
    )
    assert artifact["owner"] == {
        "org_id": str(job.org_id),
        "kg_id": str(job.kg_id),
        "id": str(receipt.revision_id),
    }
    assert "blob" not in artifact and "source_version_id" not in artifact
    await queue.database.operation_artifacts.update_one(
        {"id": str(checkpoint.artifact_id)}, {"$set": {"owner.kg_id": str(uuid4())}}
    )
    with pytest.raises(DomainError) as error:
        await queue.load_checkpoint(leased, "embedding", digest)
    assert error.value.code == ErrorCode.PROJECTION_INCONSISTENT


@pytest.mark.parametrize("corruption", ["job_target", "receipt_job", "curation_job"])
async def test_curation_job_and_receipt_references_cannot_borrow_another_revision(
    publishing, corruption
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=editor.kg_ids, query="Alpha", mode="graph"), job.kg_id
    )
    alpha = next(
        item.entity.id
        for item in (await graph.repository.open(scope)).observations
        if item.name == "Alpha"
    )
    service = RevisionService(queue, coordinator.providers)
    command = NodeUpdate(
        kg_id=job.kg_id,
        entity_id=alpha,
        expected_revision=0,
        source_ids=(source.material_id,),
        changes=NodeChanges(name="First pending correction"),
        reason="Reference fixture",
        idempotency_key="first-curation-reference",
    )
    first = await service.update_node(editor, command)
    second = await service.update_node(
        editor,
        command.model_copy(
            update={
                "changes": NodeChanges(name="Other pending correction"),
                "idempotency_key": "second-curation-reference",
            }
        ),
    )
    if corruption == "job_target":
        await queue.database.jobs.update_one(
            {"id": str(first.job_id)}, {"$set": {"target_id": str(second.revision_id)}}
        )
        with pytest.raises(DomainError):
            await queue.get(editor, first.job_id)
    else:
        await queue.database.curation_commands.update_one(
            {"revision_id": str(first.revision_id)},
            {"$set": {"receipt.job_id": str(second.job_id)}},
        )
        if corruption == "curation_job":
            await queue.database.curation_revisions.update_one(
                {"id": str(first.revision_id)}, {"$set": {"job_id": str(second.job_id)}}
            )
        with pytest.raises(DomainError):
            await service.update_node(editor, command)
