"""A real graph/vector write is never acceptance without the Mongo marker."""

import json
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_entity_resolution import TEXT, batch_for

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.ingestion.models import ParsedChunk
from domain_kg.ingestion.pipeline import extraction_steps, preparation_steps
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import AddMaterial
from domain_kg.models import Namespace
from domain_kg.providers.profiles import (
    ProviderConfig,
    ProviderConnection,
    ProviderProfile,
    ProviderRegistry,
)
from domain_kg.providers.service import ProviderService
from domain_kg.revisions.models import PublishRevision

pytestmark = pytest.mark.storage


async def make_publishing(
    material_service,
    team,
    *,
    extraction_factory=batch_for,
    embedding_factory=None,
    restricted=False,
    embedding_model="fixture-embedding",
    profile_overrides=None,
):
    from domain_kg.revisions.publication import ProjectionCoordinator

    materials, kg = material_service
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    profile = ProviderProfile(
        id=uuid4(),
        connection_ref="fixture",
        extraction_model="fixture-extraction",
        embedding_model=embedding_model,
        embedding_dimensions=3,
        data_policy_approved=True,
        allow_restricted=restricted,
        allowed_namespaces=(Namespace(org_id=org, kg_id=kg),),
    )
    if profile_overrides:
        profile = ProviderProfile.model_validate(profile.model_copy(update=profile_overrides))
    faults = {}

    def response(request):
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            faults.setdefault("embedding_inputs", []).extend(body["input"])
            vectors = [
                {"index": index, "embedding": [1.0, 0.1 * (index + 1), 0.2]}
                for index, _ in enumerate(body["input"])
            ]
            if embedding_factory is not None:
                vectors = [
                    {"index": index, "embedding": embedding_factory(text)}
                    for index, text in enumerate(body["input"])
                ]
            if faults.get("dimensions"):
                vectors[0]["embedding"] = [1.0]
            return httpx.Response(
                200,
                json={
                    "data": vectors,
                    "model": profile.embedding_model,
                    "usage": {"prompt_tokens": 10, "total_tokens": 10},
                },
            )
        chunk = ParsedChunk.model_validate(json.loads(body["messages"][1]["content"])["source"])
        batch = extraction_factory(chunk, profile.id)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": batch.model_dump_json(include={"entities", "assertions"}),
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    providers = ProviderService(
        policy,
        materials.router,
        ProviderRegistry(
            ProviderConfig(
                profiles=(profile,),
                connections=(
                    ProviderConnection(
                        ref="fixture",
                        endpoint="https://provider.example/v1",
                        api_key=SecretStr("fixture"),
                    ),
                ),
            )
        ),
        transport=httpx.MockTransport(response),
    )
    await providers.bind(access, kg, profile.id, expected_revision=0, idempotency_key="profile")
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    queue = JobQueue(policy, materials.router, lease_seconds=120)
    await queue.initialize()
    preparation = preparation_steps(queue, providers=providers)
    steps = preparation + extraction_steps(queue, providers, preparation[-1])
    coordinator = ProjectionCoordinator(queue, providers)
    return materials, access, queue, coordinator, steps, faults


@pytest.fixture
async def publishing(material_service, team):
    return await make_publishing(material_service, team)


async def prepare(publishing, *, key="first", command=None):
    materials, access, queue, coordinator, steps, _ = publishing
    from domain_kg.materials.models import ReplaceMaterial

    submit = materials.replace if isinstance(command, ReplaceMaterial) else materials.add
    receipt = await submit(
        access,
        command
        or AddMaterial(
            kg_id=access.kg_ids[0],
            title="Publication fixture",
            inline_text=TEXT,
            idempotency_key=key,
        ),
    )
    job = await queue.claim("publisher")
    for step in steps:
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
    return receipt, job, checkpoint


async def ready(publishing, job, checkpoint):
    _, access, queue, coordinator, _, _ = publishing
    embedded = await coordinator.embed(job, checkpoint)
    await queue.checkpoint(job, embedded)
    batch = await coordinator.prepare(job, embedded)
    staged = await coordinator.stage(access, batch)
    report = await coordinator.verify(access, staged)
    assert report.ready
    lease = await queue.acquire_publication(job)
    return PublishRevision(
        report=report,
        lease_token=lease.lease_token,
        fence=lease.fence,
        expected_revision=batch.expected_revision,
        idempotency_key=f"publish-{job.id}",
    )


