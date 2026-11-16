"""Durable imports recover lost replies without mixing actors, namespaces or source ACLs."""
# ruff: noqa: F811

import asyncio
import hashlib
from uuid import uuid4

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.import_runs import (
    CreateImportRun,
    ImportFile,
    ImportRuns,
    ImportRunSubmission,
)
from domain_kg.materials.models import AddMaterial, UploadCommand

pytestmark = pytest.mark.storage


async def test_import_progress_survives_an_unrelated_publication_and_bounds_parallel_reads(
    publishing, monkeypatch
):
    materials, access, queue, coordinator, _, _ = publishing
    kg = access.kg_ids[0]
    _, job, checkpoint = await prepare(publishing)
    prepared = await ready(publishing, job, checkpoint)
    service = ImportRuns(materials, queue)
    await service.initialize()
    run = await service.create(
        access,
        kg,
        CreateImportRun(
            files=tuple(original(f"folder/{index}.txt", b"text") for index in range(12)),
            idempotency_key="progress-with-publication",
        ),
    )
    actual_item = service._item
    active = maximum = 0

    async def publish_during_read(current, identifier, source, index, row):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            if index == 0:
                await coordinator.publish(access, prepared)
            await asyncio.sleep(0.01)
            return await actual_item(current, identifier, source, index, row)
        finally:
            active -= 1

    monkeypatch.setattr(service, "_item", publish_during_read)
    restored = await service.get(access, kg, run.id)
    assert [item.path for item in restored.items] == [f"folder/{index}.txt" for index in range(12)]
    assert maximum == 4
    assert len(await service.list(access, kg)) == 1


async def test_replacement_preserves_metadata_and_current_evidence_until_publication(
    publishing, team, monkeypatch
):
    materials, access, queue, coordinator, _, _ = publishing
    _, policy, principals, _, _, _ = team
    kg = access.kg_ids[0]
    accepted, job, checkpoint = await prepare(
        publishing,
        command=AddMaterial(
            kg_id=kg,
            title="Versioned source",
            source="folder:folder/source.txt",
            inline_text="Alpha inhibits Beta.",
            tags=("keep-tag",),
            author="Source author",
            language="en",
            idempotency_key="first-version",
        ),
    )
    await coordinator.publish(access, await ready(publishing, job, checkpoint))
    target = await materials.database.material_access.find_one({"id": str(accepted.material_id)})
    replacement = b"Alpha inhibits Beta again."
    item = original("folder/source.txt", replacement)
    service = ImportRuns(materials, queue)
    await service.initialize()
    run = await service.create(
        access, kg, CreateImportRun(files=(item,), idempotency_key="replace")
    )
    staged = await upload(materials, access, kg, item, replacement, target=accepted.material_id)
    submitted = await service.submit(
        access,
        kg,
        run.id,
        0,
        ImportRunSubmission(
            upload_id=staged.upload_id,
            material_id=accepted.material_id,
            expected_active_version=accepted.version_id,
            expected_revision=target["revision"],
        ),
    )
    version = await materials.database.version_blobs.find_one(
        {"id": str(submitted.job.source.version_id)}
    )
    assert version["proposed"]["tags"] == ["keep-tag"]
    assert version["proposed"]["author"] == "Source author"
    assert version["proposed"]["language"] == "en"
    unchanged = await materials.database.material_access.find_one({"id": str(accepted.material_id)})
    assert unchanged["active_version_id"] == str(accepted.version_id)
    actual_get = queue.get
    revoked = False

    async def revoke_during_read(current, identifier):
        nonlocal revoked
        receipt = await actual_get(current, identifier)
        if not revoked:
            revoked = True
            owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
            await policy.set_material_acl(
                owner,
                kg,
                accepted.material_id,
                "restricted",
                member_ids=(),
                group_ids=(),
                idempotency_key="late-import-revocation",
            )
        return receipt

    monkeypatch.setattr(queue, "get", revoke_during_read)
    with pytest.raises(DomainError) as denied:
        await service.get(access, kg, run.id)
    assert denied.value.code in (ErrorCode.POLICY_CHANGED, ErrorCode.RESOURCE_NOT_ACCESSIBLE)


