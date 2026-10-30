"""Verified platform assertions become stable memberships and bounded external-token connections."""

import base64
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid5

import httpx
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.bridge.broker import IdentityBroker, token_hash
from domain_kg.identity.bridge.config import read_config
from domain_kg.identity.bridge.control import IntegrationControl, profile_fingerprint
from domain_kg.identity.bridge.intents import BridgeIntents, now
from domain_kg.identity.bridge.management import BridgeManagement
from domain_kg.identity.bridge.models import (
    AssertionRequest,
    BootstrapResult,
    ConnectionRecord,
    IdentityLink,
    IntegrationRecord,
    TokenBinding,
    TokenRequest,
    TokenResult,
)
from domain_kg.identity.delegation import connection_for, invalid_connection, member_enabled
from domain_kg.identity.models import Action, DelegationRef, Member, Principal
from domain_kg.identity.oidc import OIDCVerifier
from domain_kg.identity.policy import Policy
from domain_kg.identity.repository import IdentityRepository
from domain_kg.materials.service import digest
from domain_kg.settings import Settings
from domain_kg.storage.transactions import transaction


class IdentityBridge:
    def __init__(
        self,
        settings: Settings,
        identities: IdentityRepository,
        verifier: OIDCVerifier,
        http: httpx.AsyncClient,
    ) -> None:
        self.settings, self.identities, self.db = settings, identities, identities.database
        self.config = read_config(settings.identity_bridge_config)
        self.policy = Policy(self.db)
        self.control = IntegrationControl(self.policy, self.config, settings.oidc_issuer)
        self.management = BridgeManagement(self.control, identities)
        self.broker = IdentityBroker(verifier, http)
        self.intents = (
            BridgeIntents(
                self.db, base64.urlsafe_b64decode(self.config.response_key.get_secret_value())
            )
            if self.config
            else None
        )

    async def initialize(self) -> None:
        for name in (
            "identity_integrations",
            "identity_links",
            "mcp_connections",
            "member_identity_states",
        ):
            await self.db[name].create_index([("org_id", 1), ("id", 1)], unique=True)
        await self.db.identity_integrations.create_index(
            [("canonical_issuer", 1), ("exchange_client_id", 1)], unique=True
        )
        await self.db.identity_links.create_index(
            [("org_id", 1), ("integration_id", 1), ("external_subject", 1)], unique=True
        )
        await self.db.mcp_token_bindings.create_index("token_hash", unique=True)
        await self.db.mcp_token_bindings.create_index("expires_at", expireAfterSeconds=300)
        await self.db.identity_assertion_uses.create_index("expires_at", expireAfterSeconds=0)
        await self.db.identity_bridge_responses.create_index("expires_at", expireAfterSeconds=0)
        # Do not silently activate a disabled integration when configuration reappears.
        async for row in self.db.identity_integrations.find({"active": True}):
            try:
                profile = self.control.profile(UUID(row["profile_id"]), UUID(row["org_id"]))
                if row.get("profile_fingerprint") == profile_fingerprint(profile):
                    continue
            except DomainError:
                pass
            await self._disable_untrusted(row)

    async def _disable_untrusted(self, row: dict[str, Any]) -> None:
        async def apply(session: AsyncClientSession) -> None:
            changed = await self.db.identity_integrations.update_one(
                {
                    "_id": row["_id"],
                    "org_id": row["org_id"],
                    "active": True,
                    "revision": row["revision"],
                },
                {"$set": {"active": False}, "$inc": {"revision": 1}},
                session=session,
            )
            if not changed.modified_count:
                return
            await self.db.mcp_connections.update_many(
                {"org_id": row["org_id"], "integration_id": row["id"], "revoked": False},
                {"$set": {"revoked": True}, "$inc": {"revision": 1}},
                session=session,
            )
            org = UUID(row["org_id"])
            await self.control.bump(org, session)
            await self.control.audit(
                org,
                "trust_profile_removed_or_changed",
                UUID(row["id"]),
                {"source": "operator_configuration"},
                session,
            )

        await transaction(self.db, apply)

    async def caller(self, identifier: UUID, token: str, scope: str) -> IntegrationRecord:
        try:
            integration = await self.control.record(identifier)
            if not integration.active:
                raise invalid_connection()
            profile = self.control.profile(integration.profile_id, integration.org_id)
            await self.broker.caller(profile, token, scope)
            return integration
        except DomainError as error:
            if error.code in (ErrorCode.RESOURCE_NOT_ACCESSIBLE, ErrorCode.UNAUTHENTICATED):
                raise DomainError(
                    ErrorCode.UNAUTHENTICATED, "The identity integration credential is unavailable."
                ) from None
            raise

    async def bind_principal(
        self, principal: Principal, claims: dict[str, Any], token: str
    ) -> Principal:
        application = claims.get("azp", claims.get("client_id"))
        registered = (
            await self.db.identity_integrations.find_one(
                {"canonical_issuer": principal.issuer, "exchange_client_id": application}
            )
            if isinstance(application, str)
            else None
        )
        raw = await self.db.mcp_token_bindings.find_one({"token_hash": token_hash(token)})
        if registered is None and raw is None:
            return principal
        if registered is None or raw is None:
            raise invalid_connection()
        binding = TokenBinding.model_validate({k: v for k, v in raw.items() if k != "_id"})
        if (binding.issuer, binding.subject, binding.client_id) != (
            principal.issuer,
            principal.subject,
            application,
        ) or binding.expires_at <= now():
            raise invalid_connection()
        delegated = principal.model_copy(
            update={
                "delegation": DelegationRef(
                    org_id=binding.org_id, connection_id=binding.connection_id
                )
            }
        )
        connection = await connection_for(self.db, delegated)
        if str(connection.integration_id) != registered["id"]:
            raise invalid_connection()
        await self.db.mcp_connections.update_one(
            {"id": str(connection.id), "revoked": False}, {"$set": {"last_used_at": now()}}
        )
        return delegated

    async def link(
        self,
        integration: IntegrationRecord,
        external_subject: str,
        session: AsyncClientSession | None = None,
    ) -> IdentityLink:
        raw = await self.db.identity_links.find_one(
            {
                "org_id": str(integration.org_id),
                "integration_id": str(integration.id),
                "external_subject": external_subject,
            },
            session=session,
        )
        if raw is None:
            raise DomainError(
                ErrorCode.RESOURCE_NOT_ACCESSIBLE,
                "Bootstrap the verified user before requesting a connection.",
            )
        return IdentityLink.model_validate({k: v for k, v in raw.items() if k != "_id"})

    async def _status(self, link: IdentityLink, created: bool) -> BootstrapResult:
        raw = await self.db.members.find_one(
            {"org_id": str(link.org_id), "id": str(link.member_id)}
        )
        if raw is None:
            raise inaccessible_reference()
        suspended = (
            link.disabled
            or raw["suspended"]
            or (link.approved and not await member_enabled(self.db, link.org_id, link.member_id))
        )
        status: Literal["active", "pending_approval", "suspended"] = (
            "suspended" if suspended else "active" if link.approved else "pending_approval"
        )
        return BootstrapResult(
            member_id=link.member_id,
            created=created,
            status=status,
            connection_allowed=status == "active",
        )

    async def process(
        self, caller_token: str, command: AssertionRequest, key: str
    ) -> BootstrapResult | TokenResult:
        operation = "token" if isinstance(command, TokenRequest) else "bootstrap"
        integration = await self.caller(
            command.integration_id,
            caller_token,
            "identity:exchange" if operation == "token" else "identity:provision",
        )
        if self.intents is None:
            raise invalid_connection()
        intents = self.intents
        identifier = intents.identifier(integration, operation, key)
        fingerprint = digest(command.model_dump(mode="json"))
        previous = await intents.previous(identifier, fingerprint)
        if previous and previous["state"] == "complete":
            if operation == "bootstrap":
                link = await self.link(integration, previous["subject"])
                return await self._status(link, previous["created"])
            cached = await self.db.identity_bridge_responses.find_one(
                {"_id": str(identifier), "expires_at": {"$gt": now()}}
            )
            if cached is None:
                raise DomainError(
                    ErrorCode.REVISION_NOT_READY,
                    "The cached token expired; use a fresh assertion and request key.",
                )
            result = TokenResult.model_validate(intents.decrypt(identifier, cached["response"]))
            claims = await self.broker.canonical.verify_claims(result.access_token)
            await self.bind_principal(
                Principal(
                    issuer=claims["iss"],
                    subject=claims["sub"],
                    scopes=frozenset(str(claims.get("scope", "")).split()),
                ),
                claims,
                result.access_token,
            )
            return result
        profile = self.control.profile(integration.profile_id, integration.org_id)
        claims = await self.broker.subject(profile, command.subject_token)
        if isinstance(command, TokenRequest):
            if not set(command.kg_ids) <= set(integration.allowed_kg_ids) or not set(
                command.actions
            ) <= set(integration.allowed_actions):
                raise DomainError(
                    ErrorCode.FORBIDDEN, "The requested delegation exceeds integration limits."
                )
            existing = await self.link(integration, claims["sub"])
            if not (await self._status(existing, False)).connection_allowed:
                raise invalid_connection()
        await intents.reserve(integration, identifier, operation, fingerprint, claims)
        owner = await intents.lease(identifier)
        try:
            scopes = (
                ("domain_kg:read",)
                if not isinstance(command, TokenRequest)
                or all(a in (Action.READ, Action.SEARCH, Action.LIST_KGS) for a in command.actions)
                else ("domain_kg:read", "domain_kg:write")
            )
            access, canonical = await self.broker.exchange(profile, command.subject_token, scopes)
            if (
                profile.subject_issuer == self.settings.oidc_issuer
                and canonical["sub"] != claims["sub"]
            ):
                raise DomainError(
                    ErrorCode.UNAUTHENTICATED, "The identity service returned a different user."
                )
            if isinstance(command, TokenRequest):
                return await self._issue(
                    integration, identifier, owner, command, claims["sub"], access, canonical
                )
            return await self._bootstrap(
                integration, identifier, owner, claims["sub"], canonical["sub"]
            )
        finally:
            await intents.release(identifier, owner)

    async def _fence(
        self,
        integration: IntegrationRecord,
        identifier: UUID,
        owner: str,
        session: AsyncClientSession,
    ) -> None:
        if (
            await self.db.identity_integrations.find_one(
                {
                    "id": str(integration.id),
                    "org_id": str(integration.org_id),
                    "active": True,
                    "revision": integration.revision,
                },
                session=session,
            )
            is None
        ):
            raise DomainError(ErrorCode.POLICY_CHANGED, "The identity integration changed.")
        if (
            await self.db.identity_bridge_intents.find_one(
                {"_id": str(identifier), "lease_owner": owner, "lease_until": {"$gt": now()}},
                session=session,
            )
            is None
        ):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The identity request lease changed.")

    async def _bootstrap(
        self,
        integration: IntegrationRecord,
        identifier: UUID,
        owner: str,
        external: str,
        canonical: str,
    ) -> BootstrapResult:
        profile = self.control.profile(integration.profile_id, integration.org_id)

        async def apply(session: AsyncClientSession) -> BootstrapResult:
            await self._fence(integration, identifier, owner, session)
            existing = await self.db.identity_links.find_one(
                {
                    "org_id": str(integration.org_id),
                    "integration_id": str(integration.id),
                    "external_subject": external,
                },
                session=session,
            )
            created = False
            if existing:
                link = IdentityLink.model_validate(
                    {k: v for k, v in existing.items() if k != "_id"}
                )
                if link.canonical_subject != canonical:
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT,
                        "The external identity mapping changed; administrator review is required.",
                    )
            else:
                raw = await self.db.members.find_one(
                    {
                        "org_id": str(integration.org_id),
                        "issuer": self.settings.oidc_issuer,
                        "subject": canonical,
                    },
                    session=session,
                )
                if raw is not None and profile.subject_issuer != self.settings.oidc_issuer:
                    raise DomainError(
                        ErrorCode.FORBIDDEN,
                        "Cross-issuer linking to an existing account is not enabled.",
                    )
                if raw:
                    member = Member.model_validate({k: v for k, v in raw.items() if k != "_id"})
                    if member.kind != "human":
                        raise inaccessible_reference()
                else:
                    created = True
                    member = Member(
                        id=uuid5(integration.org_id, self.settings.oidc_issuer + "\0" + canonical),
                        org_id=integration.org_id,
                        issuer=self.settings.oidc_issuer,
                        subject=canonical,
                        role="member",
                    )
                    await self.db.members.insert_one(
                        member.model_dump(mode="json") | {"_id": str(member.id)}, session=session
                    )
                    await self.db.member_identity_states.insert_one(
                        {
                            "_id": str(member.id),
                            "org_id": str(integration.org_id),
                            "id": str(member.id),
                        },
                        session=session,
                    )
                link = IdentityLink(
                    id=uuid5(integration.id, external),
                    org_id=integration.org_id,
                    member_id=member.id,
                    integration_id=integration.id,
                    external_subject=external,
                    external_issuer=profile.subject_issuer,
                    canonical_issuer=member.issuer,
                    canonical_subject=canonical,
                    approved=integration.auto_approve,
                    disabled=False,
                    revision=0,
                )
                await self.db.identity_links.insert_one(
                    link.model_dump(mode="json") | {"_id": str(link.id)}, session=session
                )
                if created:
                    for grant in integration.default_grants:
                        if not await self.db.kgs.find_one(
                            {
                                "org_id": str(integration.org_id),
                                "id": str(grant.kg_id),
                                "state": {"$ne": "deleted"},
                            },
                            session=session,
                        ):
                            raise inaccessible_reference()
                        # The control-plane admission already checked the approving KG owner.
                        await self.db.kg_grants.insert_one(
                            {
                                "org_id": str(integration.org_id),
                                "kg_id": str(grant.kg_id),
                                "subject_kind": "member",
                                "subject_id": str(member.id),
                                "role": grant.role,
                            },
                            session=session,
                        )
                await self.control.bump(integration.org_id, session)
                await self.control.audit(
                    integration.org_id,
                    "user_bootstrapped",
                    link.member_id,
                    {"integration_id": str(integration.id), "external_subject": external},
                    session,
                )
            raw_member = await self.db.members.find_one(
                {"id": str(link.member_id), "org_id": str(link.org_id)}, session=session
            )
            if raw_member is None:
                raise inaccessible_reference()
            status: Literal["active", "pending_approval", "suspended"] = (
                "suspended"
                if link.disabled
                or raw_member["suspended"]
                or (
                    link.approved
                    and not await member_enabled(self.db, link.org_id, link.member_id, session)
                )
                else "active"
                if link.approved
                else "pending_approval"
            )
            result = BootstrapResult(
                member_id=link.member_id,
                created=created,
                status=status,
                connection_allowed=status == "active",
            )
            await self.db.identity_bridge_intents.update_one(
                {"_id": str(identifier), "lease_owner": owner},
                {"$set": {"state": "complete", "created": created}},
                session=session,
            )
            return result

        return await transaction(self.db, apply)

    async def _issue(
        self,
        integration: IntegrationRecord,
        identifier: UUID,
        owner: str,
        command: TokenRequest,
        external: str,
        access_token: str,
        claims: dict[str, Any],
    ) -> TokenResult:
        assert self.intents is not None
        intents = self.intents

        async def apply(session: AsyncClientSession) -> TokenResult:
            await self._fence(integration, identifier, owner, session)
            link = await self.link(integration, external, session)
            if not link.approved or link.disabled or link.canonical_subject != claims["sub"]:
                raise invalid_connection()
            principal = Principal(
                issuer=link.canonical_issuer,
                subject=link.canonical_subject,
                scopes=frozenset(str(claims.get("scope", "")).split()),
            )
            if command.kg_ids:
                for action in command.actions:
                    await self.policy.resolve_access(
                        principal, list(command.kg_ids), action, org_id=link.org_id, session=session
                    )
            else:
                await self.identities.require_member(principal, link.org_id)
            expires = now() + timedelta(seconds=command.connection_seconds)
            connection = ConnectionRecord(
                id=identifier,
                org_id=integration.org_id,
                integration_id=integration.id,
                member_id=link.member_id,
                application=integration.name,
                kg_ids=command.kg_ids,
                actions=tuple(dict.fromkeys((Action.LIST_KGS, *command.actions))),
                created_at=now(),
                expires_at=expires,
                revoked=False,
                revision=0,
                issuer=principal.issuer,
                subject=principal.subject,
                link_id=link.id,
            )
            # LIST_KGS is intrinsically filtered through current per-KG permissions.
            binding = TokenBinding(
                token_hash=token_hash(access_token),
                connection_id=identifier,
                org_id=link.org_id,
                issuer=principal.issuer,
                subject=principal.subject,
                client_id=integration.exchange_client_id,
                expires_at=datetime.fromtimestamp(claims["exp"], UTC),
            )
            token_exp = min(binding.expires_at, expires)
            ttl = int((token_exp - now()).total_seconds())
            if ttl < 1:
                raise invalid_connection()
            result = TokenResult(
                access_token=access_token,
                expires_in=ttl,
                connection_id=identifier,
                connection_expires_at=expires,
            )
            await self.db.mcp_connections.insert_one(
                connection.model_dump(mode="json")
                | {
                    "_id": str(identifier),
                    "created_at": connection.created_at,
                    "expires_at": expires,
                },
                session=session,
            )
            await self.db.mcp_token_bindings.insert_one(
                binding.model_dump(mode="json")
                | {"_id": binding.token_hash, "expires_at": binding.expires_at},
                session=session,
            )
            await self.db.identity_bridge_intents.update_one(
                {"_id": str(identifier), "lease_owner": owner},
                {
                    "$set": {
                        "state": "complete",
                    }
                },
                session=session,
            )
            await self.db.identity_bridge_responses.insert_one(
                {
                    "_id": str(identifier),
                    "org_id": str(link.org_id),
                    "response": intents.encrypt(identifier, result.model_dump(mode="json")),
                    "expires_at": token_exp,
                },
                session=session,
            )
            await self.control.bump(link.org_id, session)
            await self.control.audit(
                link.org_id,
                "connection_created",
                identifier,
                {"integration_id": str(integration.id), "member_id": str(link.member_id)},
                session,
            )
            return result

        return await transaction(self.db, apply)
