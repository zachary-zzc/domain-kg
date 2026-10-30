"""Private configuration for a separate, loopback-only Keycloak development realm."""

import json
import os
import secrets
import stat
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import httpx
from pydantic import TypeAdapter

from domain_kg.identity.oidc import OIDCVerifier

if TYPE_CHECKING:
    from fastapi import FastAPI

    from domain_kg.identity.models import Principal

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / "secrets/local-browser"
ISSUER = "http://127.0.0.1:18080/realms/domain-kg"
ORIGIN = "http://127.0.0.1:8000"
SCOPES = ("domain_kg:read", "domain_kg:write")


def write_private(path: Path, value: str) -> None:
    """Atomically replace our regular private file, without following symlinks."""
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValueError("Refusing to overwrite a non-private file.")
    temporary = path.with_name(path.name + "." + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def prepare() -> dict[str, str]:
    PRIVATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if PRIVATE.is_symlink() or PRIVATE.stat().st_mode & 0o077:
        raise ValueError("Local browser configuration requires an owner-only directory.")
    identity_path = PRIVATE / "identity.json"
    if identity_path.exists():
        return TypeAdapter(dict[str, str]).validate_json(identity_path.read_text())
    identity = {
        "owner_subject": str(uuid4()),
        "service_subject": str(uuid4()),
        "password": secrets.token_urlsafe(24),
        "client_secret": secrets.token_urlsafe(32),
        "placement_id": str(uuid4()),
    }
    realm = {
        "realm": "domain-kg",
        "enabled": True,
        "sslRequired": "none",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "accessTokenLifespan": 300,
        "clients": [
            {
                "clientId": "domain-kg-console",
                "enabled": True,
                "publicClient": True,
                "protocol": "openid-connect",
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "redirectUris": [ORIGIN + "/auth/callback"],
                "webOrigins": [ORIGIN],
                "attributes": {"pkce.code.challenge.method": "S256"},
            },
            {
                "clientId": "domain-kg-mcp-test",
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": False,
                "secret": identity["client_secret"],
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "defaultClientScopes": list(SCOPES),
                "protocolMappers": [
                    {
                        "name": "domain-kg-resource",
                        "protocol": "openid-connect",
                        "protocolMapper": "oidc-audience-mapper",
                        "config": {
                            "included.custom.audience": ORIGIN + "/mcp",
                            "access.token.claim": "true",
                            "id.token.claim": "false",
                        },
                    }
                ],
            },
        ],
        "clientScopes": [
            {
                "name": scope,
                "protocol": "openid-connect",
                "attributes": {
                    "include.in.token.scope": "true",
                    "display.on.consent.screen": "true",
                },
            }
            for scope in SCOPES
        ],
        "users": [
            {
                "id": identity["owner_subject"],
                "username": "owner",
                "enabled": True,
                "emailVerified": True,
                "firstName": "Local",
                "lastName": "Owner",
                "email": "owner@localhost.test",
                "requiredActions": [],
                "credentials": [
                    {"type": "password", "value": identity["password"], "temporary": False}
                ],
            },
            {
                "id": identity["service_subject"],
                "username": "service-account-domain-kg-mcp-test",
                "enabled": True,
                "serviceAccountClientId": "domain-kg-mcp-test",
            },
        ],
    }
    write_private(PRIVATE / "realm.json", json.dumps(realm, indent=2) + "\n")
    write_private(
        PRIVATE / "login.txt",
        "URL: " + ORIGIN + "/admin\nUsername: owner\nPassword: " + identity["password"] + "\n",
    )
    # Persist identity last so interrupted preparation is safe to repeat before import.
    write_private(identity_path, json.dumps(identity, indent=2) + "\n")
    return identity


async def access_token(identity: dict[str, str]) -> str:
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as client:
        response = await client.post(
            ISSUER + "/protocol/openid-connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": "domain-kg-mcp-test",
                "client_secret": identity["client_secret"],
            },
        )
        response.raise_for_status()
        token = response.json()["access_token"]
        principal = await OIDCVerifier(ISSUER, ORIGIN + "/mcp", client).authenticate_bearer(token)
        if principal.subject != identity["service_subject"] or principal.scopes != set(SCOPES):
            raise ValueError(
                "The local identity provider returned an unexpected identity or scope."
            )
        return str(token)


async def login_owner(app: "FastAPI", identity: dict[str, str]) -> "Principal":
    """Use the application's real PKCE/nonce/session flow for setup commands.

    Offline provisioning only admits the known subject. It never supplies an
    authenticated AccessContext or bypasses the application's callback checks.
    """
    from html.parser import HTMLParser
    from urllib.parse import urlsplit

    from domain_kg.identity.models import Principal

    class LoginForm(HTMLParser):
        action: str | None = None

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            fields = dict(attrs)
            if tag == "form" and fields.get("id") == "kc-form-login":
                self.action = fields.get("action")

    async with (
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as application,
        httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as provider,
    ):
        start = await application.get("/auth/login")
        if start.status_code != 303:
            raise ValueError("The application did not start an OIDC login.")
        authorize = start.headers["location"]
        issuer_url = urlsplit(ISSUER)
        if urlsplit(authorize).netloc != issuer_url.netloc:
            raise ValueError("The login endpoint differs from the configured local provider.")
        login = await provider.get(authorize)
        login.raise_for_status()
        # Browsers treat loopback as trustworthy; http.cookiejar does not. Keycloak
        # sets Secure on these development cookies even for its loopback HTTP URL.
        # This client only posts them back to the exact local realm below.
        for cookie in provider.cookies.jar:
            if cookie.domain == "127.0.0.1" and cookie.path == "/realms/domain-kg/":
                cookie.secure = False
        form = LoginForm()
        form.feed(login.text)
        if not form.action or not form.action.startswith(ISSUER + "/login-actions/authenticate?"):
            raise ValueError("The local provider did not return its expected login form.")
        accepted = await provider.post(
            form.action,
            data={
                "username": "owner",
                "password": identity["password"],
                "credentialId": "",
            },
        )
        target = accepted.headers.get("location", "")
        if accepted.status_code != 302 or not target.startswith(ORIGIN + "/auth/callback?"):
            raise ValueError(
                f"Local owner login returned HTTP {accepted.status_code}; "
                f"redirect path: {urlsplit(target).path or 'none'}."
            )
        callback = await application.get(target)
        if callback.status_code != 303 or callback.headers.get("location") != "/admin":
            raise ValueError("The application did not verify the local owner login.")
        current = await application.get("/api/v1/me")
        current.raise_for_status()
        principal = Principal.model_validate(current.json()["principal"])
        if principal.issuer != ISSUER or principal.subject != identity["owner_subject"]:
            raise ValueError("The authenticated owner differs from the admitted identity.")
        await app.state.identity.sessions.revoke(application.cookies.get("domain_kg_session"))
        return principal
