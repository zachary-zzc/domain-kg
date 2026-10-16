"""Browser revisions retain source ownership, pending state and safe human diffs."""
# ruff: noqa: F811

import httpx
import pytest
from test_curation import alpha_id, publish_curation
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app

pytestmark = pytest.mark.storage


async def test_node_edit_pending_history_stale_form_and_owner_compensation(
    publishing, team, catalog, issuer_fixture
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
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
            ticket = await app.state.identity.sessions.create(principals["editor"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000"},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/admin/kgs/{job.kg_id}"
                node = f"{root}/nodes/{alpha}"
                assert (await browser.get(node)).status_code == 200
                form = await browser.get(f"{node}/edit")
                assert form.status_code == 200 and 'name="expected_revision" value="0"' in form.text
                fields = {
                    "csrf_token": ticket.csrf,
                    "expected_revision": "0",
                    "curation_scope": "source",
                    "source_ids": str(source.material_id),
                    "reason": "Reviewed <script>reason</script>",
                    "idempotency_key": "console-node-edit",
                    "description": "<script>alert('review')</script>",
                }
                result = await browser.post(f"{node}/edit", data=fields)
                assert result.status_code == 303 and "/jobs/" in result.headers["location"]
                pending = await browser.get(f"{node}/history")
                assert pending.status_code == 200 and "pending" in pending.text
                assert "&lt;script&gt;" in pending.text and "<script>reason" not in pending.text
                receipt = await queue.database.curation_commands.find_one(
                    {"org_id": str(org), "kg_id": str(job.kg_id), "key": "console-node-edit"}
                )
                from domain_kg.revisions.models import RevisionReceipt

                revision = RevisionReceipt.model_validate(receipt["receipt"])
                await publish_curation(queue, coordinator, editor, revision)
                stale = await browser.post(
                    f"{node}/edit", data=fields | {"idempotency_key": "console-node-stale"}
                )
                assert stale.status_code == 409 and "VERSION_CONFLICT" in stale.text
                view = await browser.get(node)
                assert "&lt;script&gt;alert" in view.text and "<script>alert" not in view.text
                rollback_url = f"{root}/revisions/{revision.revision_id}/rollback"
                rollback = {
                    "csrf_token": ticket.csrf,
                    "expected_current_revision": "1",
                    "reason": "Compensate reviewed field",
                    "idempotency_key": "console-rollback",
                }
                assert (await browser.post(rollback_url, data=rollback)).status_code in (403, 404)
                owner = await app.state.identity.sessions.create(principals["owner"], org)
                browser.cookies.set("domain_kg_session", owner.session_id)
                browser.cookies.set("domain_kg_csrf", owner.csrf)
                rollback["csrf_token"] = owner.csrf
                result = await browser.post(rollback_url, data=rollback)
                assert result.status_code == 303 and "/jobs/" in result.headers["location"]
                history = await browser.get(f"{node}/history")
                assert "Compensate reviewed field" in history.text and "pending" in history.text

