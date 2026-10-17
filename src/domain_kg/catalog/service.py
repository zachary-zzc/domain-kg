"""KG creation and lifecycle depend on shared policy and registered placements."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from domain_kg.catalog.backends import BackendRegistry
from domain_kg.catalog.models import CreateKG, KGReceipt
from domain_kg.catalog.repository import CatalogRepository
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.models import PageRequest
from domain_kg.storage.router import KGStoreRouter


class CatalogService:
    general_profile_id = uuid5(NAMESPACE_URL, "domain-kg/domain/general/v1")
    scientific_profile_id = uuid5(NAMESPACE_URL, "domain-kg/domain/scientific/v1")
    medical_profile_id = uuid5(NAMESPACE_URL, "domain-kg/domain/medical/v1")

    def __init__(self, policy: Policy, registry: BackendRegistry, router: KGStoreRouter) -> None:
        self.policy, self.registry, self.router = policy, registry, router
        self.repository = CatalogRepository(policy, registry)

    async def initialize(self) -> None:
        await self.repository.initialize()
        for identifier, name in (
            (self.general_profile_id, "general"),
            (self.scientific_profile_id, "scientific"),
            (self.medical_profile_id, "medical"),
        ):
            await self.policy.database.domain_profiles.update_one(
                {"id": str(identifier)},
                {"$setOnInsert": {"name": name, "revision": 1}},
                upsert=True,
            )

    @staticmethod
    def _receipt(kg: dict[str, Any]) -> KGReceipt:
        return KGReceipt(
            org_id=UUID(kg["org_id"]),
            kg_id=UUID(kg["id"]),
            state=kg["state"],
            revision=kg["revision"],
        )

    async def create_kg(self, access: AccessContext, command: CreateKG) -> KGReceipt:
        kg = await self.repository.reserve(access, command)
        if kg["state"] not in ("provisioning", "error"):
            return self._receipt(kg)
        identifier = UUID(kg["id"])
        current = await self.policy.resolve_access(
            access.principal, [identifier], Action.CONFIGURE, org_id=access.org_id
        )
        try:
            await self.router.provision(current, identifier)
        except DomainError:
            # A duplicate provisioner may have completed while this attempt was
            # checking readiness. Return only after checking current authority.
            await self.policy.resolve_access(
                access.principal, [identifier], Action.CONFIGURE, org_id=access.org_id
            )
            accepted = await self.policy.database.kgs.find_one(
                {"org_id": str(access.org_id), "id": str(identifier), "state": "ready"}
            )
            if accepted is not None:
                return self._receipt(accepted)
            await self.policy.database.kgs.update_one(
                {
                    "org_id": str(current.org_id),
                    "id": str(identifier),
                    "state": {"$in": ["provisioning", "error"]},
                },
                {"$set": {"state": "error"}},
            )
            raise
        result = await self.repository.set_state(
            current,
            identifier,
            "ready",
            expected_revision=kg["revision"],
            idempotency_key=f"provision:{identifier}",
        )
        return self._receipt(result)

    async def get_kg(self, access: AccessContext, kg_id: UUID) -> dict[str, Any]:
        current = await self.policy.revalidate(access)
        if kg_id not in current.kg_ids:
            raise inaccessible_reference()
        raw = await self.policy.database.kgs.find_one(
            {"org_id": str(current.org_id), "id": str(kg_id)}
        )
        if raw is None:
            raise inaccessible_reference()
        fields: tuple[str, ...] = ("id", "name", "state", "revision", "published_revision")
        if any(grant.kg_id == kg_id and grant.role == "owner" for grant in current.grants):
            fields += (
                "storage_profile_id",
                "domain_profile_id",
                "generation",
                "visibility_revision",
            )
        result = {key: raw[key] for key in fields if key in raw}
        await self.policy.revalidate(current)
        return result

    async def change_state(
        self,
        access: AccessContext,
        kg_id: UUID,
        state: Literal["ready", "read_only", "archived"],
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> KGReceipt:
        if state not in ("ready", "read_only", "archived"):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Unsupported KG state transition.")
        return self._receipt(
            await self.repository.set_state(
                access,
                kg_id,
                state,
                expected_revision=expected_revision,
                idempotency_key=idempotency_key,
            )
        )

    async def health(self, access: AccessContext, kg_id: UUID) -> dict[str, str]:
        await self.policy.revalidate(access)
        try:
            await self.router.for_kg(access, kg_id)
            status = "healthy"
        except DomainError as error:
            if error.code != ErrorCode.BACKEND_UNAVAILABLE:
                raise
            status = "unavailable"
        await self.policy.revalidate(access)
        return {"kg_id": str(kg_id), "status": status}

    async def list_kgs(
        self, access: AccessContext, page: PageRequest, *, lifecycle: str | None = None
    ) -> dict[str, Any]:
        current = await self.policy.resolve_access(
            access.principal, [], Action.LIST_KGS, org_id=access.org_id
        )
        if lifecycle not in (None, "provisioning", "ready", "read_only", "archived", "error"):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Unsupported lifecycle filter.")
        binding = {
            "org_id": str(current.org_id),
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "kind": "catalog",
            "revision": current.policy_revision,
            "limit": page.limit,
            "lifecycle": lifecycle,
        }
        after = ""
        if page.cursor:
            cursor = await self.policy.database.identity_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(
                    ErrorCode.CURSOR_EXPIRED, "The catalog cursor expired or its scope changed."
                )
            after = cursor["after"]
        query: dict[str, Any] = {"org_id": str(current.org_id), "id": {"$gt": after}}
        if lifecycle is not None:
            query["state"] = lifecycle
        results: list[dict[str, Any]] = []
        last = after
        more = False
        async for row in self.policy.database.kgs.find(query).sort("id", 1):
            identifier = UUID(row["id"])
            try:
                scoped = await self.policy.resolve_access(
                    current.principal, [identifier], Action.CONFIGURE, org_id=current.org_id
                )
            except DomainError as error:
                if error.code not in (ErrorCode.FORBIDDEN, ErrorCode.RESOURCE_NOT_ACCESSIBLE):
                    raise
                try:
                    scoped = await self.policy.resolve_access(
                        current.principal, [identifier], Action.READ, org_id=current.org_id
                    )
                except DomainError as error:
                    if error.code not in (ErrorCode.FORBIDDEN, ErrorCode.RESOURCE_NOT_ACCESSIBLE):
                        raise
                    continue
            if len(results) == page.limit:
                more = True
                break
            results.append(await self.get_kg(scoped, identifier))
            last = row["id"]
        await self.policy.revalidate(current)
        cursor_token = None
        if more:
            cursor_token = secrets.token_urlsafe(32)
            await self.policy.database.identity_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(cursor_token.encode()).hexdigest(),
                    "after": last,
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        return {"items": results, "next_cursor": cursor_token}
