"""A real backup restores and repairs on independent graph/vector storage under 1.5 GB."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from physical_profiles import budget_and_volumes, compose, start
from qdrant_client import models
from test_curation import alpha_id, publish_curation
from test_entity_resolution import TEXT
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.catalog.backends import PrivateConnection
from domain_kg.catalog.models import RegisterBackend, StorageProfile
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.operations.backup import BackupService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import BackupCommand, RestoreCommand
from domain_kg.operations.projections import ProjectionManagement
from domain_kg.operations.restore import RestoreService
from domain_kg.operations.restore_targets import RestoreTargets
from domain_kg.retrieval.models import SearchRequest
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.revisions.publication import accepted_document, projection_rows, verify_accepted
from domain_kg.revisions.service import RevisionService

pytestmark = [pytest.mark.storage, pytest.mark.physical]


async def test_backup_restore_and_repair_on_second_physical_profile(
    publishing,
    team,
    catalog,
    settings,
    monkeypatch,
):
    materials, editor, queue, coordinator, _, _ = publishing
    _, registry, router, primary_profile = catalog
    policy, principals, org = team[1], team[2], team[4]
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    graph = GraphService(queue)
    alpha = await alpha_id(queue, editor, source.kg_id)
    revisions = RevisionService(queue, coordinator.providers)
    change = await revisions.update_node(
        editor,
        NodeUpdate(
            kg_id=source.kg_id,
            entity_id=alpha,
            expected_revision=0,
            source_ids=(source.material_id,),
            changes=NodeChanges(name="Physical Reviewed Alpha"),
            reason="Preserve this reviewed label",
            idempotency_key="physical-human-label",
        ),
    )
    await publish_curation(queue, coordinator, editor, change)

    async def access(kg=source.kg_id, action=Action.CONFIGURE, role="owner"):
        return await policy.resolve_access(principals[role], [kg] if kg else [], action, org_id=org)

    async def forbidden(*args, **kwargs):
        pytest.fail("physical restore and stored-vector repair must not call a provider")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
    owner = await access(action=Action.BACKUP)
    backup = await OwnerOperations(queue).submit(
        owner,
        BackupCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            idempotency_key="physical-real-backup",
        ),
    )
    await BackupService(queue).run(await queue.claim("physical-backup", kinds=("backup",)))
    primary_volumes = await budget_and_volumes()
    secondary_database = f"{policy.database.name}_phys"
    target = None
    secondary_profile = None
    try:
        await router.close()
        await compose(["stop", "--timeout", "20", "neo4j", "qdrant"])
        await start(secondary=True)
        secondary_volumes = await budget_and_volumes()
        assert all(secondary_volumes[name] != primary_volumes[name] for name in primary_volumes)
        connections = (
            PrivateConnection(kind="mongo", database=secondary_database),
            PrivateConnection(
                kind="neo4j",
                endpoint="bolt://127.0.0.1:28687",
                username=settings.neo4j_user,
                credential=settings.neo4j_password,
            ),
            PrivateConnection(
                kind="qdrant", endpoint="http://127.0.0.1:27333", credential=settings.qdrant_api_key
            ),
        )
        identifiers = {}
        for connection in connections:
            reference = uuid4()
            registry._approved[reference] = connection
            registered = await registry.register(
                await access(None, Action.REGISTER_BACKEND),
                RegisterBackend(kind=connection.kind, secret_ref=reference),
                idempotency_key=str(reference),
            )
            identifiers[connection.kind] = registered.backend_id
        secondary_profile = StorageProfile(
            id=uuid4(),
            revision=0,
            mongo_id=identifiers["mongo"],
            neo4j_id=identifiers["neo4j"],
            qdrant_id=identifiers["qdrant"],
            object_store_id=primary_profile.object_store_id,
            allowed_org_ids={org},
        )
        await registry.register_profile(
            await access(None, Action.REGISTER_BACKEND),
            secondary_profile,
            idempotency_key="physical-recovery-placement",
        )
        profile = next(iter(coordinator.providers.registry.profiles.values()))
        recovery = await OwnerOperations(queue).submit(
            await access(action=Action.RESTORE),
            RestoreCommand(
                kg_id=source.kg_id,
                expected_published_revision=2,
                backup_job_id=backup.job_id,
                placement_id=secondary_profile.id,
                provider_profile_id=profile.id,
                name="Physical recovered KG",
                idempotency_key="physical-recovery",
            ),
        )
        target = await RestoreTargets(queue).get(
            await access(action=Action.RESTORE), source.kg_id, recovery.job_id
        )
        coordinator.providers.registry.profiles[profile.id] = profile.model_copy(
            update={
                "allowed_namespaces": (*profile.allowed_namespaces, target.target),
                "allow_restricted": True,
            }
        )
        restore = await queue.claim("physical-restore", kinds=("restore",))
        await JobRunner(
            queue,
            configured_pipeline(queue, coordinator.providers),
            operations=RestoreService(queue, coordinator.providers),
        ).run_job(restore)
        assert await queue.accepted(restore), (
            await queue.get(await access(action=Action.RESTORE), restore.id)
        ).warnings
        target_owner = await access(target.target.kg_id)
        stores = await router.for_kg(target_owner, target.target.kg_id)
        assert stores.authority._database.name == secondary_database
        source_scope = await graph.resolver.resolve(
            target_owner,
            SearchRequest(kg_ids=(target.target.kg_id,), query="Alpha"),
            target.target.kg_id,
        )
        observations = (await graph.repository.open(source_scope)).observations
        target_alpha = next(row.entity.id for row in observations if row.name == "Alpha")
        node = await graph.get_node(target_owner, target.target.kg_id, target_alpha)
        assert (
            node.name == "Physical Reviewed Alpha"
            and node.curations[0].provenance.reason == "Preserve this reviewed label"
        )
        for role in ("reader", "editor"):
            with pytest.raises(DomainError):
                await access(target.target.kg_id, Action.READ, role)
        with pytest.raises(DomainError) as unavailable:
            await graph.get_node(await access(action=Action.READ), source.kg_id, alpha)
        assert unavailable.value.code in (
            ErrorCode.BACKEND_UNAVAILABLE,
            ErrorCode.PROJECTION_INCONSISTENT,
        )
        view = await PublicationJournal(queue).load_metadata(target_owner, target.target.kg_id)
        restored_source = view.sources[0]
        assert (
            b"".join(
                [
                    part
                    async for part in materials.original(
                        target_owner,
                        target.target.kg_id,
                        restored_source.material_id,
                        restored_source.version_id,
                    )
                ]
            )
            == TEXT.encode()
        )
        binding = next(row for row in view.projections if row.source)
        document, batch, _ = await accepted_document(queue, target_owner, binding)
        point = projection_rows(document, batch)[0]["projection_id"]
        await stores.vectors.client.delete(
            stores.vectors.collection,
            points_selector=models.PointIdsList(points=[point]),
            wait=True,
        )
        with pytest.raises(DomainError):
            await verify_accepted(queue, target_owner, binding)
        repair = await ProjectionManagement(queue).repair(
            target_owner,
            target.target.kg_id,
            restored_source.material_id,
            idempotency_key="physical-source-repair",
        )
        repair_job = await queue.claim("physical-repair", kinds=("repair",))
        assert repair_job.id == repair.job_id
        await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(
            repair_job
        )
        assert await queue.accepted(repair_job)
        assert (
            await ProjectionManagement(queue).status(target_owner, target.target.kg_id)
        ).state == "ready"
        response = await RetrievalService(queue, coordinator.providers).search(
            target_owner,
            SearchRequest(
                kg_ids=(target.target.kg_id,), query="Physical Reviewed Alpha", mode="lexical"
            ),
        )
        assert response.results and any(row.kind == "manual" for row in response.results)
        metadata = await revisions.update_node(
            target_owner,
            NodeUpdate(
                kg_id=target.target.kg_id,
                entity_id=target_alpha,
                expected_revision=1,
                source_ids=(restored_source.material_id,),
                changes=NodeChanges(tags=("physical-restored",)),
                reason="Target owner follows up after repair",
                idempotency_key="physical-followup",
            ),
        )
        await publish_curation(queue, coordinator, target_owner, metadata)
        assert (
            "physical-restored"
            in (await graph.get_node(target_owner, target.target.kg_id, target_alpha)).tags
        )
    finally:
        try:
            if target is not None and secondary_profile is not None:
                # Only this drill's fresh target can be removed from the secondary volumes.
                stores = await router._bind_namespace(
                    target.target, secondary_profile, target.generation
                )
                if await stores.vectors.client.collection_exists(stores.vectors.collection):
                    await stores.vectors.client.delete_collection(stores.vectors.collection)
                await stores.graph.driver.execute_query(
                    "MATCH (n {org_id:$org,kg_id:$kg}) DETACH DELETE n",
                    org=str(org),
                    kg=str(target.target.kg_id),
                )
        finally:
            await router.close()
            await compose(["stop", "--timeout", "20", "neo4j", "qdrant"], secondary=True)
            await start()
            assert await budget_and_volumes() == primary_volumes
            await policy.database.client.drop_database(secondary_database)
        current = await access(action=Action.READ)
        assert (
            await graph.get_node(current, source.kg_id, alpha)
        ).name == "Physical Reviewed Alpha"
