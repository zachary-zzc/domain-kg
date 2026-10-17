"""Opt-in, temporary loopback preview over synthetic fixtures, never a production login."""
# ruff: noqa: F811

import asyncio
import os

import httpx
import pytest
import uvicorn
from fastapi.responses import RedirectResponse
from test_curation import alpha_id
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.app import create_app
from domain_kg.settings import Settings

pytestmark = [
    pytest.mark.storage,
    pytest.mark.skipif(
        os.environ.get("DOMAIN_KG_BROWSER_PREVIEW") != "1",
        reason="manual loopback browser preview is opt-in",
    ),
]


async def test_temporary_console_browser_preview(publishing, team, catalog, issuer_fixture):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    identities, _, principals, _, org, _ = team
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            Settings(
                public_url="http://localhost:28080",
                oidc_issuer=state["issuer"],
                oidc_client_id="console",
            ),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=coordinator.providers,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=28080,
                lifespan="off",
                log_level="warning",
                access_log=False,
                timeout_graceful_shutdown=5,
            )
        )

        # These fixture routes exist only in this opt-in pytest process and synthetic namespace.
        @app.get("/__smoke__/start/{role}")
        async def start(role: str):
            assert role in ("owner", "editor", "reader")
            ticket = await app.state.identity.sessions.create(principals[role], org)
            response = RedirectResponse("/admin", status_code=303)
            response.set_cookie(
                "domain_kg_session", ticket.session_id, httponly=True, samesite="lax", path="/"
            )
            response.set_cookie("domain_kg_csrf", ticket.csrf, samesite="lax", path="/")
            return response

        @app.get("/__smoke__/fixture")
        async def fixture_links():
            # Synthetic fixture identifiers only; no source payload, credentials or configuration.
            return {
                "kg_id": str(job.kg_id),
                "material_id": str(source.material_id),
                "node_id": str(alpha),
            }

        @app.get("/__smoke__/finish")
        async def finish():
            server.should_exit = True
            return {"finished": True}

        smoke_routes = app.router.routes[-3:]
        del app.router.routes[-3:]
        app.router.routes[0:0] = smoke_routes
        async with app.router.lifespan_context(app):
            serving = asyncio.create_task(server.serve())
            try:
                for _ in range(100):
                    if server.started:
                        break
                    await asyncio.sleep(0.05)
                assert server.started
                print(
                    "Browser preview ready: http://localhost:28080/__smoke__/start/owner",
                    flush=True,
                )
                await asyncio.wait_for(asyncio.shield(serving), timeout=900)
            finally:
                server.should_exit = True
                await asyncio.wait_for(serving, timeout=10)
