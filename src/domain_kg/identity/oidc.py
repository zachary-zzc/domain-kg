"""Verify external signed JWTs. Discovery and keys come only from a pinned issuer."""

import asyncio
import time
from typing import Any
from urllib.parse import urlsplit

import httpx
import jwt

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Principal


def _safe_endpoint(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("OIDC endpoints cannot contain credentials or fragments")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")
    ):
        raise ValueError("OIDC requires HTTPS, except on development loopback")
    return value


class OIDCVerifier:
    def __init__(self, issuer: str, audience: str, client: httpx.AsyncClient) -> None:
        self.issuer = _safe_endpoint(issuer)
        self.audience = audience
        self._client = client
        self._metadata: dict[str, Any] | None = None
        self._keys: dict[str, jwt.PyJWK] = {}
        self._keys_until = 0.0
        self._last_miss_refresh = 0.0
        self._lock = asyncio.Lock()

    async def metadata(self) -> dict[str, Any]:
        if self._metadata is None:
            response = await self._client.get(
                f"{self.issuer.rstrip('/')}/.well-known/openid-configuration",
                follow_redirects=False,
            )
            response.raise_for_status()
            metadata = response.json()
            if not isinstance(metadata, dict) or metadata.get("issuer") != self.issuer:
                raise DomainError(
                    ErrorCode.UNAUTHENTICATED,
                    "The configured identity provider could not be verified.",
                )
            for name in ("jwks_uri", "authorization_endpoint", "token_endpoint"):
                _safe_endpoint(metadata[name])
            self._metadata = metadata
        return self._metadata

    async def _key(self, kid: str) -> jwt.PyJWK:
        async with self._lock:
            now = time.monotonic()
            miss_refresh = (
                now < self._keys_until
                and kid not in self._keys
                and now - self._last_miss_refresh >= 1
            )
            if now >= self._keys_until or miss_refresh:
                metadata = await self.metadata()
                response = await self._client.get(metadata["jwks_uri"], follow_redirects=False)
                response.raise_for_status()
                keys = jwt.PyJWKSet.from_dict(response.json()).keys
                usable = [
                    key
                    for key in keys
                    if key.key_id
                    and key.public_key_use in (None, "sig")
                    and key.algorithm_name in ("RS256", "ES256")
                ]
                if len({key.key_id for key in usable}) != len(usable):
                    raise ValueError("duplicate signing key IDs")
                self._keys = {key.key_id: key for key in usable if key.key_id is not None}
                self._keys_until = now + 300
                if miss_refresh:
                    self._last_miss_refresh = now
            if kid not in self._keys:
                raise DomainError(
                    ErrorCode.UNAUTHENTICATED, "The credential signing key is unavailable."
                )
            return self._keys[kid]

    async def verify_claims(self, token: str, *, audience: str | None = None) -> dict[str, Any]:
        try:
            if not token or len(token) > 16384:
                raise ValueError("invalid token size")
            header = jwt.get_unverified_header(token)
            if (
                header.get("alg") not in ("RS256", "ES256")
                or not isinstance(header.get("kid"), str)
                or header.get("crit")
            ):
                raise ValueError("unsupported JWT header")
            key = await self._key(header["kid"])
            if key.algorithm_name != header["alg"]:
                raise ValueError("key algorithm mismatch")
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[key.algorithm_name],
                issuer=self.issuer,
                audience=audience or self.audience,
                # Independent IdP/application clocks can differ by a few seconds.
                leeway=5,
                options={"require": ["iss", "sub", "aud", "exp", "iat"]},
            )
            if not isinstance(claims["sub"], str) or not claims["sub"].strip():
                raise ValueError("empty subject")
            return claims
        except DomainError:
            raise
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise DomainError(
                ErrorCode.UNAUTHENTICATED, "The credential could not be verified."
            ) from exc

    async def authenticate_bearer(self, token: str) -> Principal:
        claims = await self.verify_claims(token)
        scope = claims.get("scope", "")
        if not isinstance(scope, str) or "domain_kg:read" not in scope.split():
            raise DomainError(ErrorCode.FORBIDDEN, "The credential lacks the required scope.")
        return Principal(
            issuer=claims["iss"], subject=claims["sub"], scopes=frozenset(scope.split())
        )
