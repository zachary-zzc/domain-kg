"""Verify the approved broker against an isolated, disposable Keycloak realm.

This is a development proof, never a deployment/bootstrap command. It imports
only synthetic credentials into a new resource-limited container, uses the
already cached pinned image, and removes the container on exit. No real KG,
browser realm or upstream account is changed. Password grant is enabled solely
for the synthetic user so the proof needs no browser automation.
"""

import asyncio
import json
import secrets
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final
from uuid import uuid4

import httpx
from pydantic import SecretStr

from domain_kg.errors import DomainError
from domain_kg.identity.bridge.broker import IdentityBroker
from domain_kg.identity.bridge.config import BridgeProfile
from domain_kg.identity.oidc import OIDCVerifier

IMAGE = (
    "quay.io/keycloak/keycloak:26.8.1@"
    "sha256:a78d6b79572168cb25886b80a8b5b8d4ed4390743e3eecb810538683e209dd6b"
)
RESOURCE = "http://127.0.0.1:19001/mcp"
ACCESS_TOKEN: Final = "urn:ietf:params:oauth:token-type:access_token"
SCOPES = ("domain_kg:read", "domain_kg:write")
CALLER_SCOPES = ("identity:provision", "identity:exchange", "identity:sync")


@dataclass(frozen=True)
class KeycloakProof:
    issuer: str
    subject: str
    password: SecretStr
    caller_secret: SecretStr
    profile: BridgeProfile


def audience(name: str, value: str, *, client: bool = False) -> dict[str, Any]:
    return {
        "name": name,
        "protocol": "openid-connect",
        "protocolMapper": "oidc-audience-mapper",
        "config": {
            "included.client.audience" if client else "included.custom.audience": value,
            "access.token.claim": "true",
            "id.token.claim": "false",
        },
    }


def subject_mapper() -> dict[str, Any]:
    # An explicitly imported clientScopes list does not include Keycloak's
    # default `basic` scope. Preserve the required stable subject explicitly.
    return {
        "name": "stable-subject",
        "protocol": "openid-connect",
        "protocolMapper": "oidc-sub-mapper",
        "config": {"access.token.claim": "true", "introspection.token.claim": "true"},
    }


def realm(proof: KeycloakProof) -> dict[str, Any]:
    return {
        "realm": "kg-exchange-proof",
        "enabled": True,
        "sslRequired": "none",
        "registrationAllowed": False,
        "resetPasswordAllowed": False,
        "accessTokenLifespan": 120,
        "clients": [
            {
                "clientId": "proof-caller",
                "enabled": True,
                "publicClient": False,
                "secret": proof.caller_secret.get_secret_value(),
                "protocol": "openid-connect",
                "serviceAccountsEnabled": True,
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "defaultClientScopes": list(CALLER_SCOPES),
                "protocolMappers": [subject_mapper(), audience("caller", "proof-caller")],
            },
            {
                "clientId": "proof-user-app",
                "enabled": True,
                "publicClient": True,
                "protocol": "openid-connect",
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": True,
                "protocolMappers": [
                    subject_mapper(),
                    audience("requester", "proof-exchange", client=True),
                    audience("assertion", "proof-user"),
                ],
            },
            {
                "clientId": "proof-exchange",
                "enabled": True,
                "publicClient": False,
                "secret": proof.profile.exchange_client_secret.get_secret_value(),
                "protocol": "openid-connect",
                "standardFlowEnabled": False,
                "directAccessGrantsEnabled": False,
                "attributes": {"standard.token.exchange.enabled": "true"},
                "defaultClientScopes": list(SCOPES),
                "protocolMappers": [subject_mapper(), audience("resource", RESOURCE, client=True)],
            },
            {"clientId": RESOURCE, "enabled": True, "bearerOnly": True},
        ],
        "clientScopes": [
            {
                "name": scope,
                "protocol": "openid-connect",
                "attributes": {"include.in.token.scope": "true"},
            }
            for scope in (*SCOPES, *CALLER_SCOPES)
        ],
        "users": [
            {
                "id": proof.profile.caller_subject,
                "username": "service-account-proof-caller",
                "enabled": True,
                "serviceAccountClientId": "proof-caller",
            },
            {
                "id": proof.subject,
                "username": "synthetic-user",
                "enabled": True,
                "emailVerified": True,
                "firstName": "Synthetic",
                "lastName": "Proof",
                "email": "proof@example.invalid",
                "requiredActions": [],
                "credentials": [
                    {
                        "type": "password",
                        "value": proof.password.get_secret_value(),
                        "temporary": False,
                    }
                ],
            }
        ],
    }