async def test_staging_is_invisible_and_acceptance_is_idempotent(publishing, mongo_db):
    from domain_kg.revisions.journal import PublicationJournal

    _, access, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    journal = PublicationJournal(queue)
    assert (await journal.load(access, job.kg_id)).sources == ()
    command = await ready(publishing, job, checkpoint)
    assert (await journal.load(access, job.kg_id)).revision == 0
    accepted = await coordinator.publish(access, command)
    assert accepted.state == "published" and accepted.published_revision == 1
    assert await coordinator.publish(access, command) == accepted  # Lost acknowledgement.
    view = await journal.load(access, job.kg_id)
    assert [(source.material_id, source.version_id) for source in view.sources] == [
        (receipt.material_id, receipt.version_id)
    ]
    assert view.projections[0].attempt_id == job.attempt_id
    row = await mongo_db.jobs.find_one({"id": str(job.id)})
    assert row["stage"] == "ready" and row["accepted_manifest_id"] == str(view.manifest_id)
    await queue.finish(job)  # A worker can die immediately after the acceptance marker.


async def test_wrong_embedding_dimension_never_changes_acceptance(publishing):
    from domain_kg.revisions.journal import PublicationJournal

    _, access, queue, coordinator, _, faults = publishing
    _, job, checkpoint = await prepare(publishing)
    faults["dimensions"] = True
    with pytest.raises(DomainError):
        await coordinator.embed(job, checkpoint)
    assert (await PublicationJournal(queue).load(access, job.kg_id)).revision == 0


async def test_native_worker_runs_all_stages_and_stops_after_atomic_acceptance(
    publishing, mongo_db
):
    from domain_kg.ingestion.pipeline import Pipeline, publication_steps
    from domain_kg.jobs.runner import JobRunner

    materials, access, queue, coordinator, steps, _ = publishing
    all_steps = steps + publication_steps(queue, coordinator, steps[-1])
    pipeline = Pipeline(all_steps)
    assert pipeline.configured
    receipt = await materials.add(
        access,
        AddMaterial(
            kg_id=access.kg_ids[0],
            title="Worker fixture",
            inline_text=TEXT,
            idempotency_key="worker",
        ),
    )
    await JobRunner(queue, pipeline).run_job(await queue.claim("native-worker"))
    raw = await mongo_db.jobs.find_one({"id": str(receipt.job_id)})
    assert raw["stage"] == "ready" and raw["accepted_manifest_id"]


async def test_checkpoint_hash_changes_when_owner_reselects_provider(publishing, team):
    _, _, _, coordinator, steps, _ = publishing
    _, job, _ = await prepare(publishing)
    before = await steps[3].hash_for(job)
    _, policy, principals, _, _, _ = team
    owner = await policy.resolve_access(principals["owner"], [job.kg_id], Action.CONFIGURE)
    selected = await coordinator.providers.selected(owner, job.kg_id)
    from uuid import UUID

    await coordinator.providers.bind(
        owner,
        job.kg_id,
        UUID(selected["profile_id"]),
        expected_revision=1,
        idempotency_key="reselect",
    )
    assert await steps[3].hash_for(job) != before


async def test_abandoned_attempt_of_accepted_version_is_excluded_before_top_k(publishing):
    from qdrant_client import models

    from domain_kg.engine.scope import EligibilityResolver, ScopePredicates
    from domain_kg.retrieval.models import SearchRequest
    from domain_kg.revisions.journal import PublicationJournal

    _, access, queue, coordinator, _, _ = publishing
    receipt, job, checkpoint = await prepare(publishing)
    command = await ready(publishing, job, checkpoint)
    await coordinator.publish(access, command)
    stores = await queue.router.for_kg(access, job.kg_id)
    abandoned = uuid4()
    await stores.vectors.client.upsert(
        stores.vectors.collection,
        wait=True,
        points=[
            models.PointStruct(
                id=str(abandoned),
                vector=[1.0, 0.0, 0.0],
                payload={
                    "org_id": str(job.org_id),
                    "kg_id": str(job.kg_id),
                    "generation": str(command.report.batch.generation),
                    "material_id": str(receipt.material_id),
                    "source_version_id": str(receipt.version_id),
                    "attempt_id": str(uuid4()),
                    "manifest_id": str(uuid4()),
                    "manifest_hash": "f" * 64,
                    "text": "Unpublished repair must not enter an accepted view.",
                },
            )
        ],
    )
    scope = await EligibilityResolver(queue.policy, PublicationJournal(queue)).resolve(
        access, SearchRequest(kg_ids=(job.kg_id,), query="Alpha"), job.kg_id
    )
    result = await stores.vectors.client.query_points(
        stores.vectors.collection,
        query=[1.0, 0.0, 0.0],
        query_filter=ScopePredicates(scope).vector_filter,
        limit=10,
    )
    assert str(abandoned) not in {str(point.id) for point in result.points}
    assert len(result.points) == command.report.receipts[0].record_count


