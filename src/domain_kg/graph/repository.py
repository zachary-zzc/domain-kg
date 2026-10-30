"""Bounded indexed adjacency over accepted, visible source-owned support rows."""

from dataclasses import dataclass
from functools import cached_property
from typing import Any
from uuid import UUID

from neo4j.exceptions import DriverError, Neo4jError

from domain_kg.engine.scope import EligibilityResolver, ScopePredicates
from domain_kg.errors import BranchUnavailable, DomainError, ErrorCode
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.identity_models import IdentityWitness, ManualIdentity
from domain_kg.graph.identity_partition import IdentityContext, IdentityPartition
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.models import AssertionRecord
from domain_kg.graph.views import EdgeView, NodeView, SubgraphView
from domain_kg.graph.visibility import NodeMasks
from domain_kg.imports.evidence import source_origin, source_status
from domain_kg.ingestion.resolved import ResolvedEntity
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import Namespace, SourceVersion
from domain_kg.retrieval.filters import matches_observation, matches_support
from domain_kg.retrieval.models import SearchFilters, SubgraphRequest
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
    curations: tuple[ManualNode | ManualRelation | ManualIdentity, ...] = ()

    @cached_property
    def identities(self) -> IdentityPartition | None:
        directives = tuple(item for item in self.curations if isinstance(item, ManualIdentity))
        if not directives:
            return None
        visible = {source.material_id for source in self.scope.visible_sources}
        if any(
            item.curation not in self.scope.visible_curations
            or not item.public
            and not set(item.material_ids).issubset(visible)
            for item in directives
        ):
            raise inconsistent()
        return IdentityPartition(
            Namespace(org_id=self.scope.view.org_id, kg_id=self.scope.view.kg_id), directives
        )

    @cached_property
    def identity_contexts(self) -> tuple[IdentityContext, ...]:
        contexts: set[tuple[UUID, ...] | None] = {
            (source.material_id,) for source in self.scope.visible_sources
        }
        contexts.update(
            tuple(sorted(item.material_ids, key=str)) if not item.public else None
            for item in self.curations
        )
        return tuple(sorted(contexts, key=str))

    def entity_id(self, original: UUID, context: IdentityContext) -> UUID:
        return self.identities.canonical(original, context) if self.identities else original

    @staticmethod
    def context(row: ProjectionBody) -> IdentityContext:
        if row.source:
            return row.source.material_id
        manual = row.manual_node or row.manual_relation
        return manual.material_ids if manual and not manual.public else None

    def endpoints(self, row: ProjectionBody) -> tuple[UUID, ...]:
        if not row.claim:
            return ()
        return tuple(
            self.entity_id(item.id, self.context(row))
            for item in (row.claim.assertion.subject, row.claim.assertion.target)
        )

    def effective_filters(self, context: IdentityContext) -> SearchFilters:
        filters = self.scope.filters
        if not self.identities:
            return filters
        return filters.model_copy(
            update={
                "entity_ids": tuple(self.entity_id(item, context) for item in filters.entity_ids),
                "exclude_entity_ids": tuple(
                    self.entity_id(item, context) for item in filters.exclude_entity_ids
                ),
            }
        )

    def identity_witness(self, original: UUID, context: IdentityContext) -> IdentityWitness | None:
        if self.identities is None or not (proof := self.identities.witnesses(original, context)):
            return None
        owners = (context,) if isinstance(context, UUID) else (context or ())
        return IdentityWitness(
            org_id=self.scope.view.org_id,
            kg_id=self.scope.view.kg_id,
            original_id=original,
            canonical_id=self.entity_id(original, context),
            curations=proof,
            material_ids=tuple(sorted(owners, key=str)),
        )

    def field(self, observation: ResolvedEntity, key: str) -> Any:
        value = getattr(observation, key)
        for manual in sorted(
            self.curations, key=lambda item: (item.created_at, str(item.curation.id))
        ):
            if not isinstance(manual, ManualNode):
                continue
            if self.entity_id(manual.entity.id, observation.material_id) != self.entity_id(
                observation.entity.id, observation.material_id
            ) or (not manual.public and observation.material_id not in manual.material_ids):
                continue
            proposed = getattr(manual.changes, key)
            if proposed is not None:
                value = proposed
        return value

    def node_contexts(self, manual: ManualNode) -> tuple[IdentityContext, ...]:
        contexts: tuple[IdentityContext, ...] = (
            (None, *(source.material_id for source in self.scope.visible_sources))
            if manual.public
            else manual.material_ids
        )
        return tuple(
            context
            for context in contexts
            if not self.node_masks.blocked(
                manual.entity.id,
                material_ids=(context,) if isinstance(context, UUID) else (context or ()),
            )
            and not self.node_masks.blocked(
                self.entity_id(manual.entity.id, context),
                material_ids=(context,) if isinstance(context, UUID) else (context or ()),
            )
        )

    @cached_property
    def node_masks(self) -> NodeMasks:
        return NodeMasks(
            Namespace(org_id=self.scope.view.org_id, kg_id=self.scope.view.kg_id),
            self.scope.node_fences,
        ).expanded(
            self.identities, tuple(source.material_id for source in self.scope.visible_sources)
        )

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
        typed = tuple(node for values in self._typed_nodes.values() for node in values)
        contexts: tuple[UUID | None, ...] = materials or (
            None,
            *sorted({source for node in typed for source in node.material_ids}, key=str),
        )
        result = []
        for source in contexts:
            eligible = sorted(
                (
                    node
                    for node in typed
                    if (node.public or source in node.material_ids)
                    and self.entity_id(node.entity.id, source) == self.entity_id(entity, source)
                ),
                key=lambda node: (node.created_at, str(node.curation.id)),
            )
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
            if matches_support(
                row,
                self.effective_filters(self.context(row)),
                endpoint_types=self.endpoint_types(row),
                endpoint_ids=self.endpoints(row),
            )
        )

    @cached_property
    def observations(self) -> tuple[ResolvedEntity, ...]:
        support_observations = {
            observation.id
            for row in self.supports
            for observation in (row.subject_observation, row.target_observation)
            if observation
        }
        mapped_support = (
            {
                (row.source.material_id, row.source.version_id, endpoint)
                for row in self.supports
                if row.source
                for endpoint in self.endpoints(row)
            }
            if self.identities
            else set()
        )
        return tuple(
            row.observation
            for row in self.rows
            if row.observation
            and matches_observation(
                row,
                self.effective_filters(row.observation.material_id),
                entity_type=self.field(row.observation, "entity_type"),
                entity_id=self.entity_id(row.observation.entity.id, row.observation.material_id),
            )
            and (
                not self.scope.filters.requires_assertion_support
                or row.observation.id in support_observations
                or (
                    row.observation.material_id,
                    row.observation.version_id,
                    self.entity_id(row.observation.entity.id, row.observation.material_id),
                )
                in mapped_support
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
            contexts = self.node_contexts(manual)
            if (
                any(
                    self.entity_id(manual.entity.id, context)
                    in self.effective_filters(context).exclude_entity_ids
                    for context in contexts
                )
                or self.scope.filters.entity_ids
                and not any(
                    self.entity_id(manual.entity.id, context)
                    in self.effective_filters(context).entity_ids
                    for context in contexts
                )
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
                observations = [
                    item
                    for item in self.observations
                    if self.entity_id(item.entity.id, item.material_id)
                    == self.entity_id(manual.entity.id, item.material_id)
                    and (manual.public or item.material_id in manual.material_ids)
                ]
                # Source ownership was authorized in full before loading this
                # directive; a positive filter needs one qualifying owned fact.
                source_support = bool(observations)
                human_support = any(
                    row.manual_relation
                    and row.claim
                    and self.entity_id(manual.entity.id, self.context(row)) in self.endpoints(row)
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
    curations: tuple[ManualNode | ManualRelation | ManualIdentity, ...],
) -> GraphData:
    rows = tuple(
        row
        for row in rows
        if (
            row.chunk is None
            or source_status(scope, row.source, row.chunk.spans) in scope.filters.support_statuses
        )
        and (
            row.observation is None
            or source_status(scope, row.source, row.observation.spans)
            in scope.filters.support_statuses
        )
    )
    if not scope.node_fences:
        return GraphData(scope, rows, curations)
    masks = NodeMasks(
        Namespace(org_id=scope.view.org_id, kg_id=scope.view.kg_id), scope.node_fences
    ).expanded(
        GraphData(scope, rows, curations).identities,
        tuple(source.material_id for source in scope.visible_sources),
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
    imported_contexts = {source for source, _ in chunks if source_origin(scope, source) is not None}
    if imported_contexts:
        # Legacy labels may recur outside their anchor chunk, including escaped
        # XML/JSON forms. Suppress the export context as a whole, without guessing
        # a document span or hiding independent materials.
        chunks |= frozenset(
            (row.source, row.id)
            for row in rows
            if row.chunk is not None and row.source in imported_contexts
        )
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
        curations: list[ManualNode | ManualRelation | ManualIdentity] = []
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

    def node(
        self, data: GraphData, identifier: UUID, *, resolve_alias: bool = True
    ) -> NodeView | None:
        from domain_kg.graph.identity_presence import identity_presence

        identity_origins = identity_presence(data, identifier)
        observations = sorted(
            (
                item
                for item in data.observations
                if data.entity_id(item.entity.id, item.material_id) == identifier
            ),
            key=lambda item: (
                item.entity.id != identifier,
                str(item.material_id),
                str(item.version_id),
                str(item.id),
            ),
        )
        manuals = tuple(
            item
            for item in data.manual_nodes
            if any(
                data.entity_id(item.entity.id, context) == identifier
                for context in data.node_contexts(item)
            )
        )
        supports = [row for row in data.supports if row.claim and identifier in data.endpoints(row)]
        # Eligible human claims retain opaque endpoints when source observations
        # disappear. Removed sources are already excluded by the read scope.
        manual_endpoint = any(row.manual_relation for row in supports)
        if not observations and not manuals and not manual_endpoint and not identity_origins:
            if resolve_alias and data.identities:
                roots = {data.entity_id(identifier, context) for context in data.identity_contexts}
                roots.discard(identifier)
                redirected = [
                    node
                    for root in sorted(roots, key=str)
                    if (node := self.node(data, root, resolve_alias=False)) is not None
                ]
                if len(redirected) > 1:
                    raise DomainError(
                        ErrorCode.UNSUPPORTED_FILTER,
                        "Select a source context for this identity alias.",
                    )
                if redirected:
                    return redirected[0].model_copy(update={"requested_id": identifier})
            return None
        neighbors = {
            endpoint for row in supports if row.claim for endpoint in data.endpoints(row)
        } - {identifier}
        # Counts cover all eligible rows; the response includes at most 50
        # observations and derives every displayed field from those same rows.
        selected = observations[:50]
        names = [item.changes.name for item in manuals if item.changes.name is not None]
        proofs = tuple(
            dict.fromkeys(
                proof
                for origin, context in (
                    *((item.entity.id, item.material_id) for item in selected),
                    *identity_origins,
                )
                if (proof := data.identity_witness(origin, context)) is not None
            )
        )
        identity_members = (
            tuple(
                sorted(
                    {
                        origin
                        for item in selected
                        for origin in data.identities.members(item.entity.id, item.material_id)
                    }
                    | {origin for origin, _ in identity_origins},
                    key=str,
                )
            )
            if data.identities
            else ()
        )
        name = (
            names[-1]
            if names
            else (data.field(selected[0], "name") if selected else str(identifier))
        )
        return NodeView(
            import_origins=tuple(
                sorted(
                    {
                        origin
                        for source in (
                            *(
                                SourceVersion(
                                    **{
                                        field: getattr(item, field)
                                        for field in SourceVersion.model_fields
                                    }
                                )
                                for item in selected
                            ),
                            *(row.source for row in supports if row.source),
                        )
                        if (origin := source_origin(data.scope, source)) is not None
                    },
                    key=lambda origin: (str(origin.material_id), str(origin.version_id)),
                )
            ),
            kg_id=data.scope.view.kg_id,
            id=identifier,
            name=name,
            aliases=tuple(
                sorted(
                    {alias for item in selected for alias in data.field(item, "aliases")}
                    | {alias for manual in manuals for alias in (manual.changes.aliases or ())}
                    | (
                        {item.name for item in selected if item.name != name}
                        if data.identities
                        else set()
                    )
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
            identity_members=identity_members,
            identity_mappings=proofs,
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
        endpoints = data.endpoints(row) if data else (fact.subject.id, fact.target.id)
        proofs = tuple(
            dict.fromkeys(
                proof
                for endpoint in (fact.subject, fact.target)
                if data
                and (proof := data.identity_witness(endpoint.id, data.context(row))) is not None
            )
        )
        return EdgeView(
            import_origin=source_origin(data.scope, row.source) if data else None,
            subject_id=endpoints[0],
            target_id=endpoints[1],
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
            original_subject_id=fact.subject.id if data and data.identities else None,
            original_target_id=fact.target.id if data and data.identities else None,
            identity_mappings=proofs,
        )

    async def adjacent(
        self, data: GraphData, seeds: tuple[UUID, ...], limit: int
    ) -> tuple[ProjectionBody, ...]:
        if not seeds or not data.supports:
            return ()
        if len(seeds) > 200 or not 1 <= limit <= 401:
            raise ValueError("adjacency limits exceed the bounded traversal contract")
        binding = await self.queue.router.for_graph(data.scope.access, data.scope.view.kg_id)
        by_id = {
            str(row.id): row
            for row in data.supports
            if set(data.endpoints(row)).intersection(seeds)
        }
        if not by_id:
            return ()
        raw_seeds = {
            endpoint.id
            for row in by_id.values()
            if row.claim
            for endpoint in (row.claim.assertion.subject, row.claim.assertion.target)
            if data.entity_id(endpoint.id, data.context(row)) in seeds
        }
        if len(raw_seeds) > 1000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Alias adjacency exceeds its origin limit.")
        try:
            records, _, _ = await binding.driver.execute_query(
                ADJACENCY,
                **ScopePredicates(data.scope).graph_parameters,
                seed_ids=[str(seed) for seed in sorted(raw_seeds, key=str)],
                support_ids=list(by_id),
                limit=limit,
            )
        except (DriverError, Neo4jError) as error:
            raise BranchUnavailable("The graph backend is unavailable.") from error
        if any(item["id"] not in by_id for item in records):
            raise inconsistent()
        result = tuple(by_id[item["id"]] for item in records)
        if any(not row.claim or not set(data.endpoints(row)).intersection(seeds) for row in result):
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
            node.id: node
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
