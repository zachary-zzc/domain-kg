"""Opaque server-side sessions, hashed tokens, expiry and current admission checks."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Principal
from domain_kg.identity.repository import IdentityRepository
from domain_kg.models import FrozenModel


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class SessionTicket(FrozenModel):
    session_id: str
    csrf: str


class SessionStore:
    def __init__(
        self, database: AsyncDatabase[dict[str, Any]], identities: IdentityRepository
    ) -> None:
        self._database, self._identities = database, identities

    async def initialize(self) -> None:
        await self._database.identity_sessions.create_index("expires_at", expireAfterSeconds=0)
        await self._database.oidc_states.create_index("expires_at", expireAfterSeconds=0)

    async def create(self, principal: Principal, org_id: UUID) -> SessionTicket:
        member = await self._identities.require_member(principal, org_id)
        if member.kind != "human":
            raise DomainError(
                ErrorCode.FORBIDDEN, "Interactive sessions require a human membership."
            )
        ticket = SessionTicket(session_id=secrets.token_urlsafe(32), csrf=secrets.token_urlsafe(32))
        await self._database.identity_sessions.insert_one(
            {
                "_id": token_hash(ticket.session_id),
                "issuer": principal.issuer,
                "subject": principal.subject,
                "org_id": str(org_id),
                "csrf_hash": token_hash(ticket.csrf),
                "expires_at": datetime.now(UTC) + timedelta(hours=8),
            }
        )
        return ticket

    async def authenticate_session(self, session_id: str, *, csrf: str | None = None) -> Principal:
        record = await self._database.identity_sessions.find_one(
            {"_id": token_hash(session_id), "expires_at": {"$gt": datetime.now(UTC)}}
        )
        if record is None:
            raise DomainError(ErrorCode.UNAUTHENTICATED, "The session is missing or expired.")
        if csrf is not None and not secrets.compare_digest(record["csrf_hash"], token_hash(csrf)):
            raise DomainError(ErrorCode.FORBIDDEN, "The CSRF token is invalid.")
        principal = Principal(
            issuer=record["issuer"],
            subject=record["subject"],
            scopes=frozenset({"domain_kg:read", "domain_kg:write", "domain_kg:admin"}),
        )
        await self._identities.require_member(principal, UUID(record["org_id"]))
        return principal

    async def organization(self, session_id: str) -> UUID:
        await self.authenticate_session(session_id)
        record = await self._database.identity_sessions.find_one({"_id": token_hash(session_id)})
        if record is None:
            raise DomainError(ErrorCode.UNAUTHENTICATED, "The session is missing or expired.")
        return UUID(record["org_id"])

    async def revoke(self, session_id: str) -> None:
        await self._database.identity_sessions.delete_one({"_id": token_hash(session_id)})
