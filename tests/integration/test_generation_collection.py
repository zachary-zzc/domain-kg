"""Retired derived indexes can be collected without erasing durable human history."""
# ruff: noqa: F811

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from qdrant_client import models
from test_maintenance_admission import accepted_human_edit
from test_publication import publishing  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import now
from domain_kg.operations.generation import GenerationService
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import GenerationCommand, RetentionCommand
from domain_kg.operations.retention import RetentionService
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.publication import verify_accepted
from domain_kg.revisions.writes import ProjectionWrites
from domain_kg.storage.qdrant import VectorProjection

pytestmark = pytest.mark.storage


async def test_collect_retired_generation_preserves_current_view_and_history(publishing, team):
    source, alpha, curation = await accepted_human_edit(publishing)
    _, editor, queue, _, _, _ = publishing
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    operations = OwnerOperations(queue)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    before = await queue.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
    old = before["generation"]
    history = await queue.database.curation_revisions.find_one(
        ns | {"id": str(curation.revision_id)}
    )
    await operations.submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            idempotency_key="replace-old-generation",
        ),
    )
    rebuild = await queue.claim("rebuild", kinds=("generation",))
    await GenerationService(queue).run(rebuild)
    await queue.database.projection_generations.update_one(
        ns | {"id": old},
        {
            "$set": {
                "retired_at": now() - timedelta(days=3),
                "last_settled_at": now() - timedelta(days=3),
            }
        },
    )
    receipt = await operations.submit(
        owner,
        RetentionCommand(
            kg_id=source.kg_id,
            expected_published_revision=3,
            resources="generations",
            idempotency_key="collect-old-generation",
        ),
    )
    plan = await queue.database.retention_plans.find_one(ns | {"job.id": str(receipt.job_id)})
    assert len(plan["targets"]) == 1
    assert plan["targets"][0]["generation"] == old
    cleanup = await queue.claim("generation-collector", kinds=("retention",))
    await RetentionService(queue).run(cleanup)
    assert await queue.accepted(cleanup)
    await RetentionService(queue).run(cleanup)
    stores = await queue.router.for_kg(owner, source.kg_id)
    rows, _, _ = await stores.graph.driver.execute_query(
        "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,generation:$gen}) RETURN count(n) AS n",
        org=ns["org_id"],
        kg=ns["kg_id"],
        gen=old,
    )
    assert rows[0]["n"] == 0
    assert (
        await GraphService(queue).get_node(editor, source.kg_id, alpha)
    ).name == "Reviewed Alpha"
    for binding in (await PublicationJournal(queue).load_metadata(owner, source.kg_id)).projections:
        await verify_accepted(queue, owner, binding)
    assert (
        await queue.database.curation_revisions.find_one(ns | {"id": str(curation.revision_id)})
        == history
    )
    assert (
        await queue.database.projection_attempts.count_documents(ns | {"batch.generation": old})
        == 2
    )


