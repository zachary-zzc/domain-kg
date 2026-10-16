"""Bounded indexed adjacency over accepted, visible source-owned support rows."""

from dataclasses import dataclass
from functools import cached_property
from typing import Any
from uuid import UUID

from neo4j.exceptions import DriverError, Neo4jError

from domain_kg.engine.scope import EligibilityResolver, ScopePredicates
from domain_kg.errors import BranchUnavailable, DomainError, ErrorCode
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.models import AssertionRecord
from domain_kg.graph.views import EdgeView, NodeView, SubgraphView
from domain_kg.graph.visibility import NodeMasks
from domain_kg.ingestion.resolved import ResolvedEntity
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import Namespace
from domain_kg.retrieval.filters import matches_observation, matches_support
from domain_kg.retrieval.models import SubgraphRequest
from domain_kg.revisions.models import ReadScope
from domain_kg.revisions.projections import CurationEmbeddedDocument, ProjectionBody
from domain_kg.revisions.publication import accepted_document, projection_rows, receipt_for
from domain_kg.storage.neo4j import GraphProjection, inconsistent

# One binding pairs source identity with its accepted attempt. Independent ANYs
# would admit cross-products between accepted sources and abandoned attempts.
ACCEPTED = """
n.org_id=$org_id AND n.kg_id=$kg_id AND n.generation=$generation
AND any(b IN $accepted_projections WHERE
 n.attempt_id=b.attempt_id AND n.manifest_id=b.manifest_id AND n.manifest_hash=b.manifest_hash
 AND ((n.material_id=b.material_id AND n.source_version_id=b.source_version_id)
 OR (n.curation_revision_id=b.curation_revision_id AND b.curation_revision_id IS NOT NULL)))
"""
ADJACENCY = (
    """
UNWIND $seed_ids AS seed
MATCH (a:EntityAnchor {org_id:$org_id,kg_id:$kg_id,entity_id:seed})
 <-[:SUBJECT|TARGET]-(n:ScopedProjection)
WHERE n.kind IN ['support','curation'] AND """
    + ACCEPTED
    + """
AND n.logical_id IN $support_ids
WITH DISTINCT n ORDER BY n.logical_id LIMIT $limit
RETURN n.logical_id AS id
"""
)


