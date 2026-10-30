"""Human administrators approve finite integration bounds using current policy."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.bridge.config import BridgeConfig, BridgeProfile
from domain_kg.identity.bridge.models import (
    IntegrationChange,
    IntegrationCreate,
    IntegrationRecord,
    IntegrationView,
)
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.materials.service import digest
from domain_kg.storage.transactions import transaction


def profile_fingerprint(profile: BridgeProfile) -> str:
    return digest(profile.model_dump(mode="json", exclude={"exchange_client_secret"}))


class IntegrationControl:
    def __init__(self, policy: Policy, config: BridgeConfig | None, issuer: str) -> None:
        self.policy, self.db, self.config, self.issuer = policy, policy.database, config, issuer

    def profile(self, identifier: UUID, org: UUID) -> BridgeProfile:
        result = (
            next(
                (
                    p
                    for p in self.config.profiles
                    if p.id == identifier and org in p.allowed_org_ids
                ),
                None,
            )
            if self.config
            else None
        )
        if result is None:
            raise inaccessible_reference()
        return result

    async def record(self, identifier: UUID) -> IntegrationRecord:
        raw = await self.db.identity_integrations.find_one({"id": str(identifier)})
        if raw is None:
            raise inaccessible_reference()
        return IntegrationRecord.model_validate({k: v for k, v in raw.items() if k != "_id"})

    async def create(self, access: AccessContext, command: IntegrationCreate) -> IntegrationView:
        current = await self.policy.revalidate(access, action=Action.MANAGE_ORGANIZATION)
        profile = self.profile(command.profile_id, current.org_id)
        for kg in command.allowed_kg_ids:
            await self.policy.resolve_access(
                current.principal, [kg], Action.MANAGE_GRANTS, org_id=current.org_id
            )
        for mapping in command.group_mappings:
            if (
                await self.db.groups.find_one(
                    {"org_id": str(current.org_id), "id": str(mapping.group_id)}
                )
                is None
            ):
                raise inaccessible_reference()
            async for grant in self.db.kg_grants.find(
                {
                    "org_id": str(current.org_id),
                    "subject_kind": "group",
                    "subject_id": str(mapping.group_id),
                }
            ):
                kg = UUID(grant["kg_id"])
                if kg not in command.allowed_kg_ids:
                    raise inaccessible_reference()
        identifier = uuid5(
            current.org_id,
            f"integration/{current.principal.issuer}/{current.principal.subject}/{command.idempotency_key}",
        )
        fingerprint = digest(command.model_dump(mode="json"))

        async def apply(session: AsyncClientSession) -> IntegrationView:
            await self.policy.revalidate(current, session=session)
            raw = await self.db.identity_integrations.find_one(
                {"id": str(identifier)}, session=session
            )
            key = {"_id": f"create:{identifier}"}
            previous = await self.db.identity_bridge_control.find_one(key, session=session)
            if previous:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Integration key has different input."
                    )
                if raw is None:
                    raise inaccessible_reference()
                return IntegrationView.model_validate(
                    {k: raw[k] for k in IntegrationView.model_fields}
                )
            if await self.db.identity_integrations.find_one(
                {"canonical_issuer": self.issuer, "exchange_client_id": profile.exchange_client_id},
                session=session,
            ):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT,
                    "This exchange client is registered; use a dedicated client per integration.",
                )
            record = IntegrationRecord(
                id=identifier,
                org_id=current.org_id,
                profile_id=command.profile_id,
                name=command.name,
                allowed_kg_ids=command.allowed_kg_ids,
                allowed_actions=command.allowed_actions,
                default_grants=command.default_grants,
                group_mappings=command.group_mappings,
                auto_approve=command.auto_approve,
                active=True,
                revision=0,
                canonical_issuer=self.issuer,
                exchange_client_id=profile.exchange_client_id,
                profile_fingerprint=profile_fingerprint(profile),
            )
            await self.db.identity_integrations.insert_one(
                record.model_dump(mode="json") | {"_id": str(identifier)}, session=session
            )
            await self.db.identity_bridge_control.insert_one(
                key | {"digest": fingerprint}, session=session
            )
            await self.bump(current.org_id, session)
            await self.audit(
                current.org_id,
                "integration_created",
                identifier,
                current.principal.model_dump(mode="json"),
                session,
            )
            return IntegrationView.model_validate(
                {k: getattr(record, k) for k in IntegrationView.model_fields}
            )

        return await transaction(self.db, apply)

    async def bump(self, org: UUID, session: AsyncClientSession) -> None:
        await self.db.organizations.update_one(
            {"_id": str(org)}, {"$inc": {"policy_revision": 1}}, session=session
        )

    async def audit(
        self,
        org: UUID,
        operation: str,
        target: UUID,
        actor: dict[str, Any],
        session: AsyncClientSession,
    ) -> None:
        await self.db.identity_bridge_audit.insert_one(
            {
                "org_id": str(org),
                "operation": operation,
                "target_id": str(target),
                "actor": actor,
                "created_at": datetime.now(UTC),
            },
            session=session,
        )

    async def change(
        self, access: AccessContext, identifier: UUID, command: IntegrationChange
    ) -> IntegrationView:
        current = await self.policy.revalidate(access, action=Action.MANAGE_ORGANIZATION)

        async def apply(session: AsyncClientSession) -> IntegrationView:
            await self.policy.revalidate(current, session=session)
            raw = await self.db.identity_integrations.find_one(
                {"org_id": str(current.org_id), "id": str(identifier)}, session=session
            )
            if raw is None:
                raise inaccessible_reference()
            record = IntegrationRecord.model_validate({k: v for k, v in raw.items() if k != "_id"})
            key = {
                "_id": f"change:{identifier}:{current.principal.subject}:{command.idempotency_key}"
            }
            fingerprint = digest(command.model_dump(mode="json"))
            previous = await self.db.identity_bridge_control.find_one(key, session=session)
            if previous:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Change key has different input."
                    )
            else:
                if record.revision != command.expected_revision:
                    raise DomainError(ErrorCode.VERSION_CONFLICT, "The integration changed.")
                if command.active:
                    profile = self.profile(record.profile_id, current.org_id)
                    if record.profile_fingerprint != profile_fingerprint(profile):
                        raise DomainError(
                            ErrorCode.VERSION_CONFLICT,
                            "Trust profile changed; register a new dedicated integration.",
                        )
                await self.db.identity_integrations.update_one(
                    {"id": str(identifier)},
                    {"$set": {"active": command.active}, "$inc": {"revision": 1}},
                    session=session,
                )
                # Disabling is irreversible for existing connections; re-enable needs fresh tokens.
                if not command.active:
                    await self.db.mcp_connections.update_many(
                        {
                            "org_id": str(current.org_id),
                            "integration_id": str(identifier),
                            "revoked": False,
                        },
                        {"$set": {"revoked": True}, "$inc": {"revision": 1}},
                        session=session,
                    )
                await self.db.identity_bridge_control.insert_one(
                    key | {"digest": fingerprint}, session=session
                )
                await self.bump(current.org_id, session)
                await self.audit(
                    current.org_id,
                    "integration_changed",
                    identifier,
                    current.principal.model_dump(mode="json"),
                    session,
                )
            fresh = await self.db.identity_integrations.find_one(
                {"id": str(identifier)}, session=session
            )
            assert fresh is not None
            return IntegrationView.model_validate(
                {k: fresh[k] for k in IntegrationView.model_fields}
            )

        return await transaction(self.db, apply)
