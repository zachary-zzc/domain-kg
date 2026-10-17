"""Imported split history remains reversible and supports a new owner's next edit."""
# ruff: noqa: F811

import pytest
from test_curation import alpha_id
from test_identity_curation import finish_identity
from test_publication import publishing  # noqa: F401
from test_restore_preparation import setup_restore

from domain_kg.errors import DomainError
from domain_kg.graph.identity_models import IdentityGroup
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.operations.restore import RestoreService
from domain_kg.retrieval.models import SearchRequest, SubgraphRequest
from domain_kg.revisions.history_models import HistoryRequest
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import CurationCommand, RollbackRevision
from domain_kg.revisions.service import RevisionService

pytestmark = pytest.mark.storage


async def test_restore_split_preserves_evidence_and_allows_new_owner_followup(
    publishing,
    team,
    catalog,
    monkeypatch,
):
    _, editor, queue, coordinator, _, _ = publishing
    original_revisions = []
    original_entities = []
    graph = GraphService(queue)
    revisions = RevisionService(queue, coordinator.providers)

    async def original_history(source):
        async def forbidden(*args, **kwargs):
            pytest.fail("identity metadata and unchanged restore text must not call a model")

        monkeypatch.setattr(coordinator.providers, "extract", forbidden)
        monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
        scope = await graph.resolver.resolve(
            editor, SearchRequest(kg_ids=(source.kg_id,), query="Alpha"), source.kg_id
        )
        observations = (await graph.repository.open(scope)).observations
        alpha = await alpha_id(queue, editor, source.kg_id)
        beta = next(row.entity.id for row in observations if row.name == "Beta")
        original_entities.extend((alpha, beta))
        merge = await revisions.curate_node(
            editor,
            CurationCommand(
                kg_id=source.kg_id,
                entity_id=alpha,
                action="merge",
                expected_revision=0,
                member_ids=(alpha, beta),
                member_revisions=((beta, 0),),
                source_ids=(source.material_id,),
                reason="Original author reviewed merge",
                idempotency_key="restore-split-merge",
            ),
        )
        await finish_identity(queue, coordinator, editor, merge)
        split = await revisions.curate_node(
            editor,
            CurationCommand(
                kg_id=source.kg_id,
                entity_id=alpha,
                action="split",
                expected_revision=1,
                member_revisions=((beta, 1),),
                source_ids=(source.material_id,),
                partitions=(
                    IdentityGroup(canonical_id=alpha, member_ids=(alpha,)),
                    IdentityGroup(canonical_id=beta, member_ids=(beta,)),
                ),
                reason="Original author kept identities distinct",
                idempotency_key="restore-split-original",
            ),
        )
        await finish_identity(queue, coordinator, editor, split)
        original_revisions.extend((merge.revision_id, split.revision_id))

    source, _, restore, target, _ = await setup_restore(
        publishing, team, catalog, before_backup=original_history
    )
    await RestoreService(queue, coordinator.providers).run(restore)
    assert await queue.accepted(restore)
    await RestoreService(queue, coordinator.providers).run(restore)
    owner = await queue.policy.resolve_access(
        team[2]["owner"], [target.target.kg_id], Action.CURATE
    )
    scope = await graph.resolver.resolve(
        owner, SearchRequest(kg_ids=(target.target.kg_id,), query="Alpha"), target.target.kg_id
    )
    data = await graph.repository.open(scope)
    alpha = next(row.entity.id for row in data.observations if row.name == "Alpha")
    beta = next(row.entity.id for row in data.observations if row.name == "Beta")
    assert (await graph.get_node(owner, target.target.kg_id, beta)).id == beta
    separate = await graph.subgraph(
        owner, SubgraphRequest(kg_id=target.target.kg_id, seed_ids=(alpha,))
    )
    assert len(separate.nodes) == 2 and separate.edges and separate.edges[0].spans
    for role in ("reader", "editor"):
        with pytest.raises(DomainError):
            await queue.policy.resolve_access(team[2][role], [target.target.kg_id], Action.READ)
    history = await revisions.get_revisions(
        owner, HistoryRequest(kg_id=target.target.kg_id, target_id=beta)
    )
    assert [row.operation for row in history.items] == ["merge", "split"]
    assert [row.revision_id for row in history.items] == original_revisions
    assert history.items[1].provenance.reason == "Original author kept identities distinct"
    old_author = history.items[1].provenance.actor
    assert old_author == f"member:{team[3]['editor'].id}"
    rollback = await revisions.rollback(
        owner,
        RollbackRevision(
            kg_id=target.target.kg_id,
            revision_id=original_revisions[1],
            expected_current_revision=2,
            reason="New owner retracts imported split",
            idempotency_key="restore-split-retract",
        ),
    )
    await finish_identity(queue, coordinator, owner, rollback)
    assert (await graph.get_node(owner, target.target.kg_id, beta)).id == alpha
    current = await PublicationJournal(queue).load_metadata(owner, target.target.kg_id)
    followup = await revisions.curate_node(
        owner,
        CurationCommand(
            kg_id=target.target.kg_id,
            entity_id=alpha,
            action="split",
            expected_revision=3,
            member_revisions=((beta, 3),),
            source_ids=(current.sources[0].material_id,),
            partitions=(
                IdentityGroup(canonical_id=alpha, member_ids=(alpha,)),
                IdentityGroup(canonical_id=beta, member_ids=(beta,)),
            ),
            reason="New owner independently splits again",
            idempotency_key="restore-split-followup",
        ),
    )
    await finish_identity(queue, coordinator, owner, followup)
    assert (await graph.get_node(owner, target.target.kg_id, beta)).id == beta
    final = await revisions.get_revisions(
        owner, HistoryRequest(kg_id=target.target.kg_id, target_id=beta)
    )
    assert len(final.items) == 4 and final.items[2].rollback_of == original_revisions[1]
    assert final.items[0].provenance.actor == old_author
    assert final.items[-1].provenance.actor == f"member:{team[3]['owner'].id}"
    assert final.items[-1].provenance.reason == "New owner independently splits again"
    source_editor = await queue.policy.resolve_access(
        team[2]["editor"], [source.kg_id], Action.READ
    )
    assert (
        await graph.get_node(source_editor, source.kg_id, original_entities[1])
    ).id == original_entities[1]
    target_kg = await queue.database.kgs.find_one(
        {"org_id": str(target.target.org_id), "id": str(target.target.kg_id)}
    )
    assert target_kg["published_revision"] == 3
