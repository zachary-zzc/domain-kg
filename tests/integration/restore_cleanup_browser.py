"""Reuse the same tiny cleanup fixture for real session/CSRF/owner transport checks."""

import httpx
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.jobs.models import JobReceipt


async def submit_cleanup_via_browser(command, team, catalog, providers, issuer_fixture):
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=providers,
        )
        async with app.router.lifespan_context(app):
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                root = f"/admin/kgs/{command.kg_id}/jobs/{command.restore_job_id}"
                page = await browser.get(root)
                assert page.status_code == 200 and "Discard inactive restore" in page.text
                fields = {
                    "csrf_token": ticket.csrf,
                    "idempotency_key": command.idempotency_key,
                    "expected_published_revision": str(command.expected_published_revision),
                }
                assert (
                    await browser.post(
                        root + "/cleanup-restore", data=fields | {"csrf_token": "wrong"}
                    )
                ).status_code == 403
                queued = await browser.post(root + "/cleanup-restore", data=fields)
                assert queued.status_code == 303
                api = f"/api/v1/kgs/{command.kg_id}/restores/jobs/{command.restore_job_id}/cleanup"
                replay = await browser.post(api, json=command.model_dump(mode="json"))
                assert replay.status_code == 202
                receipt = JobReceipt.model_validate(replay.json())
                assert queued.headers["location"].endswith(str(receipt.job_id))
                assert (
                    await browser.post(
                        api, json=command.model_dump(mode="json") | {"path": "arbitrary"}
                    )
                ).status_code == 422
                fenced = await browser.get(root)
                assert (
                    "View cleanup job" in fenced.text
                    and 'data-action="retry-job"' not in fenced.text
                )
                for role in ("reader", "editor"):
                    denied = await app.state.identity.sessions.create(principals[role], org)
                    browser.cookies.clear()
                    browser.cookies.set("domain_kg_session", denied.session_id)
                    browser.cookies.set("domain_kg_csrf", denied.csrf)
                    browser.headers["x-csrf-token"] = denied.csrf
                    assert (
                        await browser.post(api, json=command.model_dump(mode="json"))
                    ).status_code in (403, 404)
                return receipt
