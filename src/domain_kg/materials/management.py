"""Explicit editor metadata for pending/removed sources, without evidence or originals."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from domain_kg.engine.scope import EligibilityResolver
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.service import AcceptedMetadata
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import Namespace, NonNegative, PageRequest, ScopedRef, UTCDateTime
from domain_kg.retrieval.models import SearchRequest
from domain_kg.storage.models import MaterialRecord

ManagementState = Literal["pending", "removed"]


class ManagedMaterial(Namespace):
    id: UUID
    title: Annotated[str, Field(max_length=1000)]
    revision: NonNegative
    state: Literal["pending", "excluded", "deleted"]
    retention_until: UTCDateTime | None = None
    can_restore: bool = False


class ManagedMaterialPage(Namespace):
    items: Annotated[tuple[ManagedMaterial, ...], Field(max_length=100)]
    next_cursor: str | None = None

    @model_validator(mode="after")
    def exact_namespace(self) -> Self:
        if any((item.org_id, item.kg_id) != (self.org_id, self.kg_id) for item in self.items):
            raise ValueError("managed materials must share their page namespace")
        return self


class MaterialManagement:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.policy, self.router = queue.policy, queue.router
        self.resolver = EligibilityResolver(queue.policy, AcceptedMetadata(queue))

    async def list(
        self, access: AccessContext, kg: UUID, state: ManagementState, page: PageRequest
    ) -> ManagedMaterialPage:
        try:
            state = TypeAdapter(ManagementState).validate_python(state)
        except ValidationError:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "The management state is invalid."
            ) from None
        page = PageRequest.model_validate(page)
        current = await self.policy.revalidate(access, action=Action.REPLACE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        current = await self.policy.resolve_access(
            current.principal, [kg], Action.REPLACE, org_id=current.org_id
        )
        scope = await self.resolver.resolve(
            current, SearchRequest(kg_ids=(kg,), query="material management"), kg
        )
        ns = {"org_id": str(current.org_id), "kg_id": str(kg)}
        binding = ns | {
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "kind": current.principal.kind,
            "policy_revision": current.policy_revision,
            "visibility_revision": scope.visibility_revision,
            "manifest_id": str(scope.view.manifest_id),
            "generation": str(scope.view.generation),
            "state": state,
            "limit": page.limit,
        }
        after = ""
        if page.cursor:
            cursor = await self.database.material_management_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(ErrorCode.CURSOR_EXPIRED, "The management cursor scope changed.")
            after = cursor["after"]
        query: dict[str, Any] = ns | {"id": {"$gt": after}, "purged": {"$ne": True}}
        if state == "pending":
            query |= {
                "active_version_id": None,
                "excluded": {"$ne": True},
                "tombstoned": {"$ne": True},
            }
        else:
            query["$or"] = [{"excluded": True}, {"tombstoned": True}]
        stores = await self.router.for_sources(current, kg)
        items: list[ManagedMaterial] = []
        last, more, count = after, False, 0
        async for raw in self.database.material_access.find(query).sort("id", 1).limit(1001):
            count += 1
            if count > 1000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "The management scan exceeds its bounded window."
                )
            identifier = UUID(raw["id"])
            visible = await self.policy._material_acl_record(current, kg, identifier)
            if visible is None or visible.get("purged"):
                continue
            if len(items) == page.limit:
                more = True
                break
            try:
                record = await stores.authority._require(
                    "materials", ScopedRef(org_id=current.org_id, kg_id=kg, id=identifier)
                )
                canonical = MaterialRecord.model_validate(
                    {key: value for key, value in record.items() if key != "_id"}
                )
                version = visible.get("active_version_id")
                if version:
                    ref = ScopedRef(org_id=current.org_id, kg_id=kg, id=UUID(version))
                    source = await stores.authority._require("material_versions", ref)
                    await stores.authority.require_version(ref)
                    if source["material"] != ScopedRef(
                        org_id=current.org_id, kg_id=kg, id=identifier
                    ).model_dump(mode="json"):
                        raise ValueError("source ownership differs")
                item_state: Literal["pending", "excluded", "deleted"] = (
                    "deleted"
                    if visible.get("tombstoned")
                    else "excluded"
                    if visible.get("excluded")
                    else "pending"
                )
                expiry = visible.get("purge_after")
                if expiry is not None and not isinstance(expiry, datetime):
                    raise ValueError("invalid retention")
                expiry = expiry.replace(tzinfo=UTC) if expiry else None
                items.append(
                    ManagedMaterial(
                        org_id=current.org_id,
                        kg_id=kg,
                        id=identifier,
                        title=canonical.title,
                        revision=visible["revision"],
                        state=item_state,
                        retention_until=expiry,
                        can_restore=item_state == "excluded"
                        or (
                            item_state == "deleted"
                            and expiry is not None
                            and expiry > datetime.now(UTC)
                        ),
                    )
                )
            except (ValueError, TypeError, KeyError, ValidationError):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Material management ownership differs."
                ) from None
            except DomainError as error:
                if error.code == ErrorCode.RESOURCE_NOT_ACCESSIBLE:
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT, "Material management ownership differs."
                    ) from None
                raise
            last = raw["id"]
        await self.resolver.revalidate(scope)
        token = None
        if more:
            token = secrets.token_urlsafe(32)
            await self.database.material_management_cursors.create_index(
                "expires_at", expireAfterSeconds=0
            )
            await self.database.material_management_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(token.encode()).hexdigest(),
                    "after": last,
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        await self.policy.revalidate(current, action=Action.REPLACE)
        await self.resolver.revalidate(scope)
        return ManagedMaterialPage(
            org_id=current.org_id, kg_id=kg, items=tuple(items), next_cursor=token
        )