def original(path, content):
    return ImportFile(
        path=path,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        media_type="text/plain",
    )


async def upload(materials, access, kg, item, content, *, target=None):
    async def stream():
        yield content

    return await materials.upload(
        access,
        UploadCommand(
            kg_id=kg,
            sha256=item.sha256,
            size_bytes=item.size_bytes,
            media_type=item.media_type,
            idempotency_key=str(uuid4()),
            target_material_id=target,
        ),
        stream(),
    )


async def test_import_replay_survives_lost_admission_reply_and_isolates_job_failures(
    material_service, team, monkeypatch
):
    materials, kg = material_service
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    service = ImportRuns(materials, queue)
    await service.initialize()
    alpha, beta = b"Alpha original", b"Beta original"
    files = (original("folder/alpha.txt", alpha), original("folder/beta.txt", beta))
    command = CreateImportRun(files=files, idempotency_key="a-run")
    one, replay = await asyncio.gather(
        service.create(access, kg, command), service.create(access, kg, command)
    )
    assert one.id == replay.id and len(one.items) == 2
    assert all(item.state == "waiting" for item in one.items)
    with pytest.raises(DomainError, match="different input"):
        await service.create(access, kg, command.model_copy(update={"files": files[:1]}))
    preserved = await upload(materials, access, kg, files[0], alpha)
    actual_add = materials.add

    async def lose_response(*args, **kwargs):
        await actual_add(*args, **kwargs)
        raise RuntimeError("private backend failure is not returned")

    monkeypatch.setattr(materials, "add", lose_response)
    interrupted = await service.submit(
        access, kg, one.id, 0, ImportRunSubmission(upload_id=preserved.upload_id)
    )
    assert interrupted.state == "failed" and interrupted.can_resume
    assert "private" not in interrupted.message
    assert await materials.database.jobs.count_documents({"kg_id": str(kg)}) == 1
    # A fresh service process has only durable Mongo state; the browser supplies no bytes.
    monkeypatch.setattr(materials, "add", actual_add)
    restarted = ImportRuns(materials, queue)
    resumed, concurrent = await asyncio.gather(
        restarted.resume(access, kg, one.id, 0), restarted.resume(access, kg, one.id, 0)
    )
    assert resumed.job.job_id == concurrent.job.job_id
    assert resumed.state == "submitted" and not resumed.can_resume
    assert await materials.database.jobs.count_documents({"kg_id": str(kg)}) == 1
    with pytest.raises(DomainError):
        await restarted.submit(
            access, kg, one.id, 1, ImportRunSubmission(job_id=resumed.job.job_id)
        )
    other = await materials.add(
        access,
        AddMaterial(
            kg_id=kg,
            title="Beta",
            source="folder:folder/beta.txt",
            inline_text=beta.decode(),
            idempotency_key="beta-job",
        ),
    )
    await queue.cancel(access, other.job_id, idempotency_key="cancel-beta")
    attached = await restarted.submit(
        access, kg, one.id, 1, ImportRunSubmission(job_id=other.job_id)
    )
    assert attached.job.stage == "cancelled"  # An attachment never retries existing work.
    actual_get = queue.get
    status_reads = []

    async def count_status_reads(current, identifier):
        status_reads.append(identifier)
        return await actual_get(current, identifier)

    monkeypatch.setattr(queue, "get", count_status_reads)
    await restarted.resume(access, kg, one.id, 0)
    assert status_reads == [resumed.job.job_id]  # Single-item replay never scans the run.

    async def derived_unavailable(current, identifier):
        if identifier == resumed.job.job_id:
            raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "private provider diagnostics")
        return await actual_get(current, identifier)

    monkeypatch.setattr(queue, "get", derived_unavailable)
    partial = await restarted.get(access, kg, one.id)
    assert partial.items[0].code == ErrorCode.BACKEND_UNAVAILABLE
    assert partial.items[0].status_unavailable
    assert not partial.items[0].can_resume and "private" not in partial.items[0].message
    assert partial.items[1].job.stage == "cancelled"
    monkeypatch.setattr(queue, "get", actual_get)
    actual_authorize = queue._visible_job

    async def authority_unavailable(*args, **kwargs):
        raise DomainError(ErrorCode.BACKEND_UNAVAILABLE, "Authority unavailable")

    monkeypatch.setattr(queue, "_visible_job", authority_unavailable)
    with pytest.raises(DomainError) as authority_failure:
        await restarted.get(access, kg, one.id)
    assert authority_failure.value.code == ErrorCode.BACKEND_UNAVAILABLE
    monkeypatch.setattr(queue, "_visible_job", actual_authorize)
    assert (await restarted.list(access, kg))[0].id == one.id
    assert (await restarted.list(access, kg))[0].file_count == 2
    for principal in (principals["owner"], principals["reader"]):
        foreign_actor = await policy.resolve_access(principal, [kg], Action.READ)
        assert await restarted.list(foreign_actor, kg) == ()
        with pytest.raises(DomainError):
            await restarted.get(foreign_actor, kg, one.id)
    owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_material_acl(
        owner,
        kg,
        resumed.job.source.material_id,
        "restricted",
        member_ids=(),
        group_ids=(),
        idempotency_key="restrict-run",
    )
    with pytest.raises(DomainError):
        await restarted.get(access, kg, one.id)
    assert await materials.database.import_runs.count_documents({"org_id": str(org)}) == 1