async def test_unknown_pending_pinned_and_live_generations_stay_protected(publishing, team):
    source, _, _ = await accepted_human_edit(publishing)
    queue = publishing[2]
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    old = (await queue.database.kgs.find_one({"id": ns["kg_id"]}))["generation"]
    operations = OwnerOperations(queue)
    await operations.submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            idempotency_key="guarded-rebuild",
        ),
    )
    rebuild = await queue.claim("rebuild", kinds=("generation",))
    await GenerationService(queue).run(rebuild)
    key = ns | {"id": old}
    past = now() - timedelta(days=3)
    await queue.database.projection_generations.update_many(
        ns, {"$set": {"retired_at": past, "last_settled_at": past}}
    )

    async def plan(label):
        receipt = await operations.submit(
            owner,
            RetentionCommand(
                kg_id=source.kg_id,
                expected_published_revision=3,
                resources="generations",
                idempotency_key=label,
            ),
        )
        return await queue.database.retention_plans.find_one(ns | {"job.id": str(receipt.job_id)})

    for field in ("pending_count", "uncertain_count"):
        await queue.database.projection_generations.update_one(key, {"$set": {field: 1}})
        assert (await plan("refuse-" + field))["targets"] == []
        await queue.database.projection_generations.update_one(key, {"$set": {field: 0}})
    attempt = await queue.database.projection_attempts.find_one(ns | {"batch.generation": old})
    await queue.database.projection_attempts.update_one(
        {"_id": attempt["_id"]}, {"$unset": {"write_tracking_version": ""}}
    )
    assert (await plan("refuse-untracked"))["targets"] == []
    await queue.database.projection_attempts.update_one(
        {"_id": attempt["_id"]}, {"$set": {"write_tracking_version": 1}}
    )
    write = await queue.database.projection_writes.find_one(ns | {"generation": old})
    await queue.database.projection_writes.update_one(
        {"_id": write["_id"]}, {"$set": {"state": "pending"}}
    )
    assert (await plan("refuse-unsettled-inventory"))["targets"] == []
    await queue.database.projection_writes.update_one(
        {"_id": write["_id"]}, {"$set": {"state": "settled"}}
    )
    pin = ns | {
        "id": str(uuid4()),
        "snapshot": {"generation": old},
        "expires_at": now() + timedelta(hours=1),
    }
    await queue.database.read_pins.insert_one(pin)
    assert (await plan("refuse-pinned"))["targets"] == []
    await queue.database.read_pins.delete_one({"_id": pin["_id"]})
    allowed = await plan("allow-retired")
    assert [row["generation"] for row in allowed["targets"]] == [old]
    assert (await plan("avoid-second-collector"))["targets"] == []


async def test_abandoned_generation_collection_drains_writes_and_blocks_retry(
    publishing,
    team,
    monkeypatch,
):
    source, _, _ = await accepted_human_edit(publishing)
    queue = publishing[2]
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    operations = OwnerOperations(queue)
    await operations.submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            idempotency_key="abandoned-rebuild",
        ),
    )
    rebuild = await queue.claim("abandoned", kinds=("generation",))
    import domain_kg.operations.generation as module
    from domain_kg.operations.generation_capture import GenerationCapture
    from domain_kg.operations.generation_collection import GenerationCollection

    verify = module.verify_document

    async def fail_verification(*args):
        raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic read-back failure.")

    monkeypatch.setattr(module, "verify_document", fail_verification)
    with pytest.raises(DomainError):
        await GenerationService(queue).run(rebuild)
    monkeypatch.setattr(module, "verify_document", verify)
    await queue.fail(
        rebuild, DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic read-back failure.")
    )
    generation = (
        await queue.database.generation_targets.find_one(ns | {"id": str(rebuild.target_id)})
    )["generation"]
    await queue.database.projection_generations.update_one(
        ns | {"id": generation}, {"$set": {"last_settled_at": now() - timedelta(days=3)}}
    )
    receipt = await operations.submit(
        owner,
        RetentionCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            resources="generations",
            idempotency_key="collect-abandoned",
        ),
    )
    target = (await queue.database.retention_plans.find_one(ns | {"job.id": str(receipt.job_id)}))[
        "targets"
    ]
    assert len(target) == 1 and target[0]["generation"] == generation
    await queue.retry(owner, rebuild.id, idempotency_key="retry-before-collected")
    retry = await queue.claim("retry", kinds=("generation",))
    await GenerationCapture(queue).start(retry)
    with pytest.raises(DomainError):
        await ProjectionWrites(queue).begin(retry, UUID(generation), None)
    await GenerationCapture(queue).release(retry)
    await queue.fail(retry, DomainError(ErrorCode.VERSION_CONFLICT, "Collection owns generation."))
    cleanup = await queue.claim("cleanup", kinds=("retention",))
    service = RetentionService(queue)
    erase = GenerationCollection.erase

    async def revoke_after_erasure(self, *args):
        await erase(self, *args)
        await queue.database.members.update_one(
            {"id": str(team[3]["owner"].id)}, {"$set": {"suspended": True}}
        )

    monkeypatch.setattr(GenerationCollection, "erase", revoke_after_erasure)
    with pytest.raises(DomainError):
        await service.run(cleanup)
    assert not await queue.database.retention_results.find_one(ns | {"id": str(cleanup.target_id)})
    await queue.database.members.update_one(
        {"id": str(team[3]["owner"].id)}, {"$set": {"suspended": False}}
    )
    monkeypatch.setattr(GenerationCollection, "erase", erase)
    await queue.cancel(owner, cleanup.id, idempotency_key="cancel-collected")
    await queue.retry(owner, cleanup.id, idempotency_key="resume-collected")
    with pytest.raises(DomainError):
        await service.run(cleanup)
    current = await queue.claim("collector-retry", kinds=("retention",))
    await service.run(current)
    assert await queue.accepted(current)