@dataclass(frozen=True)
class GraphData:
    scope: ReadScope
    rows: tuple[ProjectionBody, ...]
    curations: tuple[ManualNode | ManualRelation, ...] = ()

    def field(self, observation: ResolvedEntity, key: str) -> Any:
        value = getattr(observation, key)
        for manual in sorted(
            self.curations, key=lambda item: (item.created_at, str(item.curation.id))
        ):
            if not isinstance(manual, ManualNode):
                continue
            if manual.entity != observation.entity or (
                not manual.public and observation.material_id not in manual.material_ids
            ):
                continue
            proposed = getattr(manual.changes, key)
            if proposed is not None:
                value = proposed
        return value

    @cached_property
    def _observations_by_entity(self) -> dict[UUID, tuple[ResolvedEntity, ...]]:
        grouped: dict[UUID, list[ResolvedEntity]] = {}
        for row in self.rows:
            if row.observation:
                grouped.setdefault(row.observation.entity.id, []).append(row.observation)
        return {key: tuple(value) for key, value in grouped.items()}

    @cached_property
    def _typed_nodes(self) -> dict[UUID, tuple[ManualNode, ...]]:
        grouped: dict[UUID, list[ManualNode]] = {}
        for item in sorted(
            self.curations, key=lambda item: (item.created_at, str(item.curation.id))
        ):
            if isinstance(item, ManualNode) and item.changes.entity_type is not None:
                grouped.setdefault(item.entity.id, []).append(item)
        return {key: tuple(value) for key, value in grouped.items()}

    def human_types(self, entity: UUID, materials: tuple[UUID, ...]) -> tuple[str, ...]:
        typed = self._typed_nodes.get(entity, ())
        contexts: tuple[UUID | None, ...] = materials or (
            None,
            *sorted({source for node in typed for source in node.material_ids}, key=str),
        )
        result = []
        for source in contexts:
            eligible = [node for node in typed if node.public or source in node.material_ids]
            if eligible and (value := eligible[-1].changes.entity_type) is not None:
                result.append(value)
        return tuple(result)

    def endpoint_types(self, row: ProjectionBody) -> tuple[str, ...] | None:
        if not self.scope.filters.entity_types:
            return None
        if row.subject_observation and row.target_observation:
            return tuple(
                self.field(item, "entity_type")
                for item in (row.subject_observation, row.target_observation)
            )
        if row.manual_relation and row.claim:
            return tuple(
                self.field(item, "entity_type")
                for endpoint in (row.claim.assertion.subject, row.claim.assertion.target)
                for item in self._observations_by_entity.get(endpoint.id, ())
                if row.manual_relation.public
                or item.material_id in row.manual_relation.material_ids
            ) + tuple(
                value
                for endpoint in (row.claim.assertion.subject, row.claim.assertion.target)
                for value in self.human_types(endpoint.id, row.manual_relation.material_ids)
            )
        return ()

    @cached_property
    def supports(self) -> tuple[ProjectionBody, ...]:
        return tuple(
            row
            for row in self.rows
            if matches_support(row, self.scope.filters, endpoint_types=self.endpoint_types(row))
        )

    @cached_property
    def observations(self) -> tuple[ResolvedEntity, ...]:
        support_observations = {
            observation.id
            for row in self.supports
            for observation in (row.subject_observation, row.target_observation)
            if observation
        }
        return tuple(
            row.observation
            for row in self.rows
            if row.observation
            and matches_observation(
                row, self.scope.filters, entity_type=self.field(row.observation, "entity_type")
            )
            and (
                not self.scope.filters.requires_assertion_support
                or row.observation.id in support_observations
                or any(
                    support.manual_relation
                    and support.claim
                    and row.observation.entity
                    in (support.claim.assertion.subject, support.claim.assertion.target)
                    and (
                        support.manual_relation.public
                        or row.observation.material_id in support.manual_relation.material_ids
                    )
                    for support in self.supports
                )
            )
        )

    @cached_property
    def manual_nodes(self) -> tuple[ManualNode, ...]:
        result = []
        for manual in self.curations:
            if not isinstance(manual, ManualNode):
                continue
            if not any(value is not None for value in manual.changes.model_dump().values()):
                continue
            if manual.entity.id in self.scope.filters.exclude_entity_ids or (
                self.scope.filters.entity_ids
                and manual.entity.id not in self.scope.filters.entity_ids
            ):
                continue
            if self.scope.filters.entity_types:
                types = self.human_types(manual.entity.id, manual.material_ids) + tuple(
                    self.field(item, "entity_type")
                    for item in self._observations_by_entity.get(manual.entity.id, ())
                    if manual.public or item.material_id in manual.material_ids
                )
                if not set(types).intersection(self.scope.filters.entity_types):
                    continue
            if self.scope.filters.requires_assertion_support:
                observations = [item for item in self.observations if item.entity == manual.entity]
                source_support = bool(observations) and (
                    manual.public
                    or set(manual.material_ids).issubset(
                        {item.material_id for item in observations}
                    )
                )
                human_support = any(
                    row.manual_relation
                    and row.claim
                    and manual.entity in (row.claim.assertion.subject, row.claim.assertion.target)
                    and (
                        manual.public
                        or row.manual_relation.public
                        or set(manual.material_ids).intersection(row.manual_relation.material_ids)
                    )
                    for row in self.supports
                )
                if not source_support and not human_support:
                    continue
            result.append(manual)
        return tuple(sorted(result, key=lambda item: (item.created_at, str(item.curation.id))))

    def relation_annotations(self, row: ProjectionBody) -> tuple[ManualRelation, ...]:
        if row.claim is None:
            return ()
        return tuple(
            sorted(
                (
                    manual
                    for manual in self.curations
                    if isinstance(manual, ManualRelation)
                    and manual.fact is None
                    and manual.logical_assertion.id == row.claim.assertion.id
                    and (
                        manual.public
                        or row.source is not None
                        and row.source.material_id in manual.material_ids
                    )
                    and any(value is not None for value in manual.fields.model_dump().values())
                ),
                key=lambda item: (item.created_at, str(item.curation.id)),
            )
        )

    @cached_property
    def manual_rows(self) -> tuple[ProjectionBody, ...]:
        if "manual" not in self.scope.filters.support_statuses:
            return ()
        result = []
        for row in self.rows:
            if row.manual_node and row.manual_node in self.manual_nodes:
                result.append(row)
            elif row.manual_relation:
                if row.claim:
                    if row in self.supports:
                        result.append(row)
                elif not (
                    self.scope.filters.requires_assertion_support
                    or self.scope.filters.exclude_entity_ids
                ) or any(
                    row.manual_relation in self.relation_annotations(support)
                    for support in self.supports
                ):
                    # A description annotates an eligible original claim. It does
                    # not add a new support or fabricate a document quotation.
                    result.append(row)
        return tuple(result)