async def test_import_http_retains_csrf_actor_namespace_and_current_job_receipts(
    material_service, team, catalog, issuer_fixture
):
    materials, kg = material_service
    identities, policy, principals, _, org, foreign_kg = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    content = b"HTTP import original"
    item = original("Downloads/document.txt", content)
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["editor"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/api/v1/kgs/{kg}/import-runs"
                body = {"files": [item.model_dump(mode="json")], "idempotency_key": "http-run"}
                assert (await browser.post(root, json=body)).status_code == 403
                headers = {"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf}
                created = await browser.post(root, json=body, headers=headers)
                assert created.status_code == 201 and created.headers["cache-control"] == "no-store"
                identifier = created.json()["id"]
                assert "command" not in created.json() and "issuer" not in created.json()
                access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
                preserved = await upload(materials, access, kg, item, content)
                endpoint = f"{root}/{identifier}/items/0"
                submitted = await browser.post(
                    endpoint, headers=headers, json={"upload_id": str(preserved.upload_id)}
                )
                assert submitted.status_code == 200
                job = submitted.json()["job"]
                await app.state.services.queue.cancel(
                    access, job["job_id"], idempotency_key="stop-http"
                )
                fresh = await browser.get(f"{root}/{identifier}")
                assert (
                    fresh.status_code == 200
                    and fresh.json()["items"][0]["job"]["stage"] == "cancelled"
                )
                resume = await browser.post(f"{endpoint}/resume", headers=headers)
                assert resume.status_code == 200 and resume.json()["job"]["stage"] == "cancelled"
                assert (await browser.get(root)).json()[0]["file_count"] == 1
                assert (
                    await browser.get(f"/api/v1/kgs/{foreign_kg}/import-runs/{identifier}")
                ).status_code == 404
                assert (
                    await browser.post(
                        f"{root}/{identifier}/items/-1",
                        headers=headers,
                        json={"job_id": job["job_id"]},
                    )
                ).status_code == 404
                assert (
                    await browser.post(
                        endpoint,
                        headers=headers,
                        json={"upload_id": str(preserved.upload_id), "job_id": job["job_id"]},
                    )
                ).status_code == 422
                await identities.database.members.update_one(
                    {"org_id": str(org), "subject": principals["editor"].subject},
                    {"$set": {"suspended": True}},
                )
                denied = await browser.get(f"{root}/{identifier}")
                assert denied.status_code in (401, 403) and item.path not in denied.text
