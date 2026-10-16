"""Owner repair and operator infrastructure privileges never imply one another."""
# ruff: noqa: F811

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.catalog.backends import invite_operator
from domain_kg.identity.models import Action

pytestmark = pytest.mark.storage


async def test_owner_readiness_repair_and_operator_health_without_kg_access(
    publishing, team, catalog, issuer_fixture
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    identities, _, principals, _, org, _ = team
    await invite_operator(queue.database, principals["denied"].issuer, principals["denied"].subject)
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
            ticket = await app.state.identity.sessions.create(principals["owner"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Origin": "http://localhost:8000"},
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                operations = f"/admin/kgs/{job.kg_id}/operations"
                status = await browser.get(operations)
                assert status.status_code == 200 and "Graph ready" in status.text
                assert "Vector ready" in status.text and "Published revision 1" in status.text
                repair = await browser.post(
                    f"{operations}/repair",
                    data={
                        "csrf_token": ticket.csrf,
                        "material_id": str(source.material_id),
                        "idempotency_key": "console-owner-repair",
                    },
                )
                assert repair.status_code == 303 and "/jobs/" in repair.headers["location"]
                reader = await app.state.identity.sessions.create(principals["reader"], org)
                browser.cookies.set("domain_kg_session", reader.session_id)
                browser.cookies.set("domain_kg_csrf", reader.csrf)
                assert (await browser.get(operations)).status_code in (403, 404)
                assert (await browser.get("/admin/infrastructure")).status_code in (403, 404)
                operator = await app.state.identity.sessions.create(principals["denied"], org)
                browser.cookies.set("domain_kg_session", operator.session_id)
                browser.cookies.set("domain_kg_csrf", operator.csrf)
                inventory = await browser.get("/admin/infrastructure")
                assert inventory.status_code == 200 and "Registered backends" in inventory.text
                assert (
                    "Publication fixture" not in inventory.text
                    and "localhost:" not in inventory.text
                )
                records = await catalog[0].registry.list_backends(
                    await queue.policy.resolve_access(
                        principals["denied"],
                        [],
                        Action.REGISTER_BACKEND,
                    )
                )
                backend = next(row for row in records if row.kind == "mongo")
                health = await browser.post(
                    f"/admin/infrastructure/{backend.backend_id}/validate",
                    data={"csrf_token": operator.csrf},
                )
                assert health.status_code == 200 and "healthy" in health.text
                assert "replica_set" in health.text
                denied = await browser.get(f"/admin/kgs/{job.kg_id}/search", params={"q": "Alpha"})
                assert denied.status_code == 404 and "Alpha" not in denied.text
