"""Identity metadata shares member counters and current publication authority."""
# ruff: noqa: F811

import pytest
from test_curation_races import staged_command
from test_identity_curation import finish_identity
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import CurationCommand, NodeChanges, NodeUpdate
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def pending_identity(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=(job.kg_id,), query="Alpha"), job.kg_id
    )
    data = await graph.repository.open(scope)
    alpha = next(item.entity.id for item in data.observations if item.name == "Alpha")
    beta = next(item.entity.id for item in data.observations if item.name == "Beta")
    service = RevisionService(queue, coordinator.providers)
    command = CurationCommand(
        kg_id=job.kg_id,
        entity_id=alpha,
        action="merge",
        member_ids=(alpha, beta),
        expected_revision=0,
        member_revisions=((beta, 0),),
        source_ids=(source.material_id,),
        reason="Boundary fixture grouping",
        idempotency_key="boundary-merge",
    )
    return source, alpha, beta, service, command


@pytest.mark.parametrize("merge_first", [True, False])
async def test_merge_and_member_field_edit_cannot_publish_against_the_same_revision(
    publishing, merge_first, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    source, alpha, beta, service, command = await pending_identity(publishing)
    merged = await service.curate_node(editor, command)
    edited = await service.update_node(
        editor,
        NodeUpdate(
            kg_id=command.kg_id,
            entity_id=beta,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(tags=("reviewed",)),
            reason="Edit the secondary member",
            idempotency_key="boundary-member-edit",
        ),
    )
    first, second = (
        await queue.claim("identity-conflict-one"),
        await queue.claim("identity-conflict-two"),
    )
    assert first.id == merged.job_id and second.id == edited.job_id

    async def forbidden(*args, **kwargs):
        pytest.fail("metadata conflict checks must not call a model")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    runner = JobRunner(queue, configured_pipeline(queue, coordinator.providers))
    winner, loser = (first, second) if merge_first else (second, first)
    await runner.run_job(winner)
    await runner.run_job(loser)
    assert (await queue.get(editor, winner.id)).stage == "ready"
    status = await queue.get(editor, loser.id)
    assert status.stage == "failed" and "VERSION_CONFLICT" in status.warnings
    node = await GraphService(queue).get_node(editor, command.kg_id, beta)
    assert node.id == (alpha if merge_first else beta)


async def test_current_material_revocation_blocks_prepared_identity_publication(publishing, team):
    _, editor, queue, coordinator, _, _ = publishing
    source, _, beta, service, command = await pending_identity(publishing)
    receipt = await service.curate_node(editor, command)
    leased = await queue.claim("identity-revoked-worker")
    publication = await staged_command(queue, coordinator, editor, leased)
    owner = await queue.policy.resolve_access(
        team[2]["owner"], [command.kg_id], Action.MANAGE_GRANTS
    )
    await queue.policy.set_material_acl(
        owner,
        command.kg_id,
        source.material_id,
        "restricted",
        member_ids=(team[3]["owner"].id,),
        group_ids=(),
        idempotency_key="identity-revoke",
    )
    with pytest.raises(DomainError):
        await coordinator.publish(editor, publication)
    with pytest.raises(DomainError):
        await queue.get(editor, receipt.job_id)
    owner = await queue.policy.resolve_access(team[2]["owner"], [command.kg_id], Action.SEARCH)
    assert (await GraphService(queue).get_node(owner, command.kg_id, beta)).id == beta


async def test_public_identity_requires_owner_and_applies_inside_source_filter(publishing, team):
    _, editor, queue, coordinator, _, _ = publishing
    source, alpha, beta, service, command = await pending_identity(publishing)
    public = command.model_copy(update={"curation_scope": "kg_public", "source_ids": ()})
    with pytest.raises(DomainError):
        await service.curate_node(editor, public)
    owner = await queue.policy.resolve_access(team[2]["owner"], [command.kg_id], Action.CURATE)
    receipt = await service.curate_node(owner, public)
    await finish_identity(queue, coordinator, owner, receipt)
    node = await GraphService(queue).get_node(
        editor, command.kg_id, beta, SearchFilters(material_ids=(source.material_id,))
    )
    assert node.id == alpha and node.identity_mappings
