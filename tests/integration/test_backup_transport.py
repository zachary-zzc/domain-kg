"""Owner backup controls use verified jobs; browser and HTTP boundaries share policy."""
# ruff: noqa: F811

import hashlib

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.runner import JobRunner
from domain_kg.materials.models import AddMaterial
from domain_kg.operations.executor import OwnerJobExecutor
from domain_kg.worker import serve

pytestmark = pytest.mark.storage


async def test_owner_can_queue_and_download_verified_backup_while_reader_is_denied(
    publishing, team, catalog, issuer_fixture
):
    materials, editor, queue, coordinator, _, _ = publishing
    source, source_job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, source_job, checkpoint))
    pending = await materials.add(
        editor,
        AddMaterial(
            kg_id=source.kg_id,
            title="Do not claim",
            inline_text="pending",
            idempotency_key="backup-worker-leaves-source-queued",
        ),
    )
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=coordinator.providers,
        )
        async with app.router.lifespan_context(app):
            limited = principals["owner"].model_copy(
                update={"scopes": frozenset({"domain_kg:read", "domain_kg:admin"})}
            )
            ticket = await app.state.identity.sessions.create(limited, org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as client:
                operations = f"/admin/kgs/{source.kg_id}/operations"
                page = await client.get(operations)
                assert page.status_code == 200 and "Create verified backup" in page.text
                fields = {
                    "csrf_token": ticket.csrf,
                    "expected_published_revision": "1",
                    "retention_days": "7",
                    "idempotency_key": "console-backup-job",
                }
                assert (
                    await client.post(f"{operations}/backup", data=fields | {"csrf_token": "wrong"})
                ).status_code == 403
                admitted = await client.post(f"{operations}/backup", data=fields)
                assert admitted.status_code == 303
                assert (await client.get(admitted.headers["location"])).status_code == 200
                job_id = admitted.headers["location"].rsplit("/", 1)[1]
                cancelled = await client.post(
                    admitted.headers["location"] + "/cancel",
                    data={"csrf_token": ticket.csrf, "idempotency_key": "owner-backup-cancel"},
                )
                assert cancelled.status_code == 303
                retried = await client.post(
                    f"/api/v1/kgs/{source.kg_id}/jobs/{job_id}/retry",
                    json={"idempotency_key": "owner-backup-retry"},
                )
                assert retried.status_code == 202
                await serve(
                    queue,
                    JobRunner(
                        queue,
                        configured_pipeline(queue, coordinator.providers),
                        operations=OwnerJobExecutor(queue),
                    ),
                    once=True,
                    worker_id="backup-only",
                    operations_only=True,
                )
                owner = await queue.policy.resolve_access(
                    principals["owner"], [source.kg_id], Action.BACKUP
                )
                assert (await queue.get(owner, pending.job_id)).stage == "queued"
                base = f"/api/v1/kgs/{source.kg_id}/backups"
                response = await client.get(f"{base}/jobs/{job_id}")
                assert response.status_code == 200
                manifest = response.json()
                backup_id = manifest["backup_id"]
                page = await client.get(f"{base}/{backup_id}/pages/0")
                assert page.status_code == 200
                original = next(part for part in page.json()["parts"] if part["kind"] == "original")
                link = f"{base}/{backup_id}/pages/0/objects/{original['object']['id']}"
                downloaded = await client.get(link)
                assert downloaded.status_code == 200 and downloaded.content
                assert (
                    hashlib.sha256(downloaded.content).hexdigest() == original["object"]["sha256"]
                )
                assert downloaded.headers["content-disposition"].startswith("attachment;")
                assert downloaded.headers["cache-control"] == "no-store"
                reader = await app.state.identity.sessions.create(principals["reader"], org)
                client.cookies.set("domain_kg_session", reader.session_id)
                client.cookies.set("domain_kg_csrf", reader.csrf)
                for endpoint in (f"{base}/jobs/{job_id}", f"{base}/{backup_id}/pages/0", link):
                    assert (await client.get(endpoint)).status_code in (403, 404)
                assert (
                    await client.post(
                        f"{operations}/backup", data=fields | {"csrf_token": reader.csrf}
                    )
                ).status_code in (403, 404)
                client.cookies.set("domain_kg_session", ticket.session_id)
                client.cookies.set("domain_kg_csrf", ticket.csrf)
                from domain_kg.operations.packages import PackageObject
                from domain_kg.operations.storage import PackageStore

                reference = PackageObject.model_validate(original["object"])
                binding = (await queue.router.for_sources(owner, source.kg_id)).blobs
                binding.client.put_object(
                    Bucket=binding.bucket,
                    Key=PackageStore(binding)._key(reference),
                    Body=b"corrupt",
                )
                rejected = await client.get(link)
                assert rejected.status_code == 422
                assert rejected.content != b"corrupt"