async def scoped_graph_data(
    queue: JobQueue,
    scope: ReadScope,
    rows: tuple[ProjectionBody, ...],
    curations: tuple[ManualNode | ManualRelation, ...],
) -> GraphData:
    if not scope.node_fences:
        return GraphData(scope, rows, curations)
    masks = NodeMasks(
        Namespace(org_id=scope.view.org_id, kg_id=scope.view.kg_id), scope.node_fences
    )
    contexts: dict[UUID, AssertionRecord] = {}
    annotations = [
        item for item in curations if isinstance(item, ManualRelation) and item.fact is None
    ]
    if annotations:
        stores = await queue.router.for_sources(scope.access, scope.view.kg_id)
        for manual in annotations:
            raw = await stores.authority._require("assertions", manual.logical_assertion)
            contexts[manual.curation.id] = AssertionRecord.model_validate(
                {key: value for key, value in raw.items() if key != "_id"}
            )
    chunks = masks.blocked_chunks(rows)
    visible_rows = tuple(
        row
        for row in rows
        if not masks.hides(
            row, chunks, relation_context=contexts.get(row.curation.id) if row.curation else None
        )
    )
    visible_directives = tuple(
        manual
        for manual in curations
        if not masks.hides_directive(manual, relation_context=contexts.get(manual.curation.id))
    )
    return GraphData(scope, visible_rows, visible_directives)


