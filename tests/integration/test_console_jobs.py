"""Browser job controls retain shared current-policy source ownership checks."""

import html
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.materials.models import AddMaterial, RemoveMaterial

pytestmark = pytest.mark.storage


async def test_console_pending_jobs_and_controls_use_current_source_access(
    material_service, team, catalog, issuer_fixture
):
    materials, kg = material_service
    identities, policy, principals, _, org, _ = team
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipt = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg,
            title="Pending source",
            inline_text="Tiny pending source.",
            idempotency_key="console-pending-job",
        ),
    )
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=catalog[0],
        )
        async with app.router.lifespan_context(app):
            reader = await app.state.identity.sessions.create(principals["reader"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": reader.session_id, "domain_kg_csrf": reader.csrf},
            ) as browser:
                listing = await browser.get(f"/admin/kgs/{kg}/jobs")
                assert listing.status_code == 200 and str(receipt.job_id) in listing.text
                assert "Pending source" in listing.text
                api_listing = await browser.get(f"/api/v1/kgs/{kg}/jobs")
                assert api_listing.status_code == 200
                assert api_listing.json()["items"][0]["source"]["title"] == "Pending source"
                path = f"/admin/kgs/{kg}/jobs/{receipt.job_id}"
                job = await browser.get(path)
                assert job.status_code == 200 and 'data-stage="queued"' in job.text
                assert "Pending source" in job.text and 'hx-trigger="every 3s"' in job.text
                assert 'data-action="cancel-job"' not in job.text
                denied = await browser.post(
                    path + "/cancel",
                    headers={"Origin": "http://localhost:8000"},
                    data={"csrf_token": reader.csrf, "idempotency_key": "reader-cancel"},
                )
                assert denied.status_code in (403, 404)
                ticket = await app.state.identity.sessions.create(principals["editor"], org)
                browser.cookies.clear()
                browser.cookies.set("domain_kg_session", ticket.session_id)
                browser.cookies.set("domain_kg_csrf", ticket.csrf)
                controls = await browser.get(path)
                assert 'data-action="cancel-job"' in controls.text
                assert 'data-action="remove-material"' in controls.text
                assert 'name="expected_revision" value="0"' in controls.text
                sources = f"/admin/kgs/{kg}/materials"
                processing = await browser.get(sources, params={"view": "processing"})
                assert processing.status_code == 200 and "Pending source" in processing.text
                assert str(receipt.job_id) not in (await browser.get(sources)).text
                headers = {"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf}
                created = await browser.post(
                    f"/api/v1/kgs/{kg}/import-runs",
                    headers=headers,
                    json={
                        "files": [
                            {
                                "path": "this-selection.txt",
                                "sha256": "a" * 64,
                                "size_bytes": 5,
                                "media_type": "text/plain",
                            }
                        ],
                        "idempotency_key": "separate-current-import",
                    },
                )
                assert created.status_code == 201
                selected = await browser.get(
                    sources,
                    params={
                        "view": "processing",
                        "import_id": created.json()["id"],
                    },
                )
                assert selected.status_code == 200 and "this-selection.txt" not in selected.text
                progress = await browser.get(
                    f"/api/v1/kgs/{kg}/import-runs/{created.json()['id']}"
                )
                assert progress.status_code == 200 and "this-selection.txt" in progress.text
                assert (
                    "Pending source" not in selected.text
                    and str(receipt.job_id) not in selected.text
                )
                assert 'id="job-list"' not in selected.text
                assert 'id="import-rows"' in selected.text
                cancelled = await browser.post(
                    path + "/cancel",
                    headers={"Origin": "http://localhost:8000"},
                    data={"csrf_token": ticket.csrf, "idempotency_key": "editor-cancel"},
                )
                assert cancelled.status_code == 303
                assert 'data-stage="cancelled"' in (await browser.get(path)).text
                assert (
                    str(receipt.job_id)
                    not in (await browser.get(sources, params={"view": "processing"})).text
                )
                assert (
                    str(receipt.job_id)
                    in (await browser.get(sources, params={"view": "attention"})).text
                )
                incomplete = await browser.get(
                    f"/api/v1/kgs/{kg}/jobs",
                    params={"view": "incomplete", "sources_only": "true"},
                )
                assert incomplete.status_code == 200
                assert incomplete.json()["items"][0]["stage"] == "cancelled"
                retried = await browser.post(
                    path + "/retry",
                    headers={"Origin": "http://localhost:8000"},
                    data={"csrf_token": ticket.csrf, "idempotency_key": "editor-retry"},
                )
                assert retried.status_code == 303
                assert 'data-stage="queued"' in (await browser.get(path)).text
                owner = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
                await policy.set_material_acl(
                    owner,
                    kg,
                    receipt.material_id,
                    "restricted",
                    member_ids=(),
                    group_ids=(),
                    idempotency_key="hide-console-job",
                )
                assert (await browser.get(path)).status_code == 404
                assert not (await browser.get(f"/api/v1/kgs/{kg}/jobs")).json()["items"]


async def test_source_pages_keep_limits_filters_and_current_cursor_scope(
    material_service, team, catalog, issuer_fixture
):
    materials, kg = material_service
    identities, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipts = [
        await materials.add(
            access,
            AddMaterial(
                kg_id=kg,
                title=f"Pagination/source-{index}.txt",
                inline_text="Synthetic pagination source.",
                idempotency_key=f"console-page-{index}",
            ),
        )
        for index in range(2)
    ]
    _, state, transport, _ = issuer_fixture
    state["issuer"] = principals["owner"].issuer
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
                path = f"/admin/kgs/{kg}/materials"
                for view in ("processing", "attention", "removed"):
                    if view == "attention":
                        for receipt in receipts:
                            await app.state.services.queue.cancel(
                                access,
                                receipt.job_id,
                                idempotency_key=f"cancel-page-{receipt.job_id}",
                            )
                    elif view == "removed":
                        for receipt in receipts:
                            await app.state.services.lifecycle.remove(
                                access,
                                RemoveMaterial(
                                    kg_id=kg,
                                    material_id=receipt.material_id,
                                    action="exclude",
                                    expected_revision=0,
                                    idempotency_key=f"remove-page-{receipt.material_id}",
                                ),
                            )
                    first = await browser.get(
                        path,
                        params={"view": view, "limit": 1, "include_unverified": "true"},
                    )
                    assert first.status_code == 200
                    first_ids = re.findall(r'data-bulk-id="([^"]+)"', first.text)
                    assert len(first_ids) == 1
                    next_link = re.search(r'rel="next" href="([^"]+)"', first.text)
                    assert next_link
                    next_url = html.unescape(next_link.group(1))
                    params = parse_qs(urlsplit(next_url).query)
                    assert params["view"] == [view]
                    assert params["limit"] == ["1"]
                    assert params["include_unverified"] == ["true"]
                    second = await browser.get(path + next_url)
                    assert second.status_code == 200
                    second_ids = re.findall(r'data-bulk-id="([^"]+)"', second.text)
                    assert len(second_ids) == 1 and first_ids != second_ids
                    assert 'rel="next"' not in second.text
                    first_link = re.search(r'rel="first" href="([^"]+)"', second.text)
                    assert first_link
                    assert "cursor" not in parse_qs(
                        urlsplit(html.unescape(first_link.group(1))).query
                    )
                    changed_limit = await browser.get(
                        path,
                        params={key: values[0] for key, values in params.items()} | {"limit": 2},
                    )
                    assert changed_limit.status_code == 409
                    assert "CURSOR_EXPIRED" in changed_limit.text
                    restart = f"{path}?view={view}&limit=2&include_unverified=true"
                    assert f'href="{restart}"' in html.unescape(changed_limit.text)
                    assert params["cursor"][0] not in changed_limit.text
                invalid = await browser.get(path, params={"limit": 101})
                assert invalid.status_code == 422