async def test_embedding_checkpoint_reuses_owned_artifact_without_provider_call(
    publishing, mongo_db
):
    _, access, queue, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    embedded = await coordinator.embed(job, checkpoint)
    await queue.checkpoint(job, embedded)
    before = await mongo_db.provider_usage.find_one(
        {"org_id": str(access.org_id), "kg_id": str(job.kg_id)}
    )
    assert await coordinator.embed(job, checkpoint) == embedded
    after = await mongo_db.provider_usage.find_one(
        {"org_id": str(access.org_id), "kg_id": str(job.kg_id)}
    )
    assert after["embedding_calls"] == before["embedding_calls"]


@pytest.mark.parametrize("failed", ["graph", "vector"])
async def test_one_store_success_never_publishes_and_lost_write_ack_is_recoverable(
    publishing, monkeypatch, failed
):
    from domain_kg.errors import ErrorCode
    from domain_kg.revisions.journal import PublicationJournal
    from domain_kg.revisions.publication import projection_rows
    from domain_kg.storage.neo4j import GraphProjection
    from domain_kg.storage.qdrant import VectorProjection

    _, access, queue, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    embedded = await coordinator.embed(job, checkpoint)
    await queue.checkpoint(job, embedded)
    batch = await coordinator.prepare(job, embedded)
    stores = await queue.router.for_kg(access, job.kg_id)
    if failed == "graph":
        document = await coordinator._document(
            access, await coordinator.journal.attempt(access, batch.manifest_id)
        )
        await VectorProjection(stores.vectors).write(
            projection_rows(document, batch), [list(row.vector) for row in document.rows]
        )
    adapter = GraphProjection if failed == "graph" else VectorProjection
    original = adapter.write

    async def unavailable(*_):
        raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Injected store failure.", retryable=True)

    monkeypatch.setattr(adapter, "write", unavailable)
    with pytest.raises(DomainError):
        await coordinator.stage(access, batch)
    assert (await PublicationJournal(queue).load(access, job.kg_id)).revision == 0
    monkeypatch.setattr(adapter, "write", original)
    # Idempotent real writes preserve any completed work after an acknowledgement loss.
    staged = await coordinator.stage(access, batch)
    report = await coordinator.verify(access, staged)
    assert report.ready and len(report.receipts) == 3