class GraphRepository:
    def __init__(self, queue: JobQueue, resolver: EligibilityResolver) -> None:
        self.queue, self.resolver = queue, resolver

    async def open(self, scope: ReadScope) -> GraphData:
        await self.resolver.revalidate(scope)
        rows: list[ProjectionBody] = []
        curations: list[ManualNode | ManualRelation] = []
        if len(scope.visible_sources) + len(scope.visible_curations) > 1000:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "This read exceeds the accepted-source budget."
            )
        binding = await self.queue.router.for_graph(scope.access, scope.view.kg_id)
        for accepted in scope.projections:
            if (
                accepted.source not in scope.visible_sources
                and accepted.curation not in scope.visible_curations
            ):
                continue
            try:
                document, batch, receipts = await accepted_document(
                    self.queue, scope.access, accepted
                )
                expected = projection_rows(document, batch)
                receipt = receipt_for(batch, "graph", expected)
                if (
                    receipt not in receipts
                    or not await GraphProjection(binding).verify(expected)
                    or not await binding.verify(receipt)
                ):
                    raise inconsistent()
                rows.extend(
                    ProjectionBody.model_validate(row.model_dump(exclude={"vector"}))
                    for row in document.rows
                )
                if isinstance(document, CurationEmbeddedDocument):
                    curations.append(document.directive)
                if len(rows) > 20000:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "This read exceeds the sealed-row budget."
                    )
            except (DomainError, DriverError, Neo4jError) as error:
                if isinstance(error, DomainError):
                    if error.code not in (
                        ErrorCode.PROJECTION_INCONSISTENT,
                        ErrorCode.BACKEND_UNAVAILABLE,
                    ):
                        raise
                await self.queue.database.repair_requests.update_one(
                    {
                        "org_id": str(scope.view.org_id),
                        "kg_id": str(scope.view.kg_id),
                        "manifest_id": str(accepted.manifest_id),
                    },
                    {
                        "$setOnInsert": {
                            "state": "pending_authorized_repair",
                            "binding": accepted.model_dump(mode="json"),
                        }
                    },
                    upsert=True,
                )
                if isinstance(error, DomainError):
                    raise
                raise BranchUnavailable("The graph backend is unavailable.") from error
        await self.resolver.revalidate(scope)
        return await scoped_graph_data(self.queue, scope, tuple(rows), tuple(curations))

    def node(self, data: GraphData, identifier: UUID) -> NodeView | None:
        observations = sorted(
            (item for item in data.observations if item.entity.id == identifier),
            key=lambda item: (str(item.material_id), str(item.version_id), str(item.id)),
        )
        manuals = tuple(item for item in data.manual_nodes if item.entity.id == identifier)
        supports = [
            row
            for row in data.supports
            if row.claim
            and identifier
            in (
                row.claim.assertion.subject.id,
                row.claim.assertion.target.id,
            )
        ]
        # Eligible human claims retain opaque endpoints when source observations
        # disappear. Removed sources are already excluded by the read scope.
        manual_endpoint = any(row.manual_relation for row in supports)
        if not observations and not manuals and not manual_endpoint:
            return None
        neighbors = {
            endpoint.id
            for row in supports
            if row.claim
            for endpoint in (row.claim.assertion.subject, row.claim.assertion.target)
        } - {identifier}
        # Counts cover all eligible rows; the response includes at most 50
        # observations and derives every displayed field from those same rows.
        selected = observations[:50]
        names = [item.changes.name for item in manuals if item.changes.name is not None]
        return NodeView(
            kg_id=data.scope.view.kg_id,
            id=identifier,
            name=names[-1]
            if names
            else (data.field(selected[0], "name") if selected else str(identifier)),
            aliases=tuple(
                sorted(
                    {alias for item in selected for alias in data.field(item, "aliases")}
                    | {alias for manual in manuals for alias in (manual.changes.aliases or ())}
                )
            ),
            descriptions=tuple(
                sorted(
                    {
                        data.field(item, "description")
                        for item in selected
                        if data.field(item, "description")
                    }
                    | {
                        manual.changes.description
                        for manual in manuals
                        if manual.changes.description
                    }
                )
            ),
            entity_types=tuple(
                sorted(
                    {data.field(item, "entity_type") for item in selected}
                    | {
                        manual.changes.entity_type
                        for manual in manuals
                        if manual.changes.entity_type
                    }
                )
            ),
            observations=tuple(selected),
            observation_count=len(observations),
            support_count=len(supports),
            neighbor_count=len(neighbors),
            truncation=len(observations) > 50,
            curations=manuals,
            tags=tuple(sorted({tag for manual in manuals for tag in (manual.changes.tags or ())})),
        )

    @staticmethod
    def edge(row: ProjectionBody, *, data: GraphData | None = None) -> EdgeView:
        if not row.claim:
            raise inconsistent()
        fact, support = row.claim.assertion, row.claim.support
        annotations = data.relation_annotations(row) if data else ()
        description = row.manual_relation.fields.description if row.manual_relation else None
        tags = row.manual_relation.fields.tags or () if row.manual_relation else ()
        for manual in annotations:
            if manual.fields.description is not None:
                description = manual.fields.description
            if manual.fields.tags is not None:
                tags = manual.fields.tags
        return EdgeView(
            subject_id=fact.subject.id,
            target_id=fact.target.id,
            assertion_id=fact.id,
            support_id=support.id,
            predicate=fact.predicate,
            polarity=fact.polarity,
            confidence=support.confidence,
            source=row.source,
            spans=support.spans,
            curation_revision_id=row.curation.id if row.curation else None,
            provenance=support.provenance,
            status=support.status,
            description=description,
            tags=tags,
            annotations=annotations,
        )

    async def adjacent(
        self, data: GraphData, seeds: tuple[UUID, ...], limit: int
    ) -> tuple[ProjectionBody, ...]:
        if not seeds or not data.supports:
            return ()
        if len(seeds) > 200 or not 1 <= limit <= 401:
            raise ValueError("adjacency limits exceed the bounded traversal contract")
        binding = await self.queue.router.for_graph(data.scope.access, data.scope.view.kg_id)
        by_id = {str(row.id): row for row in data.supports}
        try:
            records, _, _ = await binding.driver.execute_query(
                ADJACENCY,
                **ScopePredicates(data.scope).graph_parameters,
                seed_ids=[str(seed) for seed in seeds],
                support_ids=list(by_id),
                limit=limit,
            )
        except (DriverError, Neo4jError) as error:
            raise BranchUnavailable("The graph backend is unavailable.") from error
        result = tuple(by_id[item["id"]] for item in records)
        if any(
            not row.claim
            or not {
                row.claim.assertion.subject.id,
                row.claim.assertion.target.id,
            }.intersection(seeds)
            for row in result
        ):
            raise inconsistent()
        return result

    async def traverse(
        self, scope: ReadScope, request: SubgraphRequest, *, data: GraphData | None = None
    ) -> SubgraphView:
        request = SubgraphRequest.model_validate(request)
        if request.kg_id != scope.view.kg_id or request.filters != scope.filters:
            raise ValueError("graph request and immutable scope differ")
        data = data or await self.open(scope)
        if data.scope != scope:
            raise ValueError("sealed graph data belongs to another scope")
        nodes = {
            identifier: node
            for identifier in sorted(set(request.seed_ids), key=str)
            if (node := self.node(data, identifier)) is not None
        }
        frontier = tuple(nodes)
        visited: set[UUID] = set()
        edges: dict[UUID, EdgeView] = {}
        truncated = False
        for _ in range(request.max_hops):
            if not frontier:
                break
            # The extra row proves truncation without following another edge.
            rows = await self.adjacent(data, frontier, request.max_edges + 1)
            next_frontier: set[UUID] = set()
            for row in rows:
                edge = self.edge(row, data=data)
                if edge.support_id in edges:
                    continue
                additions = {edge.subject_id, edge.target_id} - nodes.keys()
                if (
                    len(edges) >= request.max_edges
                    or len(nodes) + len(additions) > request.max_nodes
                ):
                    truncated = True
                    continue
                new_nodes = {identifier: self.node(data, identifier) for identifier in additions}
                if any(node is None for node in new_nodes.values()):
                    continue
                for identifier, node in new_nodes.items():
                    if node is not None:
                        nodes[identifier] = node
                edges[edge.support_id] = edge
                next_frontier.update(additions - visited)
            visited.update(frontier)
            frontier = tuple(sorted(next_frontier - visited, key=str))
        await self.resolver.revalidate(scope)
        return SubgraphView(
            kg_id=scope.view.kg_id,
            revision=scope.current_view.revision,
            nodes=tuple(nodes[key] for key in sorted(nodes, key=str)),
            edges=tuple(edges[key] for key in sorted(edges, key=str)),
            truncation=truncated,
        )

    async def explain_adjacency(self, scope: ReadScope, seed: UUID) -> tuple[str, ...]:
        data = await self.open(scope)
        binding = await self.queue.router.for_graph(scope.access, scope.view.kg_id)
        _, summary, _ = await binding.driver.execute_query(
            "EXPLAIN " + ADJACENCY,
            **ScopePredicates(scope).graph_parameters,
            seed_ids=[str(seed)],
            support_ids=[str(row.id) for row in data.supports],
            limit=50,
        )

        def operators(plan: Any) -> list[str]:
            return [
                str(plan.get("operatorType", "")),
                *[name for child in plan.get("children", []) for name in operators(child)],
            ]

        await self.resolver.revalidate(scope)
        return tuple(operators(summary.plan or {}))
