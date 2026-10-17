"""Accepted generation metadata cannot substitute another source or worker identity."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.operations.generation import GenerationService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import GenerationCommand
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.publication import accepted_document

pytestmark = pytest.mark.storage


async def test_rebuild_read_rejects_cross_document_lineage_substitution(publishing, team):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    await OwnerOperations(queue).submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id,
            expected_published_revision=1,
            idempotency_key="generation-integrity",
        ),
    )
    rebuild = await queue.claim("generation-integrity", kinds=("generation",))
    await GenerationService(queue).run(rebuild)
    binding = (await PublicationJournal(queue).load_metadata(owner, source.kg_id)).projections[0]
    key = {"org_id": str(source.org_id), "kg_id": str(source.kg_id), "id": str(binding.manifest_id)}
    missed = []
    for table, field, replacement in (
        ("projection_attempts", "source.org_id", str(uuid4())),
        ("projection_attempts", "source.kg_id", str(uuid4())),
        ("projection_attempts", "source.version_id", str(uuid4())),
        ("projection_attempts", "job.org_id", str(uuid4())),
        ("projection_attempts", "job.kg_id", str(uuid4())),
        ("projection_attempts", "generation_origin.job_id", str(uuid4())),
        ("projection_attempts", "generation_origin.base.source.version_id", str(uuid4())),
        ("projection_attempts", "embedded_checkpoint.job_id", str(uuid4())),
        ("projection_attempts", "job.target_id", str(uuid4())),
        ("projection_attempts", "job.kind", "ingest"),
        ("generation_outputs", "owner_key", "unrelated-owner"),
        ("generation_outputs", "input_id", str(uuid4())),
    ):
        original = await queue.database[table].find_one(key)
        before = original
        for component in field.split("."):
            before = before[component]
        await queue.database[table].update_one(key, {"$set": {field: replacement}})
        try:
            try:
                await accepted_document(queue, owner, binding)
            except DomainError:
                pass
            else:
                missed.append(table + "." + field)
        finally:
            await queue.database[table].update_one(key, {"$set": {field: before}})
    assert missed == []
    assert (await accepted_document(queue, owner, binding))[0].rows
