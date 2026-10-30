"""Images publish into the same KG with source ownership and honest interpretation status."""

import hashlib
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from test_entity_resolution import batch_for
from test_publication import make_publishing

from domain_kg.citations.service import CitationService
from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.ingestion.models import ExtractionBatch, ParsedDocument
from domain_kg.ingestion.pipeline import Pipeline, publication_steps
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.image_sources import ImageSources
from domain_kg.materials.models import AddMaterial, UploadCommand
from domain_kg.retrieval.evidence import SealedEvidence
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.journal import PublicationJournal, read_artifact
from domain_kg.revisions.publication import accepted_document

pytestmark = pytest.mark.storage
FIXTURES = Path(__file__).parents[1] / "fixtures/materials"


async def test_image_description_checkpoint_graph_provenance_and_protected_sources(
    material_service, team, catalog
):
    def extraction(chunk, profile):
        if "Alpha uses Beta." in chunk.text:
            batch = batch_for(chunk, profile)
            return batch.model_copy(
                update={
                    "entities": tuple(
                        e.model_copy(update={"aliases": (), "identifier": None})
                        for e in batch.entities
                    )
                }
            )
        return ExtractionBatch(
            org_id=chunk.org_id,
            kg_id=chunk.kg_id,
            material_id=chunk.material_id,
            version_id=chunk.version_id,
            chunk_id=chunk.id,
            schema_id=uuid4(),
            schema_revision=0,
            entities=(),
            assertions=(),
            provider_profile_id=profile,
        )

    # No OCR text in this synthetic diagram. Only the mocked image interpretation
    # supplies Alpha/Beta, so a native-only filter must remove every derived field.
    publishing = await make_publishing(
        material_service,
        team,
        extraction_factory=extraction,
        profile_overrides={"supports_images": True},
    )
    materials, access, queue, coordinator, steps, _ = publishing
    fallback = coordinator.providers.transport
    calls = []

    async def response(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/chat/completions") and isinstance(
            body["messages"][1]["content"], list
        ):
            calls.append(body["model"])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"sentences": ["Alpha uses Beta."]})},
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                },
            )
        return await fallback.handle_async_request(request)

    coordinator.providers.transport = httpx.MockTransport(response)
    import io

    from PIL import Image

    output = io.BytesIO()
    Image.new("RGB", (32, 32), "blue").save(output, format="PNG")
    data = output.getvalue()

    async def stream():
        yield data

    upload = await materials.upload(
        access,
        UploadCommand(
            kg_id=access.kg_ids[0],
            media_type="image/png",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            idempotency_key="image-upload",
        ),
        stream(),
    )
    receipt = await materials.add(
        access,
        AddMaterial(
            kg_id=access.kg_ids[0],
            title="Synthetic diagram",
            upload_id=upload.upload_id,
            idempotency_key="image-add",
        ),
    )
    job = await queue.claim("image-worker")
    for step in steps[:2]:
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
    # Simulate interruption after a completed image call, before chunk completion.
    from domain_kg.ingestion.image_preparation import interpret_images
    from domain_kg.ingestion.repository import SourceRepository

    parsed_before = await SourceRepository(queue).read(job, checkpoint)
    await interpret_images(
        queue, job, parsed_before, coordinator.providers, await steps[2].hash_for(job)
    )
    checkpoint = await steps[2].execute(job)
    await queue.checkpoint(job, checkpoint)
    assert calls == ["fixture-extraction"]
    runner = JobRunner(queue, Pipeline(steps + publication_steps(queue, coordinator, steps[-1])))
    await runner.run_job(job)
    status = await queue.get(access, receipt.job_id)
    assert status.stage == "ready_with_warnings", status.error
    assert "IMAGE_MODEL_INTERPRETED" in status.warnings
    kg = access.kg_ids[0]
    view = await PublicationJournal(queue).load(access, kg)
    binding = next(b for b in view.projections if b.source.version_id == receipt.version_id)
    embedded, _, _ = await accepted_document(queue, access, binding)
    parsed = ParsedDocument.model_validate_json(
        await read_artifact(queue, access, embedded.chunk_checkpoint, binding.source)
    )
    assert parsed.images[0].data_base64 is None and parsed.images[0].preview_base64 is None
    assert parsed.images[0].blob == parsed.blob  # Standalone original is retained once.
    assert any(
        row.assertion.support.status == "model_interpreted"
        for row in embedded.rows
        if row.assertion
    )
    images = ImageSources(queue)
    listed = await images.list(access, kg, receipt.material_id, receipt.version_id)
    assert len(listed) == 1 and listed[0].sha256 == hashlib.sha256(data).hexdigest()
    assert (
        b"".join(
            [
                part
                async for part in images.stream(
                    access, kg, receipt.material_id, receipt.version_id, listed[0].id
                )
            ]
        )
        == data
    )
    with pytest.raises(DomainError):
        await images.list(access, kg, uuid4(), receipt.version_id)
    with pytest.raises(DomainError):
        await images.list(access, uuid4(), receipt.material_id, receipt.version_id)
    graph = GraphService(queue)
    normal, count = await graph.overview(access, kg, SearchFilters())
    assert count == 2 and {e.status for e in normal.edges} == {"model_interpreted"}
    native, count = await graph.overview(access, kg, SearchFilters(support_statuses=("verified",)))
    assert count == 0 and not native.nodes and not native.edges
    scope = await graph.resolver.resolve(access, SearchRequest(kg_ids=(kg,), query="Alpha"), kg)
    evidence = await SealedEvidence(queue).load(scope, "lexical")
    row = next(
        row
        for row in evidence.graph.rows
        if row.chunk and any(s.location.text_origin == "vision" for s in row.chunk.spans)
    )
    candidate = evidence.candidate(row, "lexical", 1.0, 1)
    assert candidate.support_status == "model_interpreted"
    citation = await CitationService(queue).resolve(access, kg, candidate.citation_ids[0])
    assert citation.support_status == "model_interpreted"
    assert citation.images[0].id == listed[0].id
    assert citation.spans[0].location.interpretation_model == "fixture-extraction"

    # Back up and restore the actual source image, preview, vision block ledger
    # and projection; no model call is allowed during retained-artifact recovery.
    from domain_kg.identity.models import Action
    from domain_kg.operations.backup import BackupService
    from domain_kg.operations.jobs import OwnerOperations
    from domain_kg.operations.models import BackupCommand, RestoreCommand
    from domain_kg.operations.restore_activation import RestoreActivation
    from domain_kg.operations.restore_authority import RestoreAuthority
    from domain_kg.operations.restore_control import RestoreControl
    from domain_kg.operations.restore_preparation import RestorePreparation
    from domain_kg.operations.restore_projections import RestoreProjections
    from domain_kg.operations.restore_targets import RestoreTargets
    from domain_kg.retrieval.graph import source_citation

    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.RESTORE)
    operations = OwnerOperations(queue)
    backup = await operations.submit(
        owner,
        BackupCommand(kg_id=kg, expected_published_revision=1, idempotency_key="image-backup"),
    )
    await BackupService(queue).run(await queue.claim("image-backup", kinds=("backup",)))
    profile = next(iter(coordinator.providers.registry.profiles.values()))
    restored = await operations.submit(
        owner,
        RestoreCommand(
            kg_id=kg,
            expected_published_revision=1,
            backup_job_id=backup.job_id,
            placement_id=catalog[3].id,
            provider_profile_id=profile.id,
            name="Image recovery",
            idempotency_key="image-restore",
        ),
    )
    restore_job = await queue.claim("image-restore", kinds=("restore",))
    target = await RestoreTargets(queue).get(owner, kg, restored.job_id)
    coordinator.providers.registry.profiles[profile.id] = profile.model_copy(
        update={
            "allowed_namespaces": (*profile.allowed_namespaces, target.target),
            "allow_restricted": True,
        }
    )
    staged = await RestorePreparation(queue, coordinator.providers).prepare(restore_job)
    try:
        await RestoreAuthority(queue, coordinator.providers).stage(restore_job, staged)
        await RestoreControl(queue, coordinator.providers).stage(restore_job, staged)
        parts = await RestoreProjections(queue, coordinator.providers).stage(restore_job, staged)
        await RestoreActivation(queue, coordinator.providers).activate(restore_job, staged, parts)
        recovered_access = await queue.policy.resolve_access(
            team[2]["owner"], [target.target.kg_id], Action.READ
        )
        recovered = await images.list(
            recovered_access, target.target.kg_id, receipt.material_id, receipt.version_id
        )
        assert recovered[0].sha256 == listed[0].sha256
        assert (
            b"".join(
                [
                    part
                    async for part in images.stream(
                        recovered_access,
                        target.target.kg_id,
                        receipt.material_id,
                        receipt.version_id,
                        recovered[0].id,
                    )
                ]
            )
            == data
        )
        recovered_citation = await CitationService(queue).resolve(
            recovered_access,
            target.target.kg_id,
            source_citation(
                target.target.org_id,
                target.target.kg_id,
                receipt.material_id,
                receipt.version_id,
                citation.chunk_id,
            ),
        )
        assert (
            recovered_citation.support_status == "model_interpreted" and recovered_citation.images
        )
        assert calls == ["fixture-extraction"]
    finally:
        await staged.binding.stores.vectors.client.delete_collection(
            staged.binding.stores.vectors.collection
        )
        await staged.binding.stores.graph.driver.execute_query(
            "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
            org=str(target.target.org_id),
            kg=str(target.target.kg_id),
        )

    from domain_kg.materials.lifecycle import MaterialLifecycle
    from domain_kg.materials.models import RemoveMaterial

    access = await queue.policy.resolve_access(team[2]["editor"], [kg], Action.REMOVE)
    raw = await queue.database.material_access.find_one(
        {"org_id": str(access.org_id), "kg_id": str(kg), "id": str(receipt.material_id)}
    )
    await MaterialLifecycle(materials, queue).remove(
        access,
        RemoveMaterial(
            kg_id=kg,
            material_id=receipt.material_id,
            expected_revision=raw["revision"],
            action="delete",
            idempotency_key="remove-image",
        ),
    )
    with pytest.raises(DomainError):
        await images.list(access, kg, receipt.material_id, receipt.version_id)
