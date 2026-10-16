"""Authorize all scopes, run requested branches, fuse ranks, then recheck every scope."""

from dataclasses import dataclass
from time import monotonic
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from domain_kg.engine.scope import EligibilityResolver
from domain_kg.errors import BranchUnavailable, DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.repository import GraphData
from domain_kg.graph.service import AcceptedMetadata
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.providers.rerank import QueryProvider
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.deep import DeepRetrievalBranch
from domain_kg.retrieval.federation import fan_out
from domain_kg.retrieval.fusion import fuse, rank_pool, select_evidence
from domain_kg.retrieval.graph import GraphRetrievalBranch, source_citation
from domain_kg.retrieval.lexical import LexicalRetrievalBranch
from domain_kg.retrieval.models import (
    BranchStatus,
    EvidenceCandidate,
    SearchRequest,
    SearchResponse,
)
from domain_kg.retrieval.vector import VectorRetrievalBranch
from domain_kg.revisions.models import ReadScope
from domain_kg.revisions.projections import CurationEmbeddedDocument, ProjectionBody
from domain_kg.revisions.publication import accepted_document
from domain_kg.storage.neo4j import inconsistent


@dataclass
class KGResult:
    candidates: dict[str, list[EvidenceCandidate]]
    statuses: list[BranchStatus]
    warnings: list[str]
    truncated: bool = False


