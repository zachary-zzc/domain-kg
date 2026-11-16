"""The browser console uses current server-side sessions and the shared services."""
# ruff: noqa: F811

import html
import re
from hashlib import sha256

import httpx
import pytest
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.materials.import_runs import CreateImportRun, ImportFile, ImportRunSubmission
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.models import AddMaterial, RemoveMaterial

pytestmark = pytest.mark.storage


async def test_console_reader_search_citation_and_forged_write_denial(
    publishing, team, catalog, issuer_fixture
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
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
            ticket = await app.state.identity.sessions.create(principals["reader"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                page = await browser.get(f"/admin/kgs/{job.kg_id}/search", params={"q": "Alpha"})
                assert page.status_code == 200 and "Alpha" in page.text
                assert "/admin/citations/" in page.text
                link = re.search(r'href="(/admin/citations/[^\"]+)"', page.text)
                assert link
                cited = await browser.get(html.unescape(link.group(1)))
                assert cited.status_code == 200 and "Alpha" in cited.text
                listing = await browser.get(f"/admin/kgs/{job.kg_id}/materials")
                assert listing.status_code == 200 and "Publication fixture" in listing.text
                detail = await browser.get(f"/admin/kgs/{job.kg_id}/materials/{source.material_id}")
                assert detail.status_code == 200
                assert 'data-action="remove-material"' not in detail.text
                forged = await browser.post(
                    f"/admin/kgs/{job.kg_id}/materials/{source.material_id}/remove",
                    headers={"Origin": "http://localhost:8000"},
                    data={
                        "csrf_token": ticket.csrf,
                        "action": "delete",
                        "expected_revision": "1",
                        "idempotency_key": "reader-console-remove",
                    },
                )
                api_denied = await browser.post(
                    f"/api/v1/kgs/{job.kg_id}/materials/{source.material_id}/remove",
                    headers={"Origin": "http://localhost:8000", "x-csrf-token": ticket.csrf},
                    json={
                        "kg_id": str(job.kg_id),
                        "material_id": str(source.material_id),
                        "action": "delete",
                        "expected_revision": 1,
                        "idempotency_key": "reader-console-remove",
                    },
                )
                assert forged.status_code == api_denied.status_code in (403, 404)
                assert api_denied.json()["code"] in forged.text
                assert await queue.policy.material_visible(editor, job.kg_id, source.material_id)
                owner = await queue.policy.resolve_access(
                    principals["owner"], [job.kg_id], Action.MANAGE_GRANTS
                )
                await queue.policy.set_material_acl(
                    owner,
                    job.kg_id,
                    source.material_id,
                    "restricted",
                    member_ids=(),
                    group_ids=(),
                    idempotency_key="restrict-console-source",
                )
                denied = await browser.get(f"/admin/kgs/{job.kg_id}/materials/{source.material_id}")
                assert denied.status_code == 404 and "Publication fixture" not in denied.text


async def test_source_withdrawal_after_search_fences_rendered_evidence(
    publishing, team, catalog, issuer_fixture, monkeypatch
):
    materials, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
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
            original = app.state.services.search.search

            async def withdrawn(access, command):
                result = await original(access, command)
                await MaterialLifecycle(materials, queue).remove(
                    editor,
                    RemoveMaterial(
                        kg_id=job.kg_id,
                        material_id=source.material_id,
                        action="exclude",
                        expected_revision=1,
                        idempotency_key="withdraw-after-console-search",
                    ),
                )
                return result

            monkeypatch.setattr(app.state.services.search, "search", withdrawn)
            ticket = await app.state.identity.sessions.create(principals["reader"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                result = await browser.get(f"/admin/kgs/{job.kg_id}/search", params={"q": "Alpha"})
                assert result.status_code == 409 and "POLICY_CHANGED" in result.text
                assert "Alpha" not in result.text


@pytest.mark.parametrize(
    ("view", "withdrawal"),
    [
        ("processing", "remove"),
        ("attention", "remove"),
        ("saved", "remove"),
        ("saved", "acl"),
        ("published", None),
    ],
)
async def test_source_monitoring_allows_publication_but_fences_late_source_withdrawal(
    publishing, team, catalog, issuer_fixture, monkeypatch, view, withdrawal
):
    materials, editor, queue, coordinator, _, _ = publishing
    _, advancing_job, checkpoint = await prepare(publishing, key="advance-during-monitor-render")
    publication = await ready(publishing, advancing_job, checkpoint)
    title, text = "live-monitor-unique.txt", "Synthetic monitoring fixture."
    monitor = await materials.add(
        editor,
        AddMaterial(
            kg_id=advancing_job.kg_id,
            title=title,
            source="folder:" + title,
            inline_text=text,
            idempotency_key="monitor-during-publication",
        ),
    )
    if view == "attention":
        await queue.cancel(editor, monitor.job_id, idempotency_key="monitor-needs-attention")
    identities, policy, principals, _, org, _ = team
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
            runtime = app.state.services
            params = {"view": "processing" if view == "saved" else view}
            if view == "saved":
                run = await runtime.import_runs.create(
                    editor,
                    monitor.kg_id,
                    CreateImportRun(
                        files=(
                            ImportFile(
                                path=title,
                                sha256=sha256(text.encode()).hexdigest(),
                                size_bytes=len(text.encode()),
                                media_type="text/plain",
                            ),
                        ),
                        idempotency_key="monitor-saved-import",
                    ),
                )
                await runtime.import_runs.submit(
                    editor,
                    monitor.kg_id,
                    run.id,
                    0,
                    ImportRunSubmission(job_id=monitor.job_id),
                )
                params["import_id"] = str(run.id)
            original = runtime.catalog.get_kg
            advanced = False
            full_import_read = runtime.import_runs.get

            async def no_progress_read_for_html(*args, **kwargs):
                raise AssertionError("Saved-run HTML must load only its actor-owned head.")

            if view == "saved":
                monkeypatch.setattr(runtime.import_runs, "get", no_progress_read_for_html)

            async def publish_after_metadata_read(access, kg):
                nonlocal advanced
                value = await original(access, kg)
                if not advanced:
                    advanced = True
                    await coordinator.publish(editor, publication)
                return value

            monkeypatch.setattr(runtime.catalog, "get_kg", publish_after_metadata_read)
            ticket = await app.state.identity.sessions.create(principals["editor"], org)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                cookies={"domain_kg_session": ticket.session_id, "domain_kg_csrf": ticket.csrf},
            ) as browser:
                path = f"/admin/kgs/{monitor.kg_id}/materials"
                result = await browser.get(path, params=params)
                assert advanced
                if view == "published":
                    assert result.status_code == 409 and "POLICY_CHANGED" in result.text
                    return
                assert result.status_code == 200
                if view == "saved":
                    assert title not in result.text and str(monitor.job_id) not in result.text
                    monkeypatch.setattr(runtime.import_runs, "get", full_import_read)
                    progress_path = f"/api/v1/kgs/{monitor.kg_id}/import-runs/{run.id}"
                    progress = await browser.get(progress_path)
                    assert progress.status_code == 200 and title in progress.text
                    other_actor = await app.state.identity.sessions.create(principals["owner"], org)
                    async with httpx.AsyncClient(
                        transport=httpx.ASGITransport(app=app),
                        base_url="http://localhost:8000",
                        cookies={
                            "domain_kg_session": other_actor.session_id,
                            "domain_kg_csrf": other_actor.csrf,
                        },
                    ) as foreign:
                        hidden = await foreign.get(path, params=params)
                        assert hidden.status_code == 404 and title not in hidden.text
                else:
                    assert title in result.text

                async def withdraw_after_metadata_read(access, kg):
                    value = await original(access, kg)
                    if withdrawal == "acl":
                        owner = await policy.resolve_access(
                            principals["owner"], [kg], Action.MANAGE_GRANTS
                        )
                        await policy.set_material_acl(
                            owner,
                            kg,
                            monitor.material_id,
                            "restricted",
                            member_ids=(),
                            group_ids=(),
                            idempotency_key="restrict-after-monitor-read",
                        )
                    else:
                        await runtime.lifecycle.remove(
                            editor,
                            RemoveMaterial(
                                kg_id=kg,
                                material_id=monitor.material_id,
                                action="exclude",
                                expected_revision=0,
                                idempotency_key="remove-after-monitor-read",
                            ),
                        )
                    return value

                monkeypatch.setattr(runtime.catalog, "get_kg", withdraw_after_metadata_read)
                denied = await browser.get(path, params=params)
                assert title not in denied.text
                if view == "saved":
                    assert denied.status_code in (200, 403, 404, 409)
                    progress = await browser.get(progress_path)
                    assert progress.status_code in (403, 404, 409) and title not in progress.text
                else:
                    assert denied.status_code in (403, 404, 409)
