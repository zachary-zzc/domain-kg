"""Editor file/text forms call the native lifecycle and preserve current revisions."""
# ruff: noqa: F811

import hashlib
from uuid import UUID

import httpx
import pytest
from test_entity_resolution import TEXT
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import publishing  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner

pytestmark = pytest.mark.storage


async def test_editor_upload_metadata_and_replacement_forms_publish_native_versions(
    publishing, team, catalog, issuer_fixture
):
    materials, editor, queue, coordinator, _, faults = publishing
    kg = editor.kg_ids[0]
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=catalog[0],
            providers=coordinator.providers,
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["editor"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                form = await browser.get(f"/admin/kgs/{kg}/materials/new")
                assert form.status_code == 200 and 'name="inline_text"' in form.text
                raw = TEXT.encode()
                uploaded = await browser.post(
                    f"/api/v1/kgs/{kg}/uploads",
                    params={
                        "media_type": "text/plain",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "size_bytes": len(raw),
                        "idempotency_key": "console-file-upload",
                    },
                    headers={
                        "Origin": "http://localhost:8000",
                        "x-csrf-token": ticket.csrf,
                        "Content-Type": "text/plain",
                    },
                    content=raw,
                )
                assert uploaded.status_code == 200
                command = {
                    "csrf_token": ticket.csrf,
                    "title": "Uploaded work note",
                    "upload_id": uploaded.json()["upload_id"],
                    "inline_text": "",
                    "tags": "work",
                    "acl_mode": "kg",
                    "idempotency_key": "console-add-file",
                }
                forged = await browser.post(
                    f"/admin/kgs/{kg}/materials",
                    headers={"Origin": "http://localhost:8000"},
                    data=command | {"principal": "owner"},
                )
                assert forged.status_code == 422
                assert (
                    await queue.database.jobs.count_documents(
                        {"org_id": str(org), "kg_id": str(kg)}
                    )
                    == 0
                )
                accepted = await browser.post(
                    f"/admin/kgs/{kg}/materials",
                    headers={"Origin": "http://localhost:8000"},
                    data=command,
                )
                assert accepted.status_code == 303
                job_id = UUID(accepted.headers["location"].rsplit("/", 1)[1])
                job = await queue.claim("console-ingest", kinds=("ingest",))
                assert job.id == job_id
                await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(
                    job
                )
                assert (await queue.get(editor, job.id)).stage == "ready"
                admitted = await queue.database.jobs.find_one(
                    {
                        "org_id": str(editor.org_id),
                        "kg_id": str(kg),
                        "id": str(job.id),
                    }
                )
                source = admitted["material_id"]
                version = str(job.target_id)
                assert (
                    b"".join(
                        [
                            part
                            async for part in materials.original(
                                editor, kg, UUID(source), UUID(version)
                            )
                        ]
                    )
                    == raw
                )
                before = len(faults["embedding_inputs"])
                metadata = await browser.post(
                    f"/admin/kgs/{kg}/materials/{source}/metadata",
                    headers={"Origin": "http://localhost:8000"},
                    data={
                        "csrf_token": ticket.csrf,
                        "title": "Updated work note",
                        "tags": "reviewed, work",
                        "expected_revision": "1",
                        "idempotency_key": "console-metadata",
                    },
                )
                assert metadata.status_code == 303 and len(faults["embedding_inputs"]) == before
                detail = await browser.get(f"/admin/kgs/{kg}/materials/{source}")
                assert detail.status_code == 200 and "Updated work note" in detail.text
                replace_form = await browser.get(f"/admin/kgs/{kg}/materials/{source}/replace")
                assert replace_form.status_code == 200 and version in replace_form.text
                replacement = await browser.post(
                    f"/admin/kgs/{kg}/materials/{source}/replace",
                    headers={"Origin": "http://localhost:8000"},
                    data={
                        "csrf_token": ticket.csrf,
                        "title": "Replacement note",
                        "inline_text": TEXT,
                        "upload_id": "",
                        "tags": "reviewed",
                        "expected_active_version": version,
                        "idempotency_key": "console-replace",
                    },
                )
                assert replacement.status_code == 303
                next_job = await queue.claim("console-replace", kinds=("replace",))
                await JobRunner(queue, configured_pipeline(queue, coordinator.providers)).run_job(
                    next_job
                )
                assert (await queue.get(editor, next_job.id)).stage == "ready"
                assert (
                    "Replacement note"
                    in (await browser.get(f"/admin/kgs/{kg}/materials/{source}")).text
                )
