"""Human identity and visibility previews use typed, scoped application commands."""
# ruff: noqa: F811

import httpx
import pytest
from test_curation import alpha_id, publish_curation
from test_identity import issuer_fixture as issuer_fixture  # noqa: F401
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.models import RevisionReceipt

pytestmark = pytest.mark.storage


async def test_relation_edit_merge_split_and_suppression_restore_with_no_model_work(
    publishing, team, catalog, issuer_fixture, monkeypatch
):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    alpha = await alpha_id(queue, editor, job.kg_id)
    from domain_kg.graph.service import GraphService

    graph = GraphService(queue)
    scope = await graph.resolver.resolve(
        editor, SearchRequest(kg_ids=(job.kg_id,), query="Beta"), job.kg_id
    )
    data = await graph.repository.open(scope)
    beta = next(item.entity.id for item in data.observations if item.name == "Beta")
    assertion = data.supports[0].claim.assertion.id

    async def forbidden(*args, **kwargs):
        pytest.fail("identity, visibility and relation tags require no model work")

    monkeypatch.setattr(coordinator.providers, "extract", forbidden)
    monkeypatch.setattr(coordinator.providers, "embeddings", forbidden)
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
                common = {
                    "csrf_token": ticket.csrf,
                    "expected_revision": "0",
                    "curation_scope": "source",
                    "source_ids": str(source.material_id),
                    "reason": "Review fixture",
                }
                relation = f"{root}/relations/{assertion}/edit"
                assert (await browser.get(relation)).status_code == 200
                edited = await browser.post(
                    relation,
                    data=common | {"tags": "reviewed", "idempotency_key": "console-relation-tags"},
                )
                assert edited.status_code == 303

                async def publish(key):
                    row = await queue.database.curation_commands.find_one(
                        {"org_id": str(org), "kg_id": str(job.kg_id), "key": key}
                    )
                    await publish_curation(
                        queue, coordinator, editor, RevisionReceipt.model_validate(row["receipt"])
                    )

                await publish("console-relation-tags")
                action_url = f"{root}/nodes/{alpha}/curate"
                merge = common | {
                    "action": "merge",
                    "member_ids": f"{alpha},{beta}",
                    "phase": "preview",
                    "idempotency_key": "console-merge",
                }
                before = await queue.database.jobs.count_documents({"kg_id": str(job.kg_id)})
                preview = await browser.post(action_url, data=merge)
                assert preview.status_code == 200 and "Review merge" in preview.text
                assert 'name="member_revisions"' in preview.text
                assert (
                    await queue.database.jobs.count_documents({"kg_id": str(job.kg_id)}) == before
                )
                applied = await browser.post(
                    action_url, data=merge | {"phase": "apply", "member_revisions": f"{beta}:0"}
                )
                assert applied.status_code == 303
                await publish("console-merge")
                assert (await graph.get_node(editor, job.kg_id, beta)).id == alpha
                split = common | {
                    "expected_revision": "1",
                    "action": "split",
                    "partitions": f"{alpha}={alpha}\n{beta}={beta}",
                    "member_revisions": f"{beta}:1",
                    "phase": "preview",
                    "idempotency_key": "console-split",
                }
                assert (await browser.post(action_url, data=split)).status_code == 200
                assert (
                    await browser.post(action_url, data=split | {"phase": "apply"})
                ).status_code == 303
                await publish("console-split")
                assert (await graph.get_node(editor, job.kg_id, beta)).id == beta
                suppress = common | {
                    "expected_revision": "2",
                    "action": "suppress",
                    "phase": "preview",
                    "idempotency_key": "console-suppress",
                }
                assert (await browser.post(action_url, data=suppress)).status_code == 200
                result = await browser.post(action_url, data=suppress | {"phase": "apply"})
                assert (
                    result.status_code == 303
                    and "include_suppressed=true" in result.headers["location"]
                )
                assert (await browser.get(f"{root}/nodes/{alpha}")).status_code == 404
                history = await browser.get(result.headers["location"])
                assert history.status_code == 200 and "Restore node visibility" in history.text
                restore = common | {
                    "expected_revision": "3",
                    "action": "restore",
                    "phase": "apply",
                    "idempotency_key": "console-node-restore",
                }
                assert (await browser.post(action_url, data=restore)).status_code == 303
                assert (await browser.get(f"{root}/nodes/{alpha}")).status_code == 200
