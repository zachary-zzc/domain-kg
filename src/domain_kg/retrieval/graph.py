"""Source evidence recall through visible seeds and bounded accepted graph paths."""

from uuid import UUID, uuid5

from domain_kg.graph.service import GraphService
from domain_kg.jobs.queue import JobQueue
from domain_kg.retrieval.models import EvidenceCandidate, GraphStep, SearchRequest, SubgraphRequest
from domain_kg.revisions.models import ReadScope


def source_citation(org: UUID, kg: UUID, material: UUID, version: UUID, chunk: UUID) -> UUID:
    return uuid5(org, f"{kg}/citation/{material}/{version}/{chunk}")


class GraphRetrievalBranch:
    def __init__(self, queue: JobQueue) -> None:
        service = GraphService(queue)
        self.resolver, self.repository = service.resolver, service.repository

    async def retrieve(self, scope: ReadScope, request: SearchRequest) -> list[EvidenceCandidate]:
        request = SearchRequest.model_validate(request)
        if scope.view.kg_id not in request.kg_ids or request.filters != scope.filters:
            raise ValueError("graph request and immutable scope differ")
        if request.mode not in ("deep", "graph"):
            raise ValueError("graph expansion requires graph or deep mode")
        data = await self.repository.open(scope)
        query = request.query.casefold()
        seeds = tuple(
            sorted(
                {
                    observation.entity.id
                    for observation in data.observations
                    if any(
                        query in text.casefold()
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
        )[: request.max_nodes]
        if not seeds:
            await self.resolver.revalidate(scope)
            return []
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
                if observation.entity.id in seeds
            }
        candidates: list[EvidenceCandidate] = []
        for identifier in sorted(grouped, key=str):
            chunk_row = chunks.get(identifier)
            if chunk_row is None or chunk_row.chunk is None or chunk_row.source is None:
                continue
            paths_for_chunk = tuple(grouped[identifier])
            entities = {item.entity.id for item in data.observations if item.chunk_id == identifier}
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
                    entity_ids=tuple(sorted(entities, key=str)),
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
        return candidates[:50]
