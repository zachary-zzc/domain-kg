"""Authorize all scopes, run requested branches, fuse ranks, then recheck every scope."""

from dataclasses import dataclass
from time import monotonic
from typing import Literal
from uuid import uuid4

from pydantic import ValidationError

from domain_kg.engine.scope import EligibilityResolver
from domain_kg.errors import BranchUnavailable, DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.identity_models import ManualIdentity
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.repository import scoped_graph_data
from domain_kg.graph.service import AcceptedMetadata
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.providers.rerank import QueryProvider
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.deep import DeepRetrievalBranch
from domain_kg.retrieval.federation import fan_out
from domain_kg.retrieval.fusion import fuse_evidence, recall_pool, select_evidence
from domain_kg.retrieval.graph import GraphRetrievalBranch, source_citation
from domain_kg.retrieval.identity import evidence_identity
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
            pool = recall_pool(combined, 50)
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
            fuse_evidence(combined), top_k=request.top_k, max_chars=request.max_context_chars
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
        curations: list[ManualNode | ManualRelation | ManualIdentity] = []
        for binding in scope.projections:
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
        data = await scoped_graph_data(self.queue, scope, tuple(rows), tuple(curations))
        manuals = {row.curation.id: row for row in rows if row.curation and row in data.manual_rows}
        chunks = {row.id: row for row in rows if row.chunk}
        supports = {row.id: row for row in data.supports if row.claim}
        for item in items:
            if item.kind == "manual":
                from domain_kg.retrieval.evidence import manual_candidate

                manual = (
                    manuals.get(item.curation_revision_id) if item.curation_revision_id else None
                )
                if manual is None:
                    raise inconsistent()
                expected = manual_candidate(manual, "vector", item.score, 1, data=data)
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
            from domain_kg.imports.evidence import source_origin, source_status

            expected_origin = source_origin(scope, row.source)
            if item.import_origin != expected_origin or item.support_status != source_status(
                scope, row.source, row.chunk.spans
            ):
                raise inconsistent()
            entities, witnesses = evidence_identity(data, row)
            if item.identity_mappings != witnesses:
                raise inconsistent()
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
                    if support is None or support.claim is None:
                        raise inconsistent()
                    if (
                        step.import_origin != source_origin(scope, support.source)
                        or step.support_status != support.claim.support.status
                    ):
                        raise inconsistent()
                    fact = support.claim.assertion
                    if (step.predicate, step.polarity) != (fact.predicate, fact.polarity):
                        raise inconsistent()
                    mapped = data.endpoints(support)
                    if (step.subject_id, step.assertion_id, step.target_id) != (
                        mapped[0],
                        fact.id,
                        mapped[1],
                    ):
                        raise inconsistent()
                    expected_originals = (fact.subject.id, fact.target.id)
                    if (step.original_subject_id, step.original_target_id) != (
                        expected_originals if data.identities else (None, None)
                    ):
                        raise inconsistent()
                    expected_proofs = tuple(
                        proof
                        for origin in expected_originals
                        if (proof := data.identity_witness(origin, data.context(support)))
                        is not None
                    )
                    if step.identity_mappings != tuple(dict.fromkeys(expected_proofs)):
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
