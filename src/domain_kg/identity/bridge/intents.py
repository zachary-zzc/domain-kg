"""Durable intent leases and atomic assertion consumption; no plaintext tokens in Mongo."""

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4, uuid5

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.bridge.models import IntegrationRecord
from domain_kg.materials.service import digest
from domain_kg.storage.transactions import transaction


def now() -> datetime:
    value = datetime.now(UTC)
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


class BridgeIntents:
    def __init__(self, db: AsyncDatabase[dict[str, Any]], key: bytes) -> None:
        self.db, self.cipher = db, AESGCM(key)

    @staticmethod
    def identifier(integration: IntegrationRecord, operation: str, key: str) -> UUID:
        return uuid5(integration.id, operation + "/" + key)

    async def previous(self, identifier: UUID, fingerprint: str) -> dict[str, Any] | None:
        raw = await self.db.identity_bridge_intents.find_one({"_id": str(identifier)})
        if raw and raw["digest"] != fingerprint:
            raise DomainError(
                ErrorCode.IDEMPOTENCY_CONFLICT, "The request key has different input."
            )
        return raw

    async def reserve(
        self,
        integration: IntegrationRecord,
        identifier: UUID,
        operation: str,
        fingerprint: str,
        claims: dict[str, Any],
    ) -> dict[str, Any]:
        assertion_id = digest(
            {"integration": str(integration.id), "iss": claims["iss"], "jti": claims["jti"]}
        )

        async def apply(session: AsyncClientSession) -> dict[str, Any]:
            current = await self.db.identity_integrations.find_one(
                {
                    "id": str(integration.id),
                    "org_id": str(integration.org_id),
                    "revision": integration.revision,
                    "active": True,
                },
                session=session,
            )
            if current is None:
                raise DomainError(ErrorCode.POLICY_CHANGED, "The integration changed.")
            old = await self.db.identity_bridge_intents.find_one(
                {"_id": str(identifier)}, session=session
            )
            if old:
                if old["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "The request key has different input."
                    )
                return old
            used = await self.db.identity_assertion_uses.find_one(
                {"_id": assertion_id}, session=session
            )
            if used:
                raise DomainError(
                    ErrorCode.IDEMPOTENCY_CONFLICT, "This user assertion was already consumed."
                )
            record = {
                "_id": str(identifier),
                "org_id": str(integration.org_id),
                "integration_id": str(integration.id),
                "operation": operation,
                "digest": fingerprint,
                "subject": claims["sub"],
                "state": "pending",
                "created_at": now(),
                "retry_until": datetime.fromtimestamp(claims["exp"], UTC),
            }
            await self.db.identity_bridge_intents.insert_one(record, session=session)
            await self.db.identity_assertion_uses.insert_one(
                {
                    "_id": assertion_id,
                    "intent_id": str(identifier),
                    "org_id": str(integration.org_id),
                    "expires_at": datetime.fromtimestamp(claims["exp"] + 300, UTC),
                },
                session=session,
            )
            return record

        return await transaction(self.db, apply)

    async def lease(self, identifier: UUID) -> str:
        owner = str(uuid4())
        raw = await self.db.identity_bridge_intents.find_one_and_update(
            {
                "_id": str(identifier),
                "state": {"$ne": "complete"},
                "$or": [{"lease_until": {"$exists": False}}, {"lease_until": {"$lte": now()}}],
            },
            {"$set": {"lease_owner": owner, "lease_until": now() + timedelta(seconds=30)}},
            return_document=ReturnDocument.AFTER,
        )
        if raw is None:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY,
                "This identity request is processing; retry with the same key.",
                retryable=True,
            )
        return owner

    async def release(self, identifier: UUID, owner: str) -> None:
        await self.db.identity_bridge_intents.update_one(
            {"_id": str(identifier), "lease_owner": owner},
            {"$unset": {"lease_owner": "", "lease_until": ""}},
        )

    def encrypt(self, identifier: UUID, value: dict[str, Any]) -> str:
        nonce = os.urandom(12)
        return base64.urlsafe_b64encode(
            nonce + self.cipher.encrypt(nonce, json.dumps(value).encode(), str(identifier).encode())
        ).decode()

    def decrypt(self, identifier: UUID, value: str) -> dict[str, Any]:
        try:
            content = base64.b64decode(value, altchars=b"-_", validate=True)
            result: dict[str, Any] = json.loads(
                self.cipher.decrypt(content[:12], content[12:], str(identifier).encode())
            )
            return result
        except Exception:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT,
                "The identity response cannot be recovered safely.",
            ) from None
