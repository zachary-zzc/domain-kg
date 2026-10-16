"""Metadata-only grouping recovers projections and survives empty source replacement."""
# ruff: noqa: F811

from datetime import timedelta

import pytest
from test_entity_resolution import batch_for
from test_identity_boundaries import pending_identity
from test_identity_curation import finish_identity
from test_publication import make_publishing, prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.queue import now
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.models import ReplaceMaterial
from domain_kg.storage.qdrant import VectorProjection

pytestmark = pytest.mark.storage


async def test_partial_identity_projection_retries_without_models_or_partial_visibility(
    publishing, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    _, alpha, beta, service, command = await pending_identity(publishing)
    receipt = await service.curate_node(editor, command)
    worker = await queue.claim("identity-partial-worker")
    runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers))

    async def offline(*args, **kwargs):
        raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic vector outage", retryable=True)

    async def forbidden(*args, **kwargs):
        pytest.fail("identity recovery must not extract or embed text")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    with monkeypatch.context() as patch:
        patch.setattr(VectorProjection, "write", offline)
        await runner.run_job(worker)
    assert (await queue.get(editor, receipt.job_id)).stage == "queued"
    assert (await GraphService(queue).get_node(editor, command.kg_id, beta)).id == beta
    await queue.database.jobs.update_one(
        {"org_id": str(editor.org_id), "kg_id": str(command.kg_id), "id": str(receipt.job_id)},
        {"$set": {"available_at": now() - timedelta(seconds=1)}},
    )
    successor = await queue.claim("identity-recovered-worker")
    assert successor.id == worker.id and successor.attempt_id != worker.attempt_id
    await runner.run_job(successor)
    assert (await queue.get(editor, receipt.job_id)).stage == "ready"
    assert (await GraphService(queue).get_node(editor, command.kg_id, beta)).id == alpha


async def test_source_identity_keeps_only_opaque_members_after_empty_replacement(
    material_service, team
):
    empty = False

    def extract(chunk, profile):
        batch = batch_for(chunk, profile)
        return batch.model_copy(update={"entities": (), "assertions": ()}) if empty else batch

    fixture = await make_publishing(material_service, team, extraction_factory=extract)
    _, editor, queue, coordinator, _, _ = fixture
    source, alpha, beta, service, command = await pending_identity(fixture)
    receipt = await service.curate_node(editor, command)
    await finish_identity(queue, coordinator, editor, receipt)
    empty = True
    _, replacement, checkpoint = await prepare(
        fixture,
        command=ReplaceMaterial(
            kg_id=command.kg_id,
            material_id=source.material_id,
            expected_active_version=source.version_id,
            title="No extracted entities",
            inline_text="This version contains no recognized entities.",
            idempotency_key="identity-empty-source",
        ),
    )
    await coordinator.publish(editor, await ready(fixture, replacement, checkpoint))
    node = await GraphService(queue).get_node(editor, command.kg_id, beta)
    assert node.id == alpha and node.name == str(alpha)
    assert set(node.identity_members) == {alpha, beta}
    assert node.observation_count == node.support_count == 0
    assert node.aliases == node.descriptions == node.entity_types == ()
