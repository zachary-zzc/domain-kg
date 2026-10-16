"""Mongo fences, real projection stores, concurrent revisions, and lost workers."""

import asyncio
from datetime import timedelta

import pytest
from test_entity_resolution import TEXT
from test_publication import make_publishing, prepare, ready

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.jobs.models import Checkpoint
from domain_kg.jobs.queue import now
from domain_kg.materials.models import ReplaceMaterial
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import PublishRevision

pytestmark = pytest.mark.storage


@pytest.fixture
async def publishing(material_service, team):
    return await make_publishing(material_service, team)


async def test_revocation_after_readiness_cannot_advance_marker(publishing, team):
    _, access, queue, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    command = await ready(publishing, job, checkpoint)
    identities, policy, principals, members, _, _ = team
    await identities.change_member(
        members["owner"],
        members["editor"].id,
        expected_revision=0,
        suspended=True,
        idempotency_key="revoke-before-publish",
    )
    with pytest.raises(DomainError):
        await coordinator.publish(access, command)
    owner = await policy.resolve_access(principals["owner"], [job.kg_id], Action.READ)
    assert (await PublicationJournal(queue).load(owner, job.kg_id)).revision == 0


async def test_dead_worker_attempt_cannot_publish_or_overwrite_successor(publishing, mongo_db):
    _, access, queue, coordinator, _, _ = publishing
    first, stale, checkpoint = await prepare(publishing)
    old_command = await ready(publishing, stale, checkpoint)
    await mongo_db.jobs.update_one(
        {"id": str(stale.id)}, {"$set": {"lease_until": now() - timedelta(seconds=1)}}
    )
    await mongo_db.kgs.update_one(
        {"id": str(stale.kg_id)},
        {"$set": {"publication_lease.lease_until": now() - timedelta(seconds=1)}},
    )
    successor = await queue.claim("recovered-worker")
    assert successor.id == stale.id and successor.attempt_id != stale.attempt_id
    with pytest.raises(DomainError) as conflict:
        await coordinator.publish(access, old_command)
    assert conflict.value.code == "VERSION_CONFLICT"
    cached = await mongo_db.job_checkpoints.find_one(
        {"job_id": str(stale.id), "stage": "embedding"}
    )
    embedded = Checkpoint.model_validate({key: cached[key] for key in Checkpoint.model_fields})
    batch = await coordinator.prepare(successor, embedded)
    report = await coordinator.verify(access, await coordinator.stage(access, batch))
    lease = await queue.acquire_publication(successor)
    accepted = await coordinator.publish(
        access,
        PublishRevision(
            report=report,
            lease_token=lease.lease_token,
            fence=lease.fence,
            expected_revision=0,
            idempotency_key="recovered",
        ),
    )
    assert accepted.published_revision == 1
    with pytest.raises(DomainError):
        await coordinator.stage(access, old_command.report.batch)
    view = await PublicationJournal(queue).load(access, stale.kg_id)
    assert view.sources[0].version_id == first.version_id
    assert view.projections[0].attempt_id == successor.attempt_id


async def test_concurrent_replacements_keep_old_view_until_one_winner(publishing, mongo_db):
    _, access, queue, coordinator, _, _ = publishing
    initial, job, checkpoint = await prepare(publishing)
    await coordinator.publish(access, await ready(publishing, job, checkpoint))
    first_command = ReplaceMaterial(
        kg_id=job.kg_id,
        material_id=initial.material_id,
        expected_active_version=initial.version_id,
        title="Version two",
        inline_text=TEXT + " Version two.",
        idempotency_key="replace-two",
    )
    second_command = first_command.model_copy(
        update={
            "title": "Version three",
            "inline_text": TEXT + " Version three.",
            "idempotency_key": "replace-three",
        }
    )
    first, job_a, parsed_a = await prepare(publishing, command=first_command)
    _, job_b, parsed_b = await prepare(publishing, command=second_command)
    command = await ready(publishing, job_a, parsed_a)
    embedded_b = await coordinator.embed(job_b, parsed_b)
    await queue.checkpoint(job_b, embedded_b)
    before = await PublicationJournal(queue).load(access, job.kg_id)
    assert before.revision == 1 and before.sources[0].version_id == initial.version_id
    outcome = await asyncio.gather(
        coordinator.publish(access, command),
        coordinator.prepare(job_b, embedded_b),
        return_exceptions=True,
    )
    assert outcome[0].published_revision == 2
    assert isinstance(outcome[1], DomainError)
    with pytest.raises(DomainError):
        await coordinator.prepare(job_b, embedded_b)
    view = await PublicationJournal(queue).load(access, job.kg_id)
    assert view.revision == 2 and view.sources[0].version_id == first.version_id
    stores = await queue.router.for_kg(access, job.kg_id)
    previous = await stores.authority._database.version_blobs.find_one(
        {"id": str(initial.version_id)}
    )
    assert previous["state"] == "retained"
    source = await mongo_db.material_access.find_one({"id": str(initial.material_id)})
    assert source["active_version_id"] == str(first.version_id)


async def test_concurrent_publication_replay_returns_one_receipt(publishing):
    _, access, _, coordinator, _, _ = publishing
    _, job, checkpoint = await prepare(publishing)
    command = await ready(publishing, job, checkpoint)
    outcomes = await asyncio.gather(
        *(coordinator.publish(access, command) for _ in range(2)), return_exceptions=True
    )
    assert not any(isinstance(outcome, Exception) for outcome in outcomes)
    assert outcomes[0] == outcomes[1] and outcomes[0].published_revision == 1
