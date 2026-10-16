"""Signed, isolated issuer fixtures and real MongoDB authority tests."""

import asyncio
import json
import time
from uuid import uuid4

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from domain_kg.errors import DomainError
from domain_kg.identity.models import Principal


def test_external_subjects_are_opaque_and_never_trimmed() -> None:
    principal = Principal(issuer="https://issuer.example", subject=" reader ")
    assert principal.subject == " reader "


@pytest.fixture
def issuer_fixture():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private.public_key())) | {
        "kid": "one",
        "alg": "RS256",
        "use": "sig",
    }
    state = {"keys": [jwk], "calls": 0}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/token":
            state["exchange"] = request.content.decode()
            return httpx.Response(200, json={"id_token": state["id_token"]})
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": "https://issuer.example",
                    "jwks_uri": "https://issuer.example/keys",
                    "authorization_endpoint": "https://issuer.example/authorize",
                    "token_endpoint": "https://issuer.example/token",
                },
            )
        assert request.url.path == "/keys"
        state["calls"] += 1
        return httpx.Response(200, json={"keys": state["keys"]})

    def sign(changes=None, *, key=private, kid="one"):
        claims = {
            "iss": "https://issuer.example",
            "sub": "reader",
            "aud": "http://localhost:8000/mcp",
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "scope": "domain_kg:read",
        } | (changes or {})
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": kid})

    return private, state, httpx.MockTransport(handle), sign


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://wrong.example"},
        {"aud": "other-resource"},
        {"exp": 1},
        {"scope": "openid"},
        {"sub": ""},
        {"exp": None},
    ],
)
async def test_oidc_rejects_invalid_claims(issuer_fixture, changes) -> None:
    from domain_kg.identity.oidc import OIDCVerifier

    _, _, transport, sign = issuer_fixture
    async with httpx.AsyncClient(transport=transport) as client:
        verifier = OIDCVerifier("https://issuer.example", "http://localhost:8000/mcp", client)
        with pytest.raises(DomainError):
            await verifier.authenticate_bearer(sign(changes))


async def test_oidc_signature_algorithm_and_key_rotation(issuer_fixture) -> None:
    from domain_kg.identity.oidc import OIDCVerifier

    _, state, transport, sign = issuer_fixture
    async with httpx.AsyncClient(transport=transport) as client:
        verifier = OIDCVerifier("https://issuer.example", "http://localhost:8000/mcp", client)
        principal = await verifier.authenticate_bearer(sign())
        assert principal.subject == "reader" and principal.issuer == "https://issuer.example"
        forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with pytest.raises(DomainError):
            await verifier.authenticate_bearer(sign(key=forged))
        claims = {
            "iss": principal.issuer,
            "sub": "reader",
            "aud": verifier.audience,
            "iat": int(time.time()),
            "exp": int(time.time()) + 300,
            "scope": "domain_kg:read",
        }
        with pytest.raises(DomainError):
            await verifier.authenticate_bearer(
                jwt.encode(claims, "attacker" * 8, algorithm="HS256", headers={"kid": "one"})
            )
        rotated = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        state["keys"] = [
            json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(rotated.public_key()))
            | {"kid": "two", "alg": "RS256", "use": "sig"}
        ]
        assert (
            await verifier.authenticate_bearer(sign(key=rotated, kid="two"))
        ).subject == "reader"
        assert state["calls"] == 2


@pytest.mark.storage
async def test_admission_subject_collisions_suspension_and_sessions(mongo_db) -> None:
    from domain_kg.identity.repository import IdentityRepository
    from domain_kg.identity.sessions import SessionStore

    repo = IdentityRepository(mongo_db)
    await repo.initialize()
    owner = Principal(issuer="https://issuer.example", subject="owner")
    org, admin = await repo.bootstrap_organization(owner, "Team")
    newcomer = Principal(issuer=owner.issuer, subject="new")
    with pytest.raises(DomainError):
        await repo.require_member(newcomer, org)
    member = await repo.admit(admin, newcomer, role="member", idempotency_key="admit-1")
    assert (await repo.require_member(newcomer, org)).id == member.id
    with pytest.raises(DomainError):
        await repo.require_member(Principal(issuer="https://other.example", subject="new"), org)
    sessions = SessionStore(mongo_db, repo)
    await sessions.initialize()
    ticket = await sessions.create(newcomer, org)
    assert (await sessions.authenticate_session(ticket.session_id)).subject == "new"
    with pytest.raises(DomainError):
        await sessions.authenticate_session(ticket.session_id, csrf="forged")
    assert (
        await sessions.authenticate_session(ticket.session_id, csrf=ticket.csrf)
    ).subject == "new"
    await repo.change_member(
        admin,
        member.id,
        expected_revision=member.revision,
        suspended=True,
        idempotency_key="suspend-1",
    )
    with pytest.raises(DomainError):
        await sessions.authenticate_session(ticket.session_id)
    assert await mongo_db.identity_sessions.count_documents({"_id": ticket.session_id}) == 0


