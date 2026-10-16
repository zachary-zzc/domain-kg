"""Scoped history paging uses the caller's current source and publication fences."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation import authorize_curation
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.history_models import HistoryPage, HistoryRequest


class RevisionHistory:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def get(self, access: AccessContext, request: HistoryRequest) -> HistoryPage:
        request = HistoryRequest.model_validate(request)
        current = await self.queue.policy.revalidate(access, action=Action.READ)
        graph = GraphService(self.queue)
        scope = await graph.resolver.resolve(
            current,
            SearchRequest(
                kg_ids=(request.kg_id,),
                query=str(request.target_id),
                mode="graph",
            ),
            request.kg_id,
        )
        ns = {"org_id": str(current.org_id), "kg_id": str(request.kg_id)}
        binding = ns | {
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "principal_kind": current.principal.kind,
            "policy_revision": current.policy_revision,
            "visibility_revision": scope.visibility_revision,
            "manifest_id": str(scope.current_view.manifest_id),
            "generation": str(scope.current_view.generation),
            "target_id": str(request.target_id),
            "kind": request.kind,
            "limit": request.limit,
        }
        cursor = None
        if request.cursor:
            cursor = await self.database.revision_cursors.find_one(
                ns
                | {
                    "_id": hashlib.sha256(request.cursor.encode()).hexdigest(),
                    "expires_at": {"$gt": datetime.now(UTC)},
                }
            )
            if cursor is None or any(cursor.get(key) != value for key, value in binding.items()):
                raise DomainError(ErrorCode.CURSOR_EXPIRED, "The history cursor scope changed.")
        data = await graph.repository.open(scope)
        if request.kind == "node":
            visible = graph.repository.node(data, request.target_id) is not None
        else:
            visible = any(
                row.claim and row.claim.assertion.id == request.target_id for row in data.supports
            ) or any(
                isinstance(manual, ManualRelation)
                and manual.logical_assertion.id == request.target_id
                and any(value is not None for value in manual.fields.model_dump().values())
                for manual in data.curations
            )
        if not visible:
            raise inaccessible_reference()
        query: dict[str, Any] = ns | {
            "target_id": str(request.target_id),
            "kind": request.kind,
            "state": {"$in": ["pending", "accepted"]},
            "$or": [
                {"public": True, "material_ids": []},
                {
                    "public": False,
                    "material_ids.0": {"$exists": True},
                    "material_ids": {
                        "$not": {
                            "$elemMatch": {
                                "$nin": [
                                    str(source.material_id) for source in scope.visible_sources
                                ]
                            }
                        }
                    },
                },
            ],
        }
        if cursor:
            query["$and"] = [
                {
                    "$or": [
                        {"created_at": {"$gt": cursor["after_time"]}},
                        {"created_at": cursor["after_time"], "id": {"$gt": cursor["after_id"]}},
                    ]
                }
            ]
        rows = (
            await self.database.curation_revisions.find(query)
            .sort([("created_at", 1), ("id", 1)])
            .limit(request.limit + 1)
            .to_list()
        )
        entries = []
        for row in rows[: request.limit]:
            record = await authorize_curation(
                self.queue,
                current,
                UUID(row["id"]),
                kg_id=request.kg_id,
            )
            entries.append(
                {
                    "revision_id": record["id"],
                    "target_id": record["target_id"],
                    "target_revision": record["expected_revision"] + 1,
                    "kind": record["kind"],
                    "state": record["state"],
                    "public": record["public"],
                    "material_ids": record["material_ids"],
                    "provenance": record["provenance"],
                    "created_at": record["created_at"].replace(tzinfo=UTC),
                    "before": record["before"],
                    "after": record["after"],
                    "rollback_of": record.get("rollback_of"),
                    "job_id": record["job_id"],
                    "published_revision": record.get("receipt", {}).get("published_revision"),
                }
            )
        await graph.resolver.revalidate(scope)
        next_cursor = None
        if len(rows) > request.limit:
            next_cursor = secrets.token_urlsafe(32)
            await self.database.revision_cursors.create_index("expires_at", expireAfterSeconds=0)
            await self.database.revision_cursors.insert_one(
                binding
                | {
                    "_id": hashlib.sha256(next_cursor.encode()).hexdigest(),
                    "after_time": rows[request.limit - 1]["created_at"],
                    "after_id": rows[request.limit - 1]["id"],
                    "expires_at": datetime.now(UTC) + timedelta(minutes=15),
                }
            )
        await graph.resolver.revalidate(scope)
        return HistoryPage.model_validate({"items": entries, "next_cursor": next_cursor})