class RetrievalService:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue = queue
        self.resolver = EligibilityResolver(queue.policy, AcceptedMetadata(queue))
        self.vector, self.lexical = (
            VectorRetrievalBranch(queue, providers),
            LexicalRetrievalBranch(queue),
        )
        self.graph = GraphRetrievalBranch(queue)
        self.deep = DeepRetrievalBranch(self.graph)
        self.queries = QueryProvider(providers, self.resolver)

    async def search(self, access: AccessContext, request: SearchRequest) -> SearchResponse:
        request = SearchRequest.model_validate(request)
        current = await self.queue.policy.revalidate(access, action=Action.SEARCH)
        if not set(request.kg_ids).issubset(current.kg_ids):
            raise inaccessible_reference()
        # Resolve EVERY requested scope before any retrieval/provider fan-out.
        scopes = [await self.resolver.resolve(current, request, kg) for kg in request.kg_ids]
        results = await fan_out(scopes, lambda scope: self._kg(scope, request))
        combined: dict[str, list[EvidenceCandidate]] = {}
        statuses, warnings = [], []
        truncated = False
        for result in results:
            for method, items in result.candidates.items():
                combined.setdefault(method, []).extend(items)
            statuses.extend(result.statuses)
            warnings.extend(result.warnings)
            truncated |= result.truncated
        valid = [
            s
            for s in statuses
            if s.branch in ("vector", "lexical", "graph") and s.status in ("ok", "empty")
        ]
        if not valid:
            raise BranchUnavailable("All requested retrieval branches are unavailable.")
        if request.rerank:
            pool = rank_pool(fuse(combined), 50)
            for scope in scopes:
                try:
                    ranked = await self.queries.rerank(
                        scope, request, [item for item in pool if item.kg_id == scope.view.kg_id]
                    )
                    self._validate(scope, ranked)
                    combined.setdefault("rerank", []).extend(ranked)
                    statuses.append(
                        BranchStatus(
                            kg_id=scope.view.kg_id,
                            branch="rerank",
                            status="ok" if ranked else "empty",
                        )
                    )
                except DomainError as error:
                    if (
                        not request.allow_partial
                        or not isinstance(error, BranchUnavailable)
                        and (
                            error.code
                            not in (ErrorCode.PROVIDER_UNAVAILABLE, ErrorCode.UNSUPPORTED_FILTER)
                        )
                    ):
                        raise
                    statuses.append(
                        BranchStatus(kg_id=scope.view.kg_id, branch="rerank", status="unavailable")
                    )
        selected, limited = select_evidence(
            fuse(combined), top_k=request.top_k, max_chars=request.max_context_chars
        )
        for scope in scopes:
            await self.resolver.revalidate(scope)
        return SearchResponse(
            request_id=uuid4(),
            mode=request.mode,
            status="partial"
            if any(s.status in ("unavailable", "unsupported") for s in statuses)
            else "ok",
            results=selected,
            applied_filters=request.filters,
            kg_revisions={scope.view.kg_id: scope.current_view.revision for scope in scopes},
            policy_revision=current.policy_revision,
            branch_status=tuple(statuses),
            projection_generations={scope.view.kg_id: scope.view.generation for scope in scopes},
            warnings=tuple(warnings),
            truncation=truncated or limited,
        )

    async def _kg(self, scope: ReadScope, request: SearchRequest) -> KGResult:
        result = KGResult({}, [], [])

        def status(
            branch: Literal["vector", "lexical", "graph", "rerank", "keywords"],
            value: Literal["ok", "empty", "unavailable", "unsupported", "skipped"],
        ) -> None:
            result.statuses.append(
                BranchStatus(kg_id=scope.view.kg_id, branch=branch, status=value)
            )

        def failure(
            error: DomainError, branch: Literal["vector", "lexical", "graph", "rerank", "keywords"]
        ) -> None:
            eligible = isinstance(error, BranchUnavailable) or error.code in (
                ErrorCode.PROVIDER_UNAVAILABLE,
                ErrorCode.UNSUPPORTED_FILTER,
            )
            if not request.allow_partial or not eligible:
                raise error
            status(
                branch,
                "unsupported" if error.code == ErrorCode.UNSUPPORTED_FILTER else "unavailable",
            )

        branches: tuple[
            tuple[Literal["vector", "lexical"], VectorRetrievalBranch | LexicalRetrievalBranch], ...
        ] = (("vector", self.vector), ("lexical", self.lexical))
        for method, branch in branches:
            if request.mode not in ("fast", "deep", method):
                status(method, "skipped")
                continue
            try:
                items = await branch.retrieve(scope, request)
                self._validate(scope, items)
                result.candidates[method] = items
                status(method, "ok" if items else "empty")
                result.truncated |= len(items) >= 50
            except DomainError as error:
                failure(error, method)
        keywords: tuple[str, ...] = ()
        if request.generate_keywords:
            started = monotonic()
            try:
                keywords = await self.queries.keywords(scope, request)
                status("keywords", "ok" if keywords else "empty")
            except DomainError as error:
                failure(error, "keywords")
            result.warnings.append(
                f"keyword_generation_ms:{scope.view.kg_id}:{round((monotonic() - started) * 1000)}"
            )
        if request.mode in ("deep", "graph"):
            try:
                recall = (
                    await self.deep.retrieve(
                        scope, request, result.candidates.get("vector", []), keywords=keywords
                    )
                    if request.mode == "deep"
                    else await self.graph.recall(scope, request)
                )
                self._validate(scope, recall.candidates)
                result.candidates["graph"] = recall.candidates
                result.truncated |= recall.truncated
                status("graph", "ok" if recall.candidates else "empty")
            except DomainError as error:
                failure(error, "graph")
        else:
            status("graph", "skipped")
        await self._authenticate(scope, request, result)
        if not request.rerank:
            status("rerank", "skipped")
        await self.resolver.revalidate(scope)
        return result

    async def _authenticate(
        self, scope: ReadScope, request: SearchRequest, result: KGResult
    ) -> None:
        items = [item for values in result.candidates.values() for item in values]
        if not items:
            return
        rows: list[ProjectionBody] = []
        curations: list[ManualNode] = []
        for binding in scope.view.projections:
            if (
                binding.source not in scope.visible_sources
                and binding.curation not in scope.visible_curations
            ):
                continue
            document, _, _ = await accepted_document(self.queue, scope.access, binding)
            rows.extend(
                ProjectionBody.model_validate(row.model_dump(exclude={"vector"}))
                for row in document.rows
            )
            if isinstance(document, CurationEmbeddedDocument):
                curations.append(document.directive)
        data = GraphData(scope, tuple(rows), tuple(curations))
        manuals = {
            row.curation.id: row
            for row in rows
            if row.curation and row.manual_node in data.manual_nodes
        }
        chunks = {row.id: row for row in rows if row.chunk}
        supports = {row.id: row for row in data.supports if row.assertion}
        for item in items:
            if item.kind == "manual":
                from domain_kg.retrieval.evidence import manual_candidate

                manual = (
                    manuals.get(item.curation_revision_id) if item.curation_revision_id else None
                )
                if manual is None or scope.filters.requires_assertion_support:
                    raise inconsistent()
                expected = manual_candidate(manual, "vector", item.score, 1)
                if item.model_dump(exclude={"score", "method_ranks"}) != expected.model_dump(
                    exclude={"score", "method_ranks"}
                ):
                    raise inconsistent()
                continue
            row = chunks.get(item.chunk_id) if item.chunk_id else None
            if row is None or row.source is None or row.chunk is None:
                raise inconsistent()
            if (
                item.kind != "source"
                or item.text != row.chunk.text
                or (item.material_id, item.version_id)
                != (row.source.material_id, row.source.version_id)
                or item.citation_ids
                != (
                    source_citation(
                        row.org_id, row.kg_id, row.source.material_id, row.source.version_id, row.id
                    ),
                )
            ):
                raise inconsistent()
            entities = {
                observation.entity.id
                for observation in data.observations
                if observation.chunk_id == row.id
            }
            assertions = {
                support.assertion.assertion.id
                for support in supports.values()
                if support.assertion
                and support.subject_observation
                and support.subject_observation.chunk_id == row.id
            }
            for path in item.graph_paths:
                if len(path) > request.effective_hops:
                    raise inconsistent()
                previous: set[object] = set()
                for step in path:
                    support = supports.get(step.support_id)
                    if support is None or support.assertion is None:
                        raise inconsistent()
                    fact = support.assertion.assertion
                    if (step.subject_id, step.assertion_id, step.target_id) != (
                        fact.subject.id,
                        fact.id,
                        fact.target.id,
                    ):
                        raise inconsistent()
                    endpoints = {step.subject_id, step.target_id}
                    if previous and not previous.intersection(endpoints):
                        raise inconsistent()
                    previous = set(endpoints)
                    assertions.add(step.assertion_id)
            if not set(item.entity_ids).issubset(entities) or not set(item.assertion_ids).issubset(
                assertions
            ):
                raise inconsistent()

    @staticmethod
    def _validate(scope: ReadScope, items: list[EvidenceCandidate]) -> None:
        if len(items) > 50:
            raise inconsistent()
        sources = {(source.material_id, source.version_id) for source in scope.visible_sources}
        curations = {curation.id for curation in scope.visible_curations}
        for raw in items:
            try:
                item = EvidenceCandidate.model_validate(raw)
            except ValidationError as error:
                raise inconsistent() from error
            if (
                item.kg_id != scope.view.kg_id
                or (item.kind == "source" and (item.material_id, item.version_id) not in sources)
                or (item.kind == "manual" and item.curation_revision_id not in curations)
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Evidence scope is inconsistent."
                )