@contextmanager
def isolated_keycloak() -> Iterator[KeycloakProof]:
    """Use a new container; never reuse or mutate the browser-development IdP."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    issuer = origin + "/realms/kg-exchange-proof"
    proof = KeycloakProof(
        issuer,
        str(uuid4()),
        SecretStr(secrets.token_urlsafe(24)),
        SecretStr(secrets.token_urlsafe(32)),
        BridgeProfile(
            id=uuid4(),
            name="Isolated Keycloak standard V2 proof",
            allowed_org_ids=frozenset({uuid4()}),
            caller_issuer=issuer,
            caller_audience="proof-caller",
            caller_client_id="proof-caller",
            caller_subject=str(uuid4()),
            subject_issuer=issuer,
            subject_audience="proof-user",
            subject_client_id="proof-user-app",
            exchange_client_id="proof-exchange",
            exchange_client_secret=SecretStr(secrets.token_urlsafe(32)),
            exchange_subject_token_type=ACCESS_TOKEN,
            exchange_parameter="audience",
            exchange_audience=RESOURCE,
        ),
    )
    name = "domain-kg-exchange-proof-" + secrets.token_hex(6)
    with tempfile.TemporaryDirectory(prefix="kg-exchange-proof-") as temporary:
        imported = Path(temporary) / "kg-exchange-proof-realm.json"
        imported.write_text(json.dumps(realm(proof)))
        imported.chmod(0o600)
        command = [
            "docker", "create", "--pull", "never",
            "--name", name, "--memory", "512m", "--memory-swap", "512m",
            "--cpus", "1", "--publish", f"127.0.0.1:{port}:8080",
            "--env", f"KC_HOSTNAME={origin}",
            "--env", "KC_HTTP_ENABLED=true",
            "--env", "JAVA_OPTS_KC_HEAP=-Xms64m -Xmx256m",
            "--env", (
                "JAVA_OPTS_APPEND=-XX:ActiveProcessorCount=1 "
                "-XX:MaxDirectMemorySize=32m -XX:ReservedCodeCacheSize=48m "
                "-XX:TieredStopAtLevel=1"
            ),
            IMAGE, "start-dev", "--import-realm",
        ]
        # Docker's UID 1000 must read the fixture; parent remains owner-only on
        # the host. The fixture is removed after the disposable container exits.
        imported.chmod(0o644)
        Path(temporary).chmod(0o755)
        started = False
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=30)
            started = True
            # Copy through Docker's API: temporary host paths need not be
            # shared into a Docker Desktop/Colima VM. No host bind is created.
            subprocess.run(
                ["docker", "cp", temporary, name + ":/opt/keycloak/data/import"],
                check=True, capture_output=True, text=True, timeout=30,
            )
            subprocess.run(
                ["docker", "start", name],
                check=True, capture_output=True, text=True, timeout=30,
            )
            deadline = time.monotonic() + 240
            next_state_check = time.monotonic() + 5
            with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
                while True:
                    try:
                        response = client.get(issuer + "/.well-known/openid-configuration")
                        if response.status_code == 200 and response.json().get("issuer") == issuer:
                            break
                    except httpx.HTTPError:
                        pass
                    if time.monotonic() >= next_state_check:
                        state = subprocess.run(
                            ["docker", "inspect", "--format", "{{.State.Running}}", name],
                            check=True, capture_output=True, text=True, timeout=10,
                        )
                        if state.stdout.strip() != "true":
                            raise RuntimeError(
                                "The isolated Keycloak fixture stopped before readiness."
                            )
                        next_state_check = time.monotonic() + 5
                    if time.monotonic() >= deadline:
                        raise RuntimeError("The isolated Keycloak fixture did not become ready.")
                    time.sleep(1)
            yield proof
        finally:
            if started:
                subprocess.run(
                    ["docker", "rm", "--force", name],
                    check=True, capture_output=True, text=True, timeout=30,
                )


async def user_token(proof: KeycloakProof, http: httpx.AsyncClient) -> str:
    """Return a fresh, short-lived signed proof for the one synthetic user."""
    response = await http.post(
        proof.issuer + "/protocol/openid-connect/token",
        data={
            "grant_type": "password",
            "client_id": "proof-user-app",
            "username": "synthetic-user",
            "password": proof.password.get_secret_value(),
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"Synthetic user token request failed: HTTP {response.status_code}.")
    return str(response.json()["access_token"])


async def caller_token(proof: KeycloakProof, http: httpx.AsyncClient) -> str:
    """Return the synthetic platform's credential for an end-to-end test."""
    response = await http.post(
        proof.issuer + "/protocol/openid-connect/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "proof-caller",
            "client_secret": proof.caller_secret.get_secret_value(),
        },
    )
    if response.status_code != 200:
        raise RuntimeError(f"Synthetic caller token request failed: HTTP {response.status_code}.")
    return str(response.json()["access_token"])


async def verify(proof: KeycloakProof) -> dict[str, str | bool]:
    async with httpx.AsyncClient(timeout=15, trust_env=False, follow_redirects=False) as http:
        token = await user_token(proof, http)
        broker = IdentityBroker(OIDCVerifier(proof.issuer, RESOURCE, http), http)
        caller = await caller_token(proof, http)
        for scope in CALLER_SCOPES:
            await broker.caller(proof.profile, caller, scope)
        subject = await broker.subject(proof.profile, token)
        exchanged, claims = await broker.exchange(proof.profile, token, SCOPES)
        assert subject["sub"] == claims["sub"] == proof.subject
        assert claims["azp"] == "proof-exchange"
        assert set(SCOPES) <= set(claims["scope"].split())
        assert exchanged != token
        # Standard V2 explicitly rejects generic JWT subject-token type.
        wrong_type = proof.profile.model_copy(
            update={"exchange_subject_token_type": "urn:ietf:params:oauth:token-type:jwt"}
        )
        try:
            await broker.exchange(wrong_type, token, SCOPES)
        except DomainError:
            pass
        else:
            raise AssertionError("Keycloak unexpectedly accepted the generic JWT token type.")
        return {
            "provider": "Keycloak 26.8.1 standard token exchange V2",
            "signed_subject_verified": True,
            "signed_platform_caller_verified": True,
            "same_realm_subject_preserved": True,
            "canonical_resource_audience_verified": True,
            "exchange_client_and_scopes_verified": True,
            "generic_jwt_subject_type_rejected": True,
            "synthetic_container_removed_on_exit": True,
        }


def main() -> None:
    with isolated_keycloak() as proof:
        result = asyncio.run(verify(proof))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
