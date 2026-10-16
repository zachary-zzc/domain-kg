"""Source evidence recall through visible seeds and bounded accepted graph paths."""

from dataclasses import dataclass
from uuid import UUID, uuid5

from domain_kg.graph.service import GraphService
from domain_kg.graph.views import SubgraphView
from domain_kg.jobs.queue import JobQueue
from domain_kg.retrieval.identity import evidence_identity
from domain_kg.retrieval.models import EvidenceCandidate, GraphStep, SearchRequest, SubgraphRequest
from domain_kg.revisions.models import ReadScope


def source_citation(org: UUID, kg: UUID, material: UUID, version: UUID, chunk: UUID) -> UUID:
    return uuid5(org, f"{kg}/citation/{material}/{version}/{chunk}")


@dataclass(frozen=True)
class GraphRecall:
    candidates: list[EvidenceCandidate]
    graph: SubgraphView | None
    truncated: bool


class GraphRetrievalBranch:
    def __init__(self, queue: JobQueue) -> None:
        service = GraphService(queue)
        self.resolver, self.repository = service.resolver, service.repository

    async def retrieve(self, scope: ReadScope, request: SearchRequest) -> list[EvidenceCandidate]:
        return (await self.recall(scope, request)).candidates

    async def recall(
        self,
        scope: ReadScope,
        request: SearchRequest,
        *,
        seed_ids: tuple[UUID, ...] = (),
        keywords: tuple[str, ...] = (),
    ) -> GraphRecall:
        request = SearchRequest.model_validate(request)
        if scope.view.kg_id not in request.kg_ids or request.filters != scope.filters:
            raise ValueError("graph request and immutable scope differ")
        if request.mode not in ("deep", "graph"):
            raise ValueError("graph expansion requires graph or deep mode")
        data = await self.repository.open(scope)
        query = request.query.casefold()
        all_seeds = tuple(
            sorted(
                set(seed_ids)
                | {
                    data.entity_id(observation.entity.id, observation.material_id)
                    for observation in data.observations
                    if any(
                        any(term.casefold() in text.casefold() for term in (keywords or (query,)))
                        for text in (
                            observation.name,
                            observation.description,
                            *observation.aliases,
                            observation.identifier or "",
                        )
                    )
                },
                key=str,
            )
        )
        manual_seeds = {
            data.entity_id(manual.entity.id, manual.material_ids if not manual.public else None)
            for manual in data.manual_nodes
            if any(term.casefold() in manual.text.casefold() for term in (keywords or (query,)))
        }
        manual_seeds.update(
            data.entity_id(endpoint.id, data.context(row))
            for row in data.manual_rows
            if row.manual_relation
            and row.claim
            and any(term.casefold() in row.text.casefold() for term in (keywords or (query,)))
            for endpoint in (row.claim.assertion.subject, row.claim.assertion.target)
        )
        all_seeds = tuple(sorted(set(all_seeds) | manual_seeds, key=str))
        all_seeds = tuple(
            sorted(
                {
                    node.id
                    for identifier in all_seeds
                    if (node := self.repository.node(data, identifier)) is not None
                },
                key=str,
            )
        )
        annotation_rows = tuple(
            row
            for row in data.manual_rows
            if row.manual_relation
            and not row.claim
            and any(term.casefold() in row.text.casefold() for term in (keywords or (query,)))
        )
        seeds = all_seeds[: request.max_nodes]
        if not seeds:
            await self.resolver.revalidate(scope)
            from domain_kg.retrieval.evidence import manual_candidate

            annotation_candidates = [
                manual_candidate(row, "graph", 0.0, index, data=data)
                for index, row in enumerate(annotation_rows[:50], 1)
            ]
            return GraphRecall(annotation_candidates, None, len(annotation_rows) > 50)
        graph = await self.repository.traverse(
            scope,
            SubgraphRequest(
                kg_id=scope.view.kg_id,
                seed_ids=seeds,
                filters=scope.filters,
                max_hops=request.effective_hops,
                max_nodes=request.max_nodes,
                max_edges=request.max_edges,
            ),
            data=data,
        )
        # Breadth-first paths are bounded by the same hop count as expansion.
        paths: dict[UUID, tuple[GraphStep, ...]] = {seed: () for seed in seeds}
        support_paths: dict[UUID, tuple[GraphStep, ...]] = {}
        for _ in range(request.effective_hops):
            additions = {}
            for edge in graph.edges:
                step = GraphStep(
                    subject_id=edge.subject_id,
                    assertion_id=edge.assertion_id,
                    target_id=edge.target_id,
                    support_id=edge.support_id,
                    original_subject_id=edge.original_subject_id,
                    original_target_id=edge.original_target_id,
                    identity_mappings=edge.identity_mappings,
                )
                for start, end in (
                    (edge.subject_id, edge.target_id),
                    (edge.target_id, edge.subject_id),
                ):
                    if start not in paths or len(paths[start]) >= request.effective_hops:
                        continue
                    path = (*paths[start], step)
                    if edge.support_id not in support_paths or len(path) < len(
                        support_paths[edge.support_id]
                    ):
                        support_paths[edge.support_id] = path
                    if end not in paths and end not in additions:
                        additions[end] = path
            paths.update(additions)
        chunks = {row.chunk.id: row for row in data.rows if row.chunk}
        grouped: dict[UUID, list[tuple[GraphStep, ...]]] = {}
        for row in data.supports:
            if row.subject_observation and row.id in support_paths:
                grouped.setdefault(row.subject_observation.chunk_id, []).append(
                    support_paths[row.id]
                )
        if request.effective_hops == 0:
            grouped = {
                observation.chunk_id: []
                for observation in data.observations
                if data.entity_id(observation.entity.id, observation.material_id) in seeds
            }
        candidates: list[EvidenceCandidate] = []
        for identifier in sorted(grouped, key=str):
            chunk_row = chunks.get(identifier)
            if chunk_row is None or chunk_row.chunk is None or chunk_row.source is None:
                continue
            paths_for_chunk = tuple(grouped[identifier])
            entities, witnesses = evidence_identity(data, chunk_row)
            candidates.append(
                EvidenceCandidate(
                    kind="source",
                    kg_id=scope.view.kg_id,
                    text=chunk_row.chunk.text,
                    citation_ids=(
                        source_citation(
                            scope.view.org_id,
                            scope.view.kg_id,
                            chunk_row.source.material_id,
                            chunk_row.source.version_id,
                            identifier,
                        ),
                    ),
                    material_id=chunk_row.source.material_id,
                    version_id=chunk_row.source.version_id,
                    chunk_id=identifier,
                    entity_ids=entities,
                    identity_mappings=witnesses,
                    assertion_ids=tuple(
                        sorted(
                            {step.assertion_id for path in paths_for_chunk for step in path},
                            key=str,
                        )
                    ),
                    method_ranks={"graph": len(candidates) + 1},
                    graph_paths=paths_for_chunk,
                )
            )
        await self.resolver.revalidate(scope)
        from domain_kg.retrieval.evidence import manual_candidate

        node_ids = {node.id for node in graph.nodes}
        first_rank = len(candidates)
        candidates.extend(
            manual_candidate(row, "graph", 0.0, first_rank + index, data=data)
            for index, row in enumerate(
                (
                    row
                    for row in data.manual_rows
                    if (
                        row.manual_node
                        and data.entity_id(row.manual_node.entity.id, data.context(row)) in node_ids
                    )
                    or (
                        row.manual_relation
                        and row.claim
                        and set(data.endpoints(row)).intersection(node_ids)
                    )
                ),
                1,
            )
        )
        annotation_first_rank = len(candidates)
        candidates.extend(
            manual_candidate(row, "graph", 0.0, annotation_first_rank + index, data=data)
            for index, row in enumerate(annotation_rows, 1)
        )
        return GraphRecall(
            candidates[:50],
            graph,
            graph.truncation or len(all_seeds) > len(seeds) or len(candidates) > 50,
        )