@pytest.mark.storage
async def test_concurrent_last_admin_protection_and_idempotency(mongo_db) -> None:
    from domain_kg.identity.repository import IdentityRepository

    repo = IdentityRepository(mongo_db)
    await repo.initialize()
    owner = Principal(issuer="https://issuer.example", subject="owner")
    org, first = await repo.bootstrap_organization(owner, "Team")
    other = Principal(issuer=owner.issuer, subject="second")
    second = await repo.admit(first, other, role="admin", idempotency_key="admit-second")
    replay = await repo.admit(first, other, role="admin", idempotency_key="admit-second")
    assert replay == second
    with pytest.raises(DomainError) as conflict:
        await repo.admit(
            first,
            Principal(issuer=owner.issuer, subject="different"),
            role="admin",
            idempotency_key="admit-second",
        )
    assert conflict.value.code == "IDEMPOTENCY_CONFLICT"
    results = await asyncio.gather(
        *[
            repo.change_member(
                actor, actor.id, expected_revision=0, suspended=True, idempotency_key=str(uuid4())
            )
            for actor in (first, second)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, DomainError) for result in results) == 1
    assert (
        await mongo_db.members.count_documents(
            {"org_id": str(org), "role": "admin", "suspended": False}
        )
        == 1
    )


@pytest.mark.storage
async def test_groups_and_service_identities_are_org_scoped(mongo_db) -> None:
    from domain_kg.identity.repository import IdentityRepository

    repo = IdentityRepository(mongo_db)
    await repo.initialize()
    owner = Principal(issuer="https://issuer.example", subject="owner")
    org, admin = await repo.bootstrap_organization(owner, "Team")
    _, foreign = await repo.bootstrap_organization(
        Principal(issuer=owner.issuer, subject="foreign"), "Other"
    )
    group = await repo.create_group(admin, "Editors", idempotency_key="group-1")
    with pytest.raises(DomainError):
        await repo.set_group_member(
            admin, group.id, foreign.id, present=True, idempotency_key="foreign-member"
        )
    await repo.set_group_member(
        admin, group.id, admin.id, present=True, idempotency_key="local-member"
    )
    kg = uuid4()
    await mongo_db.kgs.insert_one({"id": str(kg), "org_id": str(org)})
    service = await repo.admit(
        admin,
        Principal(issuer=owner.issuer, subject="automation"),
        role="member",
        kind="service",
        kg_ids=(kg,),
        scopes=("domain_kg:read",),
        idempotency_key="service-1",
    )
    with pytest.raises(DomainError):
        await repo.create_group(service, "Forbidden", idempotency_key="service-admin")
    with pytest.raises(DomainError):
        await repo.admit(
            admin,
            Principal(issuer=owner.issuer, subject="unscoped"),
            role="member",
            kind="service",
            idempotency_key="unscoped-service",
        )


