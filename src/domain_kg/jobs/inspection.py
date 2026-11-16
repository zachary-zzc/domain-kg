"""Current-policy job paging with opaque, principal and view-bound cursors."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.engine.scope import EligibilityResolver
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.service import AcceptedMetadata
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobReceipt
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import FrozenModel, Namespace, PageRequest
from domain_kg.retrieval.models import SearchRequest

JobView = Literal["all", "active", "attention", "finished", "incomplete"]
FINAL_STAGES = ("ready", "ready_with_warnings", "failed", "cancelled", "repair_required")
ATTENTION_STAGES = ("failed", "cancelled", "repair_required", "ready_with_warnings")
SOURCE_KINDS = ("ingest", "replace", "import", "restore_material", "reprocess_material")


class JobFilters(FrozenModel):
    view: JobView = "all"
    sources_only: bool = False

    def matches(self, receipt: JobReceipt) -> bool:
        return (
            (not self.sources_only or receipt.kind in SOURCE_KINDS)
            and (self.view != "active" or receipt.stage not in FINAL_STAGES)
            and (self.view != "attention" or receipt.stage in ATTENTION_STAGES)
            and (self.view != "finished" or receipt.stage in ("ready", "ready_with_warnings"))
            and (self.view != "incomplete" or receipt.stage not in ("ready", "ready_with_warnings"))
        )


class JobPage(Namespace):
    items: Annotated[tuple[JobReceipt, ...], Field(max_length=100)]
    next_cursor: str | None = None

    @model_validator(mode="after")
    def exact_namespace(self) -> Self:
        if any((item.org_id, item.kg_id) != (self.org_id, self.kg_id) for item in self.items):
            raise ValueError("job receipts must share their page namespace")
        return self


class JobInspection:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database
        self.resolver = EligibilityResolver(queue.policy, AcceptedMetadata(queue))

    async def list(
        self, access: AccessContext, kg: UUID, page: PageRequest, filters: JobFilters | None = None
    ) -> JobPage:
        page = PageRequest.model_validate(page)
        filters = JobFilters.model_validate(filters) if filters is not None else JobFilters()
        current = await self.queue.policy.revalidate(access, action=Action.READ)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        current = await self.queue.policy.resolve_access(
            current.principal, [kg], Action.READ, org_id=current.org_id
        )
        scope = await self.resolver.resolve(
            current, SearchRequest(kg_ids=(kg,), query="job list"), kg
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
            "published_revision": scope.view.revision,
            "limit": page.limit,
            "order": "newest-v1",
            "filters": filters.model_dump(mode="json"),
        }
        after: dict[str, Any] | None = None
        if page.cursor:
            cursor = await self.database.job_read_cursors.find_one(
                {
                    "_id": hashlib.sha256(page.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(ErrorCode.CURSOR_EXPIRED, "The job cursor scope changed.")
            after = cursor["after"]
        items: list[JobReceipt] = []
        last, more = after, False
        count = 0
        query: dict[str, Any] = dict(ns)
        if filters.sources_only:
            query["kind"] = {"$in": list(SOURCE_KINDS)}
        if filters.view == "active":
            query["stage"] = {"$nin": list(FINAL_STAGES)}
        elif filters.view == "attention":
            query["stage"] = {"$in": list(ATTENTION_STAGES)}
        elif filters.view == "finished":
            query["stage"] = {"$in": ["ready", "ready_with_warnings"]}
        elif filters.view == "incomplete":
            query["stage"] = {"$nin": ["ready", "ready_with_warnings"]}
        if after:
            query["$or"] = [
                {"created_at": {"$lt": after["created_at"]}},
                {"created_at": after["created_at"], "id": {"$lt": after["id"]}},
            ]
        async for raw in (
            self.database.jobs.find(query).sort([("created_at", -1), ("id", -1)]).limit(1001)
        ):
            count += 1
            if count > 1000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "The job scan exceeds its bounded window."
                )
            try:
                receipt = await self.queue.get(current, UUID(raw["id"]))
            except DomainError as error:
                if error.code in (ErrorCode.FORBIDDEN, ErrorCode.RESOURCE_NOT_ACCESSIBLE):
                    continue
                raise
            # A worker may advance after the database scan; apply the same view to
            # its authorized receipt before counting the visible page.
            if not filters.matches(receipt):
                continue
            if len(items) == page.limit:
                more = True
                break
            items.append(receipt)
            last = {"id": raw["id"], "created_at": raw["created_at"]}
        await self.resolver.revalidate(scope)
        fresh = [await self.queue.get(current, item.job_id) for item in items]
        items = [item for item in fresh if filters.matches(item)]
        token = None
        if more:
            token = secrets.token_urlsafe(32)
            await self.database.job_read_cursors.create_index("expires_at", expireAfterSeconds=0)
            await self.database.job_read_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(token.encode()).hexdigest(),
                    "after": last,
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        await self.resolver.revalidate(scope)
        return JobPage(org_id=current.org_id, kg_id=kg, items=tuple(items), next_cursor=token)