async def test_remote_write_ack_loss_prevents_generation_collection(publishing, team, monkeypatch):
    source, _, _ = await accepted_human_edit(publishing)
    queue = publishing[2]
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    operations = OwnerOperations(queue)
    await operations.submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            idempotency_key="lost-ack-rebuild",
        ),
    )
    rebuild = await queue.claim("lost-ack", kinds=("generation",))
    write = VectorProjection.write

    async def lose_ack(self, *args):
        await write(self, *args)
        raise TimeoutError("Synthetic acknowledged response loss")

    monkeypatch.setattr(VectorProjection, "write", lose_ack)
    with pytest.raises(TimeoutError):
        await GenerationService(queue).run(rebuild)
    await queue.fail(rebuild, DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Synthetic ack loss."))
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    generation = (
        await queue.database.generation_targets.find_one(ns | {"id": str(rebuild.target_id)})
    )["generation"]
    row = await queue.database.projection_generations.find_one(ns | {"id": generation})
    assert row["pending_count"] == 0 and row["uncertain_count"] == 1
    await queue.database.projection_generations.update_one(
        ns | {"id": generation}, {"$set": {"last_settled_at": now() - timedelta(days=3)}}
    )
    receipt = await operations.submit(
        owner,
        RetentionCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            resources="generations",
            idempotency_key="refuse-lost-ack",
        ),
    )
    plan = await queue.database.retention_plans.find_one(ns | {"job.id": str(receipt.job_id)})
    assert plan["targets"] == []


async def test_cancelled_projection_write_drains_before_abandoned_collection(
    publishing, team, monkeypatch
):
    source, _, _ = await accepted_human_edit(publishing)
    queue = publishing[2]
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    operations = OwnerOperations(queue)
    await operations.submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            idempotency_key="draining-rebuild",
        ),
    )
    rebuild = await queue.claim("draining", kinds=("generation",))
    started, release = asyncio.Event(), asyncio.Event()
    write = VectorProjection.write

    async def delay_ack(self, *args):
        await write(self, *args)
        started.set()
        await release.wait()

    monkeypatch.setattr(VectorProjection, "write", delay_ack)
    task = asyncio.create_task(GenerationService(queue).run(rebuild))
    await asyncio.wait_for(started.wait(), timeout=60)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    generation = (
        await queue.database.generation_targets.find_one(ns | {"id": str(rebuild.target_id)})
    )["generation"]
    key = ns | {"id": generation}
    assert (await queue.database.projection_generations.find_one(key))["pending_count"] == 1
    await queue.cancel(owner, rebuild.id, idempotency_key="cancel-inflight-rebuild")
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    assert (await queue.database.projection_generations.find_one(key))["pending_count"] == 1
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=30)
    row = await queue.database.projection_generations.find_one(key)
    assert row["pending_count"] == row["uncertain_count"] == 0
    await queue.database.projection_generations.update_one(
        key, {"$set": {"last_settled_at": now() - timedelta(days=3)}}
    )
    receipt = await operations.submit(
        owner,
        RetentionCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            resources="generations",
            idempotency_key="collect-drained",
        ),
    )
    plan = await queue.database.retention_plans.find_one(ns | {"job.id": str(receipt.job_id)})
    assert [row["generation"] for row in plan["targets"]] == [generation]
    job = await queue.claim("collector", kinds=("retention",))
    await RetentionService(queue).run(job)
    assert await queue.accepted(job)


