"""Signed fixture credentials use identical current policy over HTTP and MCP."""
# ruff: noqa: F811

import json

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_curation import alpha_id
from test_identity import issuer_fixture as issuer_fixture
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.app import create_app
from domain_kg.identity.models import Action
from domain_kg.identity.routes import IdentityRuntime
from domain_kg.revisions.models import NodeChanges, NodeUpdate
from domain_kg.settings import Settings

pytestmark = pytest.mark.storage


def settings_for(issuer):
    return Settings(
        public_url="http://localhost:8000", oidc_issuer=issuer, oidc_client_id="console"
    )


async def test_verified_multi_org_credential_requires_selected_current_membership(
    team, catalog, issuer_fixture
):
    identities, _, principals, members, org, _ = team
    _, state, transport, sign = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    other, _ = await identities.bootstrap_organization(principals["reader"], "Other team")
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        identity = IdentityRuntime(settings_for(state["issuer"]), identities, oidc_client)
        token = sign()
        # Authentication verifies an active credential, while handlers select and authorize a KG.
        verified = await identity.verify_token(token)
        assert verified is not None and verified.subject == "reader"
        assert verified.claims == {"iss": state["issuer"], "sub": "reader"}
        for selected in (org, other):
            member = await identities.require_member(principals["reader"], selected)
            assert member.org_id == selected
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc_client,
            catalog_service=catalog[0],
        )
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Authorization": f"Bearer {token}"},
            ) as wire:
                async with Client(
                    streamable_http_client("http://localhost:8000/mcp", http_client=wire)
                ) as mcp:
                    selected = await mcp.call_tool(
                        "domain_kg_get_kg", {"kg_id": str(team[5]), "org_id": str(org)}
                    )
                    assert not selected.is_error and selected.structured_content["id"] == str(
                        team[5]
                    )
                    foreign = await mcp.call_tool(
                        "domain_kg_get_kg", {"kg_id": str(team[5]), "org_id": str(other)}
                    )
                    assert (
                        foreign.is_error
                        and foreign.structured_content["code"] == "RESOURCE_NOT_ACCESSIBLE"
                    )
                    ambiguous = await mcp.call_tool("domain_kg_list_kgs", {})
                    assert (
                        ambiguous.is_error and ambiguous.structured_content["code"] == "FORBIDDEN"
                    )
        await identities.database.members.update_one(
            {"_id": str(members["reader"].id)}, {"$set": {"suspended": True}}
        )
        assert await identity.verify_token(token) is not None
        await identities.database.members.update_many(
            {"issuer": principals["reader"].issuer, "subject": "reader"},
            {"$set": {"suspended": True}},
        )
        assert await identity.verify_token(token) is None