@pytest.mark.parametrize("backend", ["graph", "vector"])
async def test_same_id_content_corruption_fails_closed_and_requests_repair(
    publishing, backend, mongo_db
):
    from domain_kg.revisions.journal import PublicationJournal
    from domain_kg.revisions.publication import projection_rows

    _, access, queue, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    command = await ready(publishing, job, checkpoint)
    await coordinator.publish(access, command)
    attempt = await coordinator.journal.attempt(access, command.report.batch.manifest_id)
    document = await coordinator._document(access, attempt)
    row = projection_rows(document, command.report.batch)[0]
    stores = await queue.router.for_kg(access, job.kg_id)
    if backend == "graph":
        await stores.graph.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,projection_id:$id}) SET n.text=$text",
            org=str(job.org_id),
            kg=str(job.kg_id),
            id=row["projection_id"],
            text="corrupted",
        )
    else:
        await stores.vectors.client.set_payload(
            stores.vectors.collection,
            payload={"text": "corrupted"},
            points=[row["projection_id"]],
            wait=True,
        )
    with pytest.raises(DomainError) as error:
        await PublicationJournal(queue).load(access, job.kg_id)
    assert error.value.code == "PROJECTION_INCONSISTENT"
    pending = await mongo_db.repair_requests.find_one(
        {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
    )
    assert pending["state"] == "pending_authorized_repair"


async def test_accepted_projection_records_cannot_be_overwritten(publishing):
    from domain_kg.revisions.journal import PublicationJournal
    from domain_kg.revisions.publication import projection_rows
    from domain_kg.storage.neo4j import GraphProjection
    from domain_kg.storage.qdrant import VectorProjection

    _, access, queue, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    command = await ready(publishing, job, checkpoint)
    await coordinator.publish(access, command)
    document = await coordinator._document(
        access, await coordinator.journal.attempt(access, command.report.batch.manifest_id)
    )
    rows = projection_rows(document, command.report.batch)
    changed = [row | {"text": "must not overwrite accepted text"} for row in rows]
    stores = await queue.router.for_kg(access, job.kg_id)
    with pytest.raises(DomainError):
        await GraphProjection(stores.graph).write(changed)
    await VectorProjection(stores.vectors).write(changed, [[0.0, 1.0, 0.0] for _ in rows])
    assert (await PublicationJournal(queue).load(access, job.kg_id)).revision == 1


async def test_repair_uses_retained_artifacts_and_a_new_attempt(publishing, mongo_db):
    from domain_kg.revisions.journal import PublicationJournal
    from domain_kg.revisions.repair import RepairService

    _, access, queue, coordinator, _, _ = publishing
    first, job, checkpoint = await prepare(publishing)
    command = await ready(publishing, job, checkpoint)
    await coordinator.publish(access, command)
    stores = await queue.router.for_kg(access, job.kg_id)
    from qdrant_client import models

    await stores.vectors.client.delete(
        stores.vectors.collection,
        wait=True,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="attempt_id", match=models.MatchValue(value=str(job.attempt_id))
                    )
                ]
            )
        ),
    )
    with pytest.raises(DomainError):
        await PublicationJournal(queue).load(access, job.kg_id)
    materials = publishing[0]
    waiting = await materials.add(
        access,
        AddMaterial(
            kg_id=job.kg_id,
            title="Waiting for a provider",
            inline_text=TEXT,
            idempotency_key="waiting",
        ),
    )
    service = RepairService(queue)
    requested = await service.request(
        access, job.kg_id, first.material_id, idempotency_key="repair"
    )
    assert (
        await service.request(access, job.kg_id, first.material_id, idempotency_key="repair")
        == requested
    )
    recovery = await queue.claim("repairer", kinds=("repair",))
    from domain_kg.providers.profiles import ProviderRegistry

    coordinator.providers.registry = ProviderRegistry()
    before = await mongo_db.provider_usage.find_one(
        {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
    )
    embedded = await service.embed(recovery, input_hash="d" * 64)
    await queue.checkpoint(recovery, embedded)
    from domain_kg.ingestion.pipeline import configured_pipeline
    from domain_kg.jobs.runner import JobRunner

    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(recovery)
    view = await PublicationJournal(queue).load(access, job.kg_id)
    assert view.revision == 2 and view.sources[0].version_id == first.version_id
    assert view.projections[0].attempt_id != job.attempt_id
    after = await mongo_db.provider_usage.find_one(
        {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
    )
    assert before["calls"] == after["calls"]
    assert (await queue.get(access, waiting.job_id)).stage == "queued"


async def test_native_scan_without_ocr_publishes_original_with_actionable_warning(publishing):
    import hashlib
    from pathlib import Path

    from domain_kg.ingestion.pipeline import configured_pipeline
    from domain_kg.jobs.runner import JobRunner
    from domain_kg.materials.models import UploadCommand

    materials, access, queue, coordinator, _, _ = publishing
    import asyncio

    body = await asyncio.to_thread(
        Path("tests/fixtures/materials/scanned-bilingual.pdf").read_bytes
    )

    async def stream():
        yield body

    uploaded = await materials.upload(
        access,
        UploadCommand(
            kg_id=access.kg_ids[0],
            media_type="application/pdf",
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            idempotency_key="scan-upload",
        ),
        stream(),
    )
    receipt = await materials.add(
        access,
        AddMaterial(
            kg_id=access.kg_ids[0],
            title="Native scan",
            upload_id=uploaded.upload_id,
            idempotency_key="scan",
        ),
    )
    await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(
        await queue.claim("native-scan")
    )
    state = await queue.get(access, receipt.job_id)
    assert state.stage == "ready_with_warnings" and "OCR_REQUIRED" in state.warnings


async def test_corrupted_accepted_binding_returns_safe_consistency_error(publishing, mongo_db):
    from domain_kg.revisions.journal import PublicationJournal

    _, access, queue, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    command = await ready(publishing, job, checkpoint)
    await coordinator.publish(access, command)
    await mongo_db.publication_bindings.update_one(
        {
            "org_id": str(job.org_id),
            "kg_id": str(job.kg_id),
            "view_manifest_id": str(command.report.batch.manifest_id),
        },
        {"$set": {"binding.generation": str(uuid4())}},
    )
    with pytest.raises(DomainError) as error:
        await PublicationJournal(queue).load(access, job.kg_id)
    assert error.value.code == "PROJECTION_INCONSISTENT"
