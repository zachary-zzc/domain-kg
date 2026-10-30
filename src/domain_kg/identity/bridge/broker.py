"""RFC 8693 adapter validates returned canonical tokens before using their identity."""

import hashlib
import json
import time
from typing import Any

import httpx

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.bridge.config import BridgeProfile
from domain_kg.identity.oidc import OIDCVerifier


def client_id(claims: dict[str, Any]) -> str:
    value = claims.get("azp", claims.get("client_id"))
    if not isinstance(value, str) or not value:
        raise DomainError(ErrorCode.UNAUTHENTICATED, "The credential does not identify its client.")
    return value


class IdentityBroker:
    def __init__(self, canonical: OIDCVerifier, http: httpx.AsyncClient) -> None:
        self.canonical, self.http = canonical, http
        self.verifiers: dict[tuple[str, str], OIDCVerifier] = {}

    def verifier(self, issuer: str, audience: str) -> OIDCVerifier:
        key = (issuer, audience)
        if key not in self.verifiers:
            self.verifiers[key] = OIDCVerifier(issuer, audience, self.http)
        return self.verifiers[key]

    async def caller(self, profile: BridgeProfile, token: str, scope: str) -> None:
        claims = await self.verifier(profile.caller_issuer, profile.caller_audience).verify_claims(
            token
        )
        if (
            claims["sub"] != profile.caller_subject
            or client_id(claims) != profile.caller_client_id
            or scope not in str(claims.get("scope", "")).split()
        ):
            raise DomainError(
                ErrorCode.UNAUTHENTICATED, "The platform credential is not authorized."
            )

    async def subject(self, profile: BridgeProfile, token: str) -> dict[str, Any]:
        claims = await self.verifier(
            profile.subject_issuer, profile.subject_audience
        ).verify_claims(token)
        if (
            client_id(claims) != profile.subject_client_id
            or not isinstance(claims.get("jti"), str)
            or not claims["jti"]
            or len(claims["jti"]) > 512
            or not isinstance(claims["iat"], int)
            or not isinstance(claims["exp"], int)
            or not 0 < claims["exp"] - claims["iat"] <= 120
        ):
            raise DomainError(
                ErrorCode.UNAUTHENTICATED,
                "A short-lived user assertion from the approved client is required.",
            )
        if profile.tenant_claim and claims.get(profile.tenant_claim) != profile.tenant_id:
            raise DomainError(ErrorCode.UNAUTHENTICATED, "The asserted tenant is not approved.")
        if len(claims["sub"]) > 512:
            raise DomainError(ErrorCode.UNAUTHENTICATED, "The external subject exceeds its limit.")
        return claims

    async def exchange(
        self, profile: BridgeProfile, token: str, scopes: tuple[str, ...]
    ) -> tuple[str, dict[str, Any]]:
        body = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": token,
            "subject_token_type": profile.exchange_subject_token_type,
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            profile.exchange_parameter: profile.exchange_audience or self.canonical.audience,
            "scope": " ".join(scopes),
        }
        if profile.subject_issuer_alias:
            body["subject_issuer"] = profile.subject_issuer_alias
        try:
            metadata = await self.canonical.metadata()
            async with self.http.stream(
                "POST",
                metadata["token_endpoint"],
                data=body,
                auth=httpx.BasicAuth(
                    profile.exchange_client_id, profile.exchange_client_secret.get_secret_value()
                ),
                timeout=15,
                follow_redirects=False,
                headers={"Accept-Encoding": "identity"},
            ) as response:
                response.raise_for_status()
                if response.headers.get("content-encoding", "identity") != "identity":
                    raise ValueError("compressed token response is not supported")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(data) + len(chunk) > 65536:
                        raise ValueError("large token response")
                    data.extend(chunk)
                payload = json.loads(data)
            access = payload["access_token"]
            if payload.get("token_type", "").lower() != "bearer" or not isinstance(access, str):
                raise ValueError("invalid token response")
            claims = await self.canonical.verify_claims(access)
            if (
                client_id(claims) != profile.exchange_client_id
                or not isinstance(claims.get("jti"), str)
                or not 0 < claims["exp"] - claims["iat"] <= 600
                or claims["exp"] <= time.time()
                or not set(scopes) <= set(str(claims.get("scope", "")).split())
            ):
                raise ValueError("token binding/scope invalid")
            return access, claims
        except (httpx.HTTPError, ValueError, KeyError, TypeError, DomainError):
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "The identity service could not complete the approved token exchange.",
                retryable=True,
            ) from None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
