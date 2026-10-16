"""Human grouping publishes immutable metadata while original evidence stays intact."""
# ruff: noqa: F811

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError
from domain_kg.graph.identity_models import IdentityGroup
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.retrieval.models import SearchRequest, SubgraphRequest
from domain_kg.revisions.history_models import HistoryRequest
from domain_kg.revisions.models import CurationCommand, RollbackRevision
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def finish_identity(queue, coordinator, access, receipt):
    job = await queue.claim("identity-worker", kinds=("curate",))
    assert job and job.id == receipt.job_id
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(job)
    assert (await queue.get(access, job.id)).stage == "ready"


async def test_merge_split_and_owner_retraction_keep_original_evidence(
    publishing, team, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    graph = GraphService(queue)
    kg = job.kg_id
    scope = await graph.resolver.resolve(editor, SearchRequest(kg_ids=(kg,), query="Alpha"), kg)
    raw = await graph.repository.open(scope)
    alpha = next(item.entity.id for item in raw.observations if item.name == "Alpha")
    beta = next(item.entity.id for item in raw.observations if item.name == "Beta")
    original = raw.supports[0]
    encoded = original.model_dump_json()
    revisions = RevisionService(queue, coordinator.providers)
    command = CurationCommand(
        kg_id=kg,
        entity_id=alpha,
        action="merge",
        expected_revision=0,
        member_ids=(alpha, beta),
        member_revisions=((beta, 0),),
        source_ids=(source.material_id,),
        reason="Explicitly reviewed two source identities",
        idempotency_key="identity-merge",
    )
    before = await queue.database.jobs.count_documents(
        {"org_id": str(editor.org_id), "kg_id": str(kg)}
    )
    preview = await revisions.curate_node(editor, command.model_copy(update={"dry_run": True}))
    assert preview.dry_run and set(preview.member_ids) == {alpha, beta}
    assert (
        await queue.database.jobs.count_documents({"org_id": str(editor.org_id), "kg_id": str(kg)})
        == before
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("identity-only publication must not extract or embed text")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    pending = await revisions.curate_node(editor, command)
    assert pending.state == "pending" and pending.job_id
    assert await revisions.curate_node(editor, command) == pending
    assert (await graph.get_node(editor, kg, beta)).id == beta
    await finish_identity(queue, coordinator, editor, pending)
    node = await graph.get_node(editor, kg, alpha)
    assert node.observation_count == 2 and set(node.identity_members) == {alpha, beta}
    alias = await graph.get_node(editor, kg, beta)
    assert alias.id == alpha and alias.requested_id == beta
    merged = await graph.subgraph(editor, SubgraphRequest(kg_id=kg, seed_ids=(beta,)))
    assert len(merged.nodes) == 1 and merged.nodes[0].id == alpha
    assert (
        len(merged.edges) == 1 and merged.edges[0].subject_id == merged.edges[0].target_id == alpha
    )
    assert merged.edges[0].original_subject_id == original.claim.assertion.subject.id
    assert merged.edges[0].original_target_id == original.claim.assertion.target.id
    assert original.model_dump_json() == encoded
    split = await revisions.curate_node(
        editor,
        CurationCommand(
            kg_id=kg,
            entity_id=alpha,
            action="split",
            expected_revision=1,
            member_revisions=((beta, 1),),
            source_ids=(source.material_id,),
            partitions=(
                IdentityGroup(canonical_id=alpha, member_ids=(alpha,)),
                IdentityGroup(canonical_id=beta, member_ids=(beta,)),
            ),
            reason="Keep the two original identities distinct",
            idempotency_key="identity-split",
        ),
    )
    await finish_identity(queue, coordinator, editor, split)
    assert (await graph.get_node(editor, kg, beta)).id == beta
    separate = await graph.subgraph(editor, SubgraphRequest(kg_id=kg, seed_ids=(alpha,)))
    assert len(separate.nodes) == 2
    assert separate.edges[0].assertion_id == original.claim.assertion.id
    assert separate.edges[0].spans == original.claim.support.spans
    owner = await queue.policy.resolve_access(team[2]["owner"], [kg], Action.CURATE)
    rollback = RollbackRevision(
        kg_id=kg,
        revision_id=split.revision_id,
        expected_current_revision=2,
        reason="Withdraw the split, preserving the prior merge",
        idempotency_key="identity-undo-split",
    )
    with pytest.raises(DomainError):
        await revisions.rollback(editor, rollback)
    compensation = await revisions.rollback(owner, rollback)
    assert await revisions.rollback(owner, rollback) == compensation
    await finish_identity(queue, coordinator, owner, compensation)
    assert (await graph.get_node(editor, kg, beta)).id == alpha
    history = await revisions.get_revisions(editor, HistoryRequest(kg_id=kg, target_id=beta))
    assert [item.kind for item in history.items] == ["identity", "identity", "identity_control"]
    assert history.items[-1].rollback_of == split.revision_id
    from uuid import uuid4

    await queue.database.curation_revisions.update_one(
        {"org_id": str(editor.org_id), "kg_id": str(kg), "id": str(compensation.revision_id)},
        {"$set": {"rollback_of": str(uuid4())}},
    )
    with pytest.raises(DomainError):
        await graph.get_node(editor, kg, beta)
