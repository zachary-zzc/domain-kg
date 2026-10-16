"""Current, authorized revision counters for an explicitly selected curation scope."""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid5

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.service import GraphService
from domain_kg.graph.views import EdgeView, NodeView
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.service import digest
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import ReadScope


@dataclass(frozen=True)
class EditContext:
    scope: ReadScope
    source_ids: tuple[UUID, ...]
    public: bool
    expected_revision: int
    node: NodeView | None
    edges: tuple[EdgeView, ...]


class CurationInspection:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.graph = queue, GraphService(queue)

    async def edit(
        self,
        access: AccessContext,
        kg: UUID,
        target: UUID,
        kind: Literal["node", "relation"],
        *,
        source_ids: tuple[UUID, ...] = (),
        public: bool = False,
    ) -> EditContext:
        current = await self.queue.policy.revalidate(access, action=Action.CURATE)
        if current.principal.kind != "human" or kg not in current.kg_ids:
            raise inaccessible_reference()
        if public and (
            source_ids
            or not any(grant.kg_id == kg and grant.role == "owner" for grant in current.grants)
        ):
            raise inaccessible_reference()
        if len(source_ids) > 100 or len(set(source_ids)) != len(source_ids):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Select distinct source owners.")
        scope = await self.graph.resolver.resolve(
            current,
            SearchRequest(
                kg_ids=(kg,), query=str(target), filters=SearchFilters.for_curation(source_ids)
            ),
            kg,
        )
        data = await self.graph.repository.open(scope)
        node = self.graph.repository.node(data, target) if kind == "node" else None
        rows = tuple(
            row
            for row in data.supports
            if row.assertion and row.assertion.assertion.id == target and row.source
        )
        if kind == "node":
            if node is None:
                raise inaccessible_reference()
            if node.id != target:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Use the current canonical target.")
            if node.truncation and not (source_ids or public):
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Select explicit source owners.")
            owners = {item.material_id for item in node.observations}
        else:
            if not rows:
                raise inaccessible_reference()
            owners = {row.source.material_id for row in rows if row.source}
        selected = () if public else tuple(sorted(set(source_ids) or owners, key=str))
        if not public and (not selected or len(selected) > 100 or not set(selected) <= owners):
            raise inaccessible_reference()
        identity: dict[str, object] = {"public": public, "material_ids": list(map(str, selected))}
        if kind == "relation":
            identity["kind"] = "relation"
        scope_key = digest(identity)
        row = await self.queue.database.curation_targets.find_one(
            {
                "org_id": str(current.org_id),
                "kg_id": str(kg),
                "id": str(uuid5(target, f"curation-scope/{scope_key}")),
            }
        )
        if row and (
            (row.get("kind"), row.get("target_id"), row.get("scope_key"))
            != (kind, str(target), scope_key)
            or type(row.get("revision")) is not int
            or row["revision"] < 0
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Curation target ownership differs."
            )
        await self.graph.resolver.revalidate(scope)
        await self.queue.policy.revalidate(current, action=Action.CURATE)
        return EditContext(
            scope,
            selected,
            public,
            row["revision"] if row else 0,
            node,
            tuple(self.graph.repository.edge(row, data=data) for row in rows),
        )
