"""Trusted platforms provision isolated members and delegate through the actual HTTP/MCP wire."""

# ruff: noqa: F811
import asyncio
import base64
import hashlib
import json
import time
from contextlib import asynccontextmanager
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import httpx2
import jwt
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_identity import issuer_fixture as issuer_fixture

from domain_kg.app import create_app
from domain_kg.settings import Settings

pytestmark = pytest.mark.storage


@pytest.fixture
async def bridge(material_service, catalog, team, issuer_fixture, tmp_path, request):
    _, kg = material_service
    _, state, transport, sign = issuer_fixture
    state["issuer"] = team[2]["owner"].issuer
    profile_id = uuid4()
    profile = {
        "id": str(profile_id),
        "name": "Synthetic platform",
        "allowed_org_ids": [str(team[4])],
        "caller_issuer": state["issuer"],
        "caller_audience": "https://kg.example/identity-bridge",
        "caller_client_id": "platform-server",
        "caller_subject": "platform-service",
        "subject_issuer": state["issuer"],
        "subject_audience": "platform-app",
        "subject_client_id": "platform-app",
        "exchange_client_id": "kg-exchanger",
        "exchange_client_secret": "synthetic-exchange-secret",
    }
    options = getattr(request, "param", {})
    profile.update(options.get("profile", {}))
    profiles = [profile]
    for number in range(1, options.get("extra_profiles", 0) + 1):
        profiles.append(
            profile
            | {
                "id": str(uuid4()),
                "name": f"Synthetic platform {number}",
                "caller_client_id": f"platform-server-{number}",
                "caller_subject": f"platform-service-{number}",
                "exchange_client_id": f"kg-exchanger-{number}",
            }
        )
    path = tmp_path / "bridge.json"
    path.write_text(
        json.dumps(
            {"response_key": base64.urlsafe_b64encode(b"k" * 32).decode(), "profiles": profiles}
        )
    )
    path.chmod(0o600)
    state["broker_calls"] = 0
    state["broker_fail"] = False
    state["sign"] = sign
    state["kg"] = kg
    state["profile"] = profile
    state["profiles"] = profiles
    state["profile_path"] = path

    def respond(request):
        if request.url.path != "/token":
            return transport.handler(request)
        state["broker_calls"] += 1
        if state["broker_fail"]:
            return httpx.Response(503, json={"private": "secret error"})
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:token-exchange"]
        credentials = base64.b64decode(request.headers["authorization"].split()[1]).decode()
        exchange_client = credentials.partition(":")[0]
        assert exchange_client in {item["exchange_client_id"] for item in profiles}
        source = jwt.decode(form["subject_token"][0], options={"verify_signature": False})
        token = sign(
            {
                "sub": state.get("canonical_subject", source["sub"]),
                "azp": exchange_client,
                "jti": str(uuid4()),
                "scope": form.get("scope", ["domain_kg:read"])[0],
            }
        )
        state["last_token"] = token
        return httpx.Response(
            200, json={"access_token": token, "token_type": "Bearer", "expires_in": 300}
        )

    oidc = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    settings = Settings(
        public_url="http://localhost:8000",
        oidc_issuer=state["issuer"],
        oidc_client_id="console",
        identity_bridge_config=path,
    )
    app = create_app(
        settings, identity_repository=team[0], catalog_service=catalog[0], oidc_http_client=oidc
    )

    @asynccontextmanager
    async def connect(integration_options=None):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={
                    "Authorization": "Bearer "
                    + sign(
                        {"sub": "owner", "scope": "domain_kg:read domain_kg:write domain_kg:admin"}
                    )
                },
            ) as http:
                created = await http.post(
                    "/api/v1/identity-integrations",
                    json={
                        "profile_id": str(profile_id),
                        "name": "Trusted test platform",
                        "allowed_kg_ids": [str(kg)],
                        "allowed_actions": [
                            "read",
                            "search",
                            "ingest",
                            "replace",
                            "remove",
                            "curate",
                        ],
                        "default_grants": [{"kg_id": str(kg), "role": "editor"}],
                        "auto_approve": True,
                        "idempotency_key": "create-integration",
                        **options.get("integration", {}),
                        **(integration_options or {}),
                    },
                )
                assert created.status_code == 201, created.text
                integration = created.json()["id"]
                caller = sign(
                    {
                        "sub": "platform-service",
                        "azp": "platform-server",
                        "aud": profile["caller_audience"],
                        "scope": "identity:provision identity:exchange identity:sync",
                    }
                )

                def assertion(subject="new-user", **changes):
                    return sign(
                        {
                            "sub": subject,
                            "aud": "platform-app",
                            "azp": "platform-app",
                            "jti": str(uuid4()),
                            "exp": int(time.time()) + 90,
                            **changes,
                        }
                    )

                async def boot(subject="new-user", key=None, token=None):
                    return await http.post(
                        "/api/v1/identity-bridge/bootstrap",
                        headers={
                            "Authorization": "Bearer " + caller,
                            "Idempotency-Key": key or str(uuid4()),
                        },
                        json={
                            "integration_id": integration,
                            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                            "subject_token": token or assertion(subject),
                        },
                    )

                async def exchange(subject="new-user", actions=None, key=None, token=None):
                    return await http.post(
                        "/api/v1/identity-bridge/token",
                        headers={
                            "Authorization": "Bearer " + caller,
                            "Idempotency-Key": key or str(uuid4()),
                        },
                        json={
                            "integration_id": integration,
                            "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                            "subject_token": token or assertion(subject),
                            "kg_ids": [str(kg)],
                            "actions": actions or ["read", "search"],
                            "connection_seconds": 3600,
                        },
                    )

                yield http, app, boot, exchange, assertion, caller, integration, state

    yield connect
    await oidc.aclose()


