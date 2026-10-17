"""Retained hidden data stays private and changing authority prevents generation switches."""
# ruff: noqa: F811

from datetime import UTC, datetime, timedelta

import pytest
from pymongo.asynchronous.collection import AsyncCollection
from test_maintenance_admission import accepted_human_edit
from test_publication import publishing  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.materials.purge_models import PurgeMaterial
from domain_kg.operations.generation import GenerationService
from domain_kg.operations.generation_capture import GenerationCapture
from domain_kg.operations.generation_preparation import GenerationPreparation
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import GenerationCommand
from domain_kg.revisions.journal import PublicationJournal, read_artifact
from domain_kg.storage.qdrant import VectorProjection

pytestmark = pytest.mark.storage


async def test_hidden_source_and_manual_artifacts_are_pinned_but_not_revealed(publishing, team):
    source, alpha, _ = await accepted_human_edit(publishing)
    materials, editor, queue, _, _, _ = publishing
    lifecycle = MaterialLifecycle(materials, queue)
    removed = await lifecycle.remove(
        editor,
        RemoveMaterial(
            kg_id=source.kg_id,
            material_id=source.material_id,
            action="delete",
            expected_revision=1,
            idempotency_key="hide-before-rebuild",
        ),
    )
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    await queue.database.material_access.update_one(
        ns | {"id": str(source.material_id)},
        {"$set": {"purge_after": datetime.now(UTC) - timedelta(seconds=1)}},
    )
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    await OwnerOperations(queue).submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id, expected_published_revision=2, idempotency_key="hidden-rebuild"
        ),
    )
    job = await queue.claim("hidden-rebuild", kinds=("generation",))
    capture = GenerationCapture(queue)
    inputs = await capture.start(job)
    assert len(inputs) == 2
    for item in inputs:
        document, checkpoint = await GenerationPreparation(queue).document(job, item)
        assert document.rows
        if item.parent.source:
            with pytest.raises(DomainError):
                await read_artifact(queue, owner, checkpoint, item.parent.source)
            permit = await capture.source_permit(job, item.parent.source)
            assert await read_artifact(
                queue, owner, checkpoint, item.parent.source, generation=permit
            )
            with pytest.raises(DomainError):
                await read_artifact(
                    queue,
                    owner.model_copy(update={"action": Action.READ}),
                    checkpoint,
                    item.parent.source,
                    generation=permit,
                )
    pin = await queue.database.read_pins.find_one(capture.pin_key(job))
    await queue.heartbeat(job)
    renewed = await queue.database.read_pins.find_one(capture.pin_key(job))
    assert renewed["expires_at"] >= pin["expires_at"]
    with pytest.raises(DomainError) as pinned:
        await lifecycle.purge(
            owner,
            PurgeMaterial(
                kg_id=source.kg_id,
                material_id=source.material_id,
                expected_revision=removed.revision,
                reason="Synthetic expired data",
                idempotency_key="generation-blocks-purge",
            ),
        )
    assert pinned.value.code == ErrorCode.REVISION_NOT_READY
    await GenerationService(queue).run(job)
    assert await queue.accepted(job)
    view = await PublicationJournal(queue).load_metadata(owner, source.kg_id)
    assert len(view.projections) == 2
    with pytest.raises(DomainError):
        await GraphService(queue).get_node(owner, source.kg_id, alpha)
    with pytest.raises(DomainError):
        await capture.guard(job)
    assert (await queue.database.material_access.find_one(ns | {"id": str(source.material_id)}))[
        "tombstoned"
    ] is True


@pytest.mark.parametrize(
    "fault", ["material_changed", "config_changed", "owner_revoked", "commit_interrupted"]
)
async def test_changed_authority_or_commit_failure_keeps_old_generation(
    publishing, team, monkeypatch, fault
):
    source, alpha, _ = await accepted_human_edit(publishing)
    _, editor, queue, _, _, _ = publishing
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    before = await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
    await OwnerOperations(queue).submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id, expected_published_revision=2, idempotency_key="fenced-rebuild"
        ),
    )
    job = await queue.claim("fenced-rebuild", kinds=("generation",))
    if fault == "commit_interrupted":
        insert = AsyncCollection.insert_one

        async def interrupt(collection, value, *args, **kwargs):
            if collection.name == "generation_results":
                raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic commit interruption.")
            return await insert(collection, value, *args, **kwargs)

        monkeypatch.setattr(AsyncCollection, "insert_one", interrupt)
    else:
        write = VectorProjection.write

        async def change(adapter, rows, vectors):
            await write(adapter, rows, vectors)
            if fault == "material_changed":
                await queue.database.material_access.update_one(
                    ns | {"id": str(source.material_id)}, {"$inc": {"revision": 1}}
                )
            elif fault == "config_changed":
                await queue.database.kg_provider_settings.update_one(ns, {"$inc": {"revision": 1}})
            else:
                await queue.database.members.update_one(
                    {"org_id": ns["org_id"], "id": str(team[3]["owner"].id)},
                    {"$set": {"suspended": True}},
                )
                await queue.database.organizations.update_one(
                    {"id": ns["org_id"]}, {"$inc": {"policy_revision": 1}}
                )

        monkeypatch.setattr(VectorProjection, "write", change)
    with pytest.raises(DomainError):
        await GenerationService(queue).run(job)
    after = await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
    assert tuple(
        after.get(key) for key in ("generation", "published_manifest_id", "published_revision")
    ) == tuple(
        before.get(key) for key in ("generation", "published_manifest_id", "published_revision")
    )
    assert await queue.database.generation_results.count_documents(ns) == 0
    assert await queue.database.generation_outputs.count_documents(ns | {"state": "sealed"}) == 0
    assert (
        await queue.database.publication_manifests.count_documents(
            ns | {"generation_operation_id": str(job.target_id)}
        )
        == 0
    )
    current = await queue.policy.resolve_access(team[2]["editor"], [source.kg_id], Action.READ)
    assert (
        await GraphService(queue).get_node(current, source.kg_id, alpha)
    ).name == "Reviewed Alpha"