async def test_http_mcp_role_source_and_job_equivalence(publishing, catalog, team, issuer_fixture):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    kg = job.kg_id
    alpha = await alpha_id(queue, editor, kg)
    identities, policy, principals, members, org, _ = team
    _, state, transport, sign = issuer_fixture
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
            headers = {"Authorization": f"Bearer {sign()}", "x-domain-kg-organization": str(org)}
            async with (
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                    headers=headers,
                ) as http,
                httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                    headers=headers,
                ) as wire,
                Client(
                    streamable_http_client("http://localhost:8000/mcp", http_client=wire)
                ) as mcp,
            ):
                tools = await mcp.list_tools()
                assert {"domain_kg_search", "domain_kg_add_folder", "domain_kg_batch"} <= {
                    tool.name for tool in tools.tools
                }
                args = {"kg_id": str(kg), "entity_id": str(alpha), "org_id": str(org)}
                node = await http.get(f"/api/v1/kgs/{kg}/nodes/{alpha}")
                result = await mcp.call_tool("domain_kg_get_node", args)
                assert node.status_code == 200, node.text
                assert not result.is_error, result
                assert result.structured_content == node.json()
                query = {"kg_ids": [str(kg)], "query": "Alpha", "mode": "lexical"}
                search = await http.post("/api/v1/search", json=query)
                recalled = await mcp.call_tool(
                    "domain_kg_search", {"command": query, "org_id": str(org)}
                )
                assert search.status_code == 200 and not recalled.is_error
                assert recalled.structured_content["results"] == search.json()["results"]
                assert search.json()["results"]
                detail = await http.get(f"/api/v1/kgs/{kg}/materials/{source.material_id}")
                inspected = await mcp.call_tool(
                    "domain_kg_get_material",
                    {"kg_id": str(kg), "material_id": str(source.material_id), "org_id": str(org)},
                )
                assert detail.status_code == 200 and not inspected.is_error
                assert (
                    inspected.structured_content["material"]["id"]
                    == detail.json()["material"]["id"]
                )
                templates = await mcp.list_resource_templates()
                assert len(templates.resource_templates) == 2
                uri = f"domain-kg://{org}/{kg}/materials/{source.material_id}"
                resource = await mcp.read_resource(uri)
                assert json.loads(resource.contents[0].text) == detail.json()
                citation_id = search.json()["results"][0]["citation_ids"][0]
                citation = await http.get(
                    f"/api/v1/citations/{citation_id}", params={"kg_id": str(kg)}
                )
                assert citation.status_code == 200 and citation.json()["spans"]
                command = NodeUpdate(
                    kg_id=kg,
                    entity_id=alpha,
                    expected_revision=0,
                    curation_scope="source",
                    source_ids=(source.material_id,),
                    changes=NodeChanges(name="Reviewed Alpha"),
                    reason="Fixture review",
                    idempotency_key="transport-node",
                ).model_dump(mode="json")
                denied = await http.patch(f"/api/v1/kgs/{kg}/nodes/{alpha}", json=command)
                denied_tool = await mcp.call_tool(
                    "domain_kg_update_node", {"command": command, "org_id": str(org)}
                )
                assert denied.status_code == 403
                assert (
                    denied_tool.is_error and denied_tool.structured_content["code"] == "FORBIDDEN"
                )
                owner_token = sign(
                    {"sub": "owner", "scope": "domain_kg:read domain_kg:write domain_kg:admin"}
                )
                owner = {"Authorization": f"Bearer {owner_token}"}
                accepted = await http.patch(
                    f"/api/v1/kgs/{kg}/nodes/{alpha}", json=command, headers=owner
                )
                assert accepted.status_code == 202 and accepted.json()["job_id"], accepted.text
                job_result = await mcp.call_tool(
                    "domain_kg_get_job",
                    {"kg_id": str(kg), "job_id": accepted.json()["job_id"], "org_id": str(org)},
                )
                job_http = await http.get(f"/api/v1/kgs/{kg}/jobs/{accepted.json()['job_id']}")
                assert job_http.status_code == 200 and not job_result.is_error
                assert job_result.structured_content == job_http.json()
                owner_access = await policy.resolve_access(
                    principals["owner"], [kg], Action.MANAGE_GRANTS
                )
                await policy.set_material_acl(
                    owner_access,
                    kg,
                    source.material_id,
                    "restricted",
                    member_ids=(members["editor"].id,),
                    group_ids=(),
                    idempotency_key="transport-restrict-source",
                )
                assert (await http.get(f"/api/v1/kgs/{kg}/nodes/{alpha}")).status_code == 404
                private_tool = await mcp.call_tool("domain_kg_get_node", args)
                assert private_tool.is_error
                private_citation = await http.get(
                    f"/api/v1/citations/{citation_id}", params={"kg_id": str(kg)}
                )
                assert private_citation.status_code == 404
                with pytest.raises(Exception) as resource_denied:
                    await mcp.read_resource(uri)
                assert "RESOURCE_NOT_ACCESSIBLE" in str(resource_denied.value)
                owner_access = await policy.resolve_access(
                    principals["owner"], [kg], Action.MANAGE_GRANTS
                )
                await policy.set_grant(
                    owner_access,
                    kg,
                    "member",
                    members["reader"].id,
                    None,
                    idempotency_key="transport-revoke",
                )
                hidden = await http.get(f"/api/v1/kgs/{kg}/nodes/{alpha}")
                hidden_tool = await mcp.call_tool("domain_kg_get_node", args)
                assert hidden.status_code == 404
                assert (
                    hidden_tool.is_error
                    and hidden_tool.structured_content["code"] == "RESOURCE_NOT_ACCESSIBLE"
                )