@pytest.mark.storage
async def test_http_code_login_pkce_nonce_replay_csrf_and_live_suspension(
    mongo_db, issuer_fixture
) -> None:
    import base64
    import hashlib
    from urllib.parse import parse_qs, urlsplit

    from domain_kg.app import create_app
    from domain_kg.identity.repository import IdentityRepository
    from domain_kg.settings import Settings

    _, state, transport, sign = issuer_fixture
    repo = IdentityRepository(mongo_db)
    await repo.initialize()
    org, admin = await repo.bootstrap_organization(
        Principal(issuer="https://issuer.example", subject="reader"), "Team"
    )
    async with httpx.AsyncClient(transport=transport) as oidc_client:
        app = create_app(
            Settings(
                public_url="http://localhost:8000",
                oidc_issuer="https://issuer.example",
                oidc_client_id="console-client",
            ),
            identity_repository=repo,
            oidc_http_client=oidc_client,
        )
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
            ) as browser:
                login = await browser.get("/auth/login", params={"org_id": str(org)})
                assert login.status_code == 303
                params = parse_qs(urlsplit(login.headers["location"]).query)
                assert params["code_challenge_method"] == ["S256"]
                assert "HttpOnly" in login.headers["set-cookie"]
                state["id_token"] = sign({"aud": "console-client", "nonce": params["nonce"][0]})
                callback = {"code": "provider-code", "state": params["state"][0]}
                accepted = await browser.get("/auth/callback", params=callback)
                assert accepted.status_code == 303
                verifier = parse_qs(state["exchange"])["code_verifier"][0]
                assert (
                    base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
                    .rstrip(b"=")
                    .decode()
                    == params["code_challenge"][0]
                )
                assert (await browser.get("/api/v1/me")).json()["member"]["id"] == str(admin.id)
                assert (await browser.get("/auth/callback", params=callback)).status_code == 401
                assert (await browser.post("/auth/logout")).status_code == 403
                csrf = browser.cookies["domain_kg_csrf"]
                assert (
                    await browser.post(
                        "/auth/logout",
                        headers={"X-CSRF-Token": csrf, "Origin": "https://evil.example"},
                    )
                ).status_code == 403
                assert (
                    await browser.post(
                        "/auth/logout",
                        headers={"X-CSRF-Token": csrf, "Origin": "http://localhost:8000"},
                    )
                ).status_code == 200
                assert (await browser.get("/api/v1/me")).status_code == 401
                response = await browser.get(
                    "/api/v1/me", headers={"Authorization": f"Bearer {sign()}"}
                )
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
                opaque = await browser.get(
                    "/api/v1/me", headers={"Authorization": f"Bearer {sign({'sub': ' reader '})}"}
                )
                assert opaque.status_code == 403
                # Administrative data and mutations use the same current identity.
                headers = {"Authorization": f"Bearer {sign()}"}
                created = await browser.post(
                    "/api/v1/members",
                    headers=headers,
                    json={
                        "issuer": "https://issuer.example",
                        "subject": "invited",
                        "idempotency_key": "http-admit",
                    },
                )
                assert created.status_code == 200
                member_id = created.json()["id"]
                listed = await browser.get("/api/v1/members", headers=headers, params={"limit": 1})
                assert listed.status_code == 200 and listed.json()["next_cursor"]
                cursor = listed.json()["next_cursor"]
                changed = await browser.patch(
                    f"/api/v1/members/{member_id}",
                    headers=headers,
                    json={
                        "expected_revision": 0,
                        "suspended": True,
                        "idempotency_key": "http-suspend",
                    },
                )
                assert changed.status_code == 200
                expired = await browser.get(
                    "/api/v1/members", headers=headers, params={"limit": 1, "cursor": cursor}
                )
                assert expired.status_code == 409 and expired.json()["code"] == "CURSOR_EXPIRED"
                denied = await browser.get(
                    "/api/v1/me", headers={"Authorization": f"Bearer {sign({'sub': 'invited'})}"}
                )
                assert denied.status_code == 403
                injected = await browser.post(
                    "/api/v1/members",
                    headers=headers,
                    json={
                        "issuer": "https://issuer.example",
                        "subject": "forged",
                        "idempotency_key": "http-forged",
                        "org_id": str(uuid4()),
                    },
                )
                assert injected.status_code == 422
                # Consume a fresh login with the wrong nonce; no session is issued.
                login = await browser.get("/auth/login", params={"org_id": str(org)})
                params = parse_qs(urlsplit(login.headers["location"]).query)
                state["id_token"] = sign({"aud": "console-client", "nonce": "wrong"})
                rejected = await browser.get(
                    "/auth/callback", params={"code": "provider-code", "state": params["state"][0]}
                )
                assert rejected.status_code == 401
                assert "domain_kg_session" not in browser.cookies
                # Exercise the real SDK and verifier together, then revoke locally.
                import httpx2
                from mcp import Client
                from mcp.client.streamable_http import streamable_http_client

                async with httpx2.AsyncClient(
                    transport=httpx2.ASGITransport(app=app),
                    base_url="http://localhost:8000",
                    headers={"Authorization": f"Bearer {sign()}"},
                ) as mcp_http:
                    async with Client(
                        streamable_http_client("http://localhost:8000/mcp", http_client=mcp_http)
                    ) as mcp_client:
                        result = await mcp_client.call_tool("domain_kg_probe", {})
                        assert result.structured_content == {"status": "ok"}
                second = await repo.admit(
                    admin,
                    Principal(issuer=admin.issuer, subject="other-admin"),
                    role="admin",
                    idempotency_key="other-admin",
                )
                await repo.change_member(
                    second,
                    admin.id,
                    expected_revision=0,
                    suspended=True,
                    idempotency_key="suspend-reader",
                )
                refused = await browser.post(
                    "/mcp",
                    headers=headers,
                    json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                )
                assert refused.status_code == 401


@pytest.mark.storage
async def test_identity_mongo_shape_constraints(mongo_db) -> None:
    from pymongo.errors import WriteError

    from domain_kg.identity.repository import IdentityRepository

    repo = IdentityRepository(mongo_db)
    await repo.initialize()
    with pytest.raises(WriteError):
        await mongo_db.members.insert_one({"_id": str(uuid4()), "subject": "missing-org"})
    org, _ = await repo.bootstrap_organization(
        Principal(issuer="https://issuer.example", subject="admin"), "Team"
    )
    with pytest.raises(WriteError):
        await mongo_db.organizations.update_one({"_id": str(org)}, {"$set": {"active_admins": 0}})