async def test_generation_collection_filters_every_store_and_refuses_forged_target(
    publishing, team
):
    source, _, _ = await accepted_human_edit(publishing)
    queue = publishing[2]
    owner = await queue.policy.resolve_access(team[2]["owner"], [source.kg_id], Action.CONFIGURE)
    operations = OwnerOperations(queue)
    ns = {"org_id": str(source.org_id), "kg_id": str(source.kg_id)}
    old = (await queue.database.kgs.find_one({"id": ns["kg_id"]}))["generation"]
    await operations.submit(
        owner,
        GenerationCommand(
            kg_id=source.kg_id,
            expected_published_revision=2,
            idempotency_key="isolated-rebuild",
        ),
    )
    rebuild = await queue.claim("rebuild", kinds=("generation",))
    await GenerationService(queue).run(rebuild)
    past = now() - timedelta(days=3)
    await queue.database.projection_generations.update_one(
        ns | {"id": old}, {"$set": {"retired_at": past, "last_settled_at": past}}
    )
    receipt = await operations.submit(
        owner,
        RetentionCommand(
            kg_id=source.kg_id,
            expected_published_revision=3,
            resources="generations",
            idempotency_key="isolated-collection",
        ),
    )
    raw = await queue.database.owner_operations.find_one(ns | {"job.id": str(receipt.job_id)})
    from domain_kg.operations.generation_collection import (
        GenerationCollection,
        GenerationCollectionTarget,
    )
    from domain_kg.operations.models import OwnerOperation

    operation = OwnerOperation.model_validate({k: raw[k] for k in OwnerOperation.model_fields})
    target_row = (await queue.database.retention_plans.find_one(ns | {"id": str(operation.id)}))[
        "targets"
    ][0]
    target = GenerationCollectionTarget.model_validate(target_row)
    with pytest.raises(DomainError):
        await GenerationCollection(queue).validate(
            operation, target.model_copy(update={"generation": uuid4()})
        )
    stores = await queue.router.for_kg(owner, source.kg_id)
    collection = f"kg_{source.org_id.hex}_{source.kg_id.hex}_{UUID(old).hex}"
    sentinel, foreign_kg = uuid4(), uuid4()
    await stores.graph.driver.execute_query(
        "CREATE (n:ScopedProjection {org_id:$org,kg_id:$kg,generation:$gen,projection_id:$id})",
        org=ns["org_id"],
        kg=str(foreign_kg),
        gen=old,
        id=str(sentinel),
    )
    try:
        await stores.vectors.client.upsert(
            collection,
            wait=True,
            points=[
                models.PointStruct(
                    id=str(sentinel),
                    vector=[1, 0, 0],
                    payload={"org_id": ns["org_id"], "kg_id": str(foreign_kg), "generation": old},
                )
            ],
        )
        job = await queue.claim("collector", kinds=("retention",))
        await RetentionService(queue).run(job)
        assert await queue.accepted(job)
        records = await stores.vectors.client.retrieve(collection, ids=[str(sentinel)])
        assert len(records) == 1
        rows, _, _ = await stores.graph.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,projection_id:$id}) "
            "RETURN count(n) AS n",
            org=ns["org_id"],
            kg=str(foreign_kg),
            id=str(sentinel),
        )
        assert rows[0]["n"] == 1
    finally:
        await stores.graph.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,projection_id:$id}) DETACH DELETE n",
            org=ns["org_id"],
            kg=str(foreign_kg),
            id=str(sentinel),
        )