async def test_bridge_concurrent_bootstrap_idempotency_and_mcp_revocation(bridge, team):
    async with bridge() as (http, app, boot, exchange, assertion, caller, integration, state):
        first, second = await asyncio.gather(boot(), boot())
        assert first.status_code == 200 and second.status_code == 200
        assert first.json()["member_id"] == second.json()["member_id"]
        assert (
            await team[0].database.members.count_documents(
                {"org_id": str(team[4]), "subject": "new-user"}
            )
            == 1
        )
        subject = assertion()
        key = "one-exchange"
        token = await exchange(token=subject, key=key)
        assert token.status_code == 200, token.text
        repeated = await exchange(token=subject, key=key)
        assert repeated.json() == token.json()
        replay = await exchange(token=subject, key="other-key")
        assert replay.status_code == 409
        access = token.json()["access_token"]
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://localhost:8000",
            headers={"Authorization": "Bearer " + access},
        ) as wire:
            async with Client(
                streamable_http_client("http://localhost:8000/mcp", http_client=wire)
            ) as client:
                listed = await client.call_tool("domain_kg_list_kgs", {})
                assert not listed.is_error and len(listed.structured_content["items"]) == 1
                denied = await client.call_tool(
                    "domain_kg_add_material",
                    {
                        "command": {
                            "kg_id": str(team[5]),
                            "title": "No",
                            "inline_text": "No",
                            "idempotency_key": "deny",
                        }
                    },
                )
                assert denied.is_error
                mine = await http.get(
                    "/api/v1/me/connections", headers={"Authorization": "Bearer " + access}
                )
                assert mine.status_code == 200 and mine.json()["items"]
                revoked = await http.post(
                    "/api/v1/me/connections/" + token.json()["connection_id"] + "/revoke",
                    headers={"Authorization": "Bearer " + access},
                    json={"expected_revision": 0, "idempotency_key": "revoke"},
                )
                assert revoked.status_code == 200
                assert (
                    await http.get("/api/v1/me", headers={"Authorization": "Bearer " + access})
                ).status_code == 401
        raw = await team[0].database.identity_bridge_intents.find_one({"operation": "token"})
        assert access not in json.dumps(raw, default=str)


async def test_bridge_denies_forgery_replay_wrong_client_and_broker_failure(bridge, team):
    async with bridge() as (http, app, boot, exchange, assertion, caller, integration, state):
        wrong = await boot(token=assertion(aud="other"))
        assert wrong.status_code == 401
        wrong = await boot(token=assertion(azp="other"))
        assert wrong.status_code == 401
        wrong = await http.post(
            "/api/v1/identity-bridge/bootstrap",
            headers={"Authorization": ""},
            json={
                "integration_id": integration,
                "subject_token": assertion(),
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
            },
        )
        assert wrong.status_code == 401
        state["broker_fail"] = True
        token = assertion()
        failed = await boot(token=token, key="recoverable")
        assert failed.status_code == 503 and "secret error" not in failed.text
        assert await team[0].database.members.count_documents({"subject": "new-user"}) == 0
        state["broker_fail"] = False
        assert (await boot(token=token, key="recoverable")).status_code == 200
        assert (await boot(token=token, key="replay")).status_code == 409
        issued = await exchange(actions=["read", "search", "ingest"])
        assert issued.status_code == 200
        access = issued.json()["access_token"]
        pos = access.rfind(".") + 10
        tampered = access[:pos] + ("x" if access[pos] != "x" else "z") + access[pos + 1 :]
        assert (
            await http.get("/api/v1/me", headers={"Authorization": "Bearer " + tampered})
        ).status_code == 401
        unknown = state["last_token"]
        # A valid token from the registered exchange client without a binding cannot bypass scopes.
        assert unknown == access
        await team[0].database.mcp_token_bindings.update_one(
            {"token_hash": hashlib.sha256(access.encode()).hexdigest()},
            {"$set": {"connection_id": str(uuid4())}},
        )
        assert (
            await http.get("/api/v1/me", headers={"Authorization": "Bearer " + access})
        ).status_code == 401
