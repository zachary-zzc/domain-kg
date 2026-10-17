"""Export is a current scoped read, never an unrestricted snapshot dump."""
# ruff: noqa: F811

import json

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import RemoveMaterial
from domain_kg.operations.export import ExportService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import ExportCommand
from domain_kg.retrieval.models import SearchFilters

pytestmark = pytest.mark.storage


async def test_export_filters_every_field_and_rechecks_withdrawal_before_download(
    publishing, team, monkeypatch
):
    materials, editor, queue, coordinator, _, _ = publishing
    first, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    second, job, checkpoint = await prepare(publishing, key="other-export-source")
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [first.kg_id], Action.CONFIGURE)

    async def forbidden(*args, **kwargs):
        pytest.fail("exports must not call an extraction, embedding or query model")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    command = ExportCommand(
        kg_id=first.kg_id,
        expected_published_revision=2,
        filters=SearchFilters(material_ids=(first.material_id,)),
        idempotency_key="scoped-export",
    )
    receipt = await OwnerOperations(queue).submit(owner, command)
    job = await queue.claim("export-worker", kinds=("export",))
    service = ExportService(queue)
    await JobRunner(
        queue, configured_pipeline(queue, coordinator.providers), operations=service
    ).run_job(job)
    status = await queue.get(owner, receipt.job_id)
    assert status.stage == "ready", status.warnings
    assert await queue.accepted(job)
    await queue.finish(job)
    manifest = await service.manifest(owner, first.kg_id, receipt.job_id)
    assert manifest.node_count == 2 and manifest.edge_count == 1
    body = b"".join([part async for part in service.download(owner, first.kg_id, receipt.job_id)])
    records = [json.loads(line) for line in body.splitlines()]
    assert {record["kind"] for record in records} == {"node", "edge"}
    assert str(first.material_id).encode() in body
    assert str(second.material_id).encode() not in body
    reader = await queue.policy.resolve_access(team[2]["reader"], [first.kg_id], Action.READ)
    with pytest.raises(DomainError):
        await service.manifest(reader, first.kg_id, receipt.job_id)
    await MaterialLifecycle(materials, queue).remove(
        editor,
        RemoveMaterial(
            kg_id=first.kg_id,
            material_id=first.material_id,
            expected_revision=1,
            action="delete",
            idempotency_key="withdraw-export-source",
        ),
    )
    with pytest.raises(DomainError):
        await anext(service.download(owner, first.kg_id, receipt.job_id))


async def test_export_cannot_seal_after_owner_revocation(publishing, team, monkeypatch):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    await OwnerOperations(queue).submit(
        owner,
        ExportCommand(
            kg_id=source.kg_id, expected_published_revision=1, idempotency_key="revoke-export-owner"
        ),
    )
    job = await queue.claim("export-revocation", kinds=("export",))
    service = ExportService(queue)
    seal = service._seal

    async def revoke(*args, **kwargs):
        await queue.database.members.update_one(
            {"org_id": str(owner.org_id), "id": str(team[3]["owner"].id)},
            {"$set": {"suspended": True}},
        )
        await seal(*args, **kwargs)

    monkeypatch.setattr(service, "_seal", revoke)
    with pytest.raises(DomainError):
        await service.run(job)
    assert not await queue.database.export_results.find_one({"id": str(job.target_id)})


async def test_unverified_imports_require_explicit_export_opt_in(material_service, team):
    from test_legacy_import import upload_csv
    from test_publication import make_publishing

    from domain_kg.imports.models import ImportSource
    from domain_kg.imports.service import Importer

    def no_extraction(*args):
        pytest.fail("an imported claim cannot become verified model extraction")

    fixture = await make_publishing(
        material_service, team, restricted=True, extraction_factory=no_extraction
    )
    _, editor, queue, coordinator, _, _ = fixture
    materials, editor, uploaded = await upload_csv(material_service, team)
    importer = Importer(materials)
    await importer.initialize()
    plan = await importer.plan(
        editor,
        ImportSource(
            kg_id=uploaded.kg_id,
            upload_id=uploaded.upload_id,
            format="csv",
            idempotency_key="export-import-plan",
        ),
    )
    await importer._admit(editor, plan)
    imported = await queue.claim("import-for-export", kinds=("import",))
    pipeline = configured_pipeline(queue, coordinator.providers)
    await JobRunner(queue, pipeline).run_job(imported)
    status = await queue.get(editor, imported.id)
    assert status.stage == "ready_with_warnings", status.warnings
    owner = await queue.policy.resolve_access(team[2]["owner"], [uploaded.kg_id], Action.CONFIGURE)
    service = ExportService(queue)
    for explicit in (False, True):
        filters = (
            SearchFilters(support_statuses=("imported_unverified",))
            if explicit
            else SearchFilters()
        )
        receipt = await OwnerOperations(queue).submit(
            owner,
            ExportCommand(
                kg_id=uploaded.kg_id,
                expected_published_revision=1,
                filters=filters,
                idempotency_key=f"export-import-{explicit}",
            ),
        )
        job = await queue.claim("export-import", kinds=("export",))
        await JobRunner(queue, pipeline, operations=service).run_job(job)
        manifest = await service.manifest(owner, uploaded.kg_id, receipt.job_id)
        body = b"".join(
            [chunk async for chunk in service.download(owner, uploaded.kg_id, receipt.job_id)]
        )
        if explicit:
            assert manifest.edge_count > 0 and b"imported_unverified" in body
        else:
            assert manifest.node_count == manifest.edge_count == 0 and body.strip() == b""
