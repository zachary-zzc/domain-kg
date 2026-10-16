"""Authenticate eligible sealed evidence before using a requested derived branch."""

from dataclasses import dataclass
from typing import Any, Literal

from domain_kg.engine.scope import EligibilityResolver
from domain_kg.errors import BranchUnavailable, DomainError, ErrorCode
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.repository import GraphData
from domain_kg.graph.service import AcceptedMetadata
from domain_kg.jobs.queue import JobQueue
from domain_kg.retrieval.graph import source_citation
from domain_kg.retrieval.models import EvidenceCandidate
from domain_kg.revisions.models import ReadScope
from domain_kg.revisions.projections import CurationEmbeddedDocument, ProjectionBody
from domain_kg.revisions.publication import accepted_document, projection_rows, receipt_for
from domain_kg.storage.neo4j import GraphProjection, inconsistent
from domain_kg.storage.qdrant import VectorProjection


def manual_candidate(
    row: ProjectionBody, method: Literal["vector", "lexical", "graph"], score: float, rank: int
) -> EvidenceCandidate:
    from uuid import uuid5

    manual = row.manual_node or row.manual_relation
    if manual is None or row.curation is None or manual.curation != row.curation:
        raise inconsistent()
    return EvidenceCandidate(
        kind="manual",
        kg_id=row.kg_id,
        text=row.text,
        material_id=manual.material_ids[0] if len(manual.material_ids) == 1 else None,
        curation_revision_id=manual.curation.id,
        provenance=manual.provenance,
        citation_ids=(uuid5(row.org_id, f"{row.kg_id}/curation/citation/{manual.curation.id}"),),
        entity_ids=(manual.entity.id,)
        if isinstance(manual, ManualNode)
        else (
            tuple(sorted({manual.fact.subject.id, manual.fact.target.id}, key=str))
            if manual.fact
            else ()
        ),
        assertion_ids=(manual.fact.id,)
        if isinstance(manual, ManualRelation) and manual.fact
        else (),
        score=score,
        method_ranks={method: rank},
    )


@dataclass(frozen=True)
class EvidenceData:
    graph: GraphData
    payloads: tuple[dict[str, Any], ...]
    spaces: tuple[str | None, ...]

    @property
    def chunks(self) -> dict[str, ProjectionBody]:
        eligible = {
            observation.chunk_id
            for row in self.graph.supports
            for observation in (row.subject_observation, row.target_observation)
            if observation
        }
        bodies = {
            str(row.id): row
            for row in self.graph.rows
            if row.chunk
            and (not self.graph.scope.filters.requires_assertion_support or row.id in eligible)
        }
        bodies.update({str(row.id): row for row in self.graph.manual_rows})
        return {
            payload["projection_id"]: bodies[payload["logical_id"]]
            for payload in self.payloads
            if payload["kind"] in ("chunk", "curation") and payload["logical_id"] in bodies
        }

    def candidate(
        self, row: ProjectionBody, method: Literal["vector", "lexical"], score: float, rank: int
    ) -> EvidenceCandidate:
        if row.manual_node or row.manual_relation:
            return manual_candidate(row, method, score, rank)
        if row.chunk is None or row.source is None:
            raise inconsistent()
        observations = [item for item in self.graph.observations if item.chunk_id == row.id]
        assertions = [
            item.assertion.assertion.id
            for item in self.graph.supports
            if item.assertion
            and item.subject_observation
            and item.subject_observation.chunk_id == row.id
        ]
        return EvidenceCandidate(
            kind="source",
            kg_id=row.kg_id,
            text=row.chunk.text,
            material_id=row.source.material_id,
            version_id=row.source.version_id,
            chunk_id=row.id,
            citation_ids=(
                source_citation(
                    row.org_id, row.kg_id, row.source.material_id, row.source.version_id, row.id
                ),
            ),
            entity_ids=tuple(sorted({item.entity.id for item in observations}, key=str)),
            assertion_ids=tuple(sorted(set(assertions), key=str)),
            score=score,
            method_ranks={method: rank},
        )


class SealedEvidence:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue
        self.resolver = EligibilityResolver(queue.policy, AcceptedMetadata(queue))

    async def load(self, scope: ReadScope, branch: Literal["vector", "lexical"]) -> EvidenceData:
        await self.resolver.revalidate(scope)
        if len(scope.visible_sources) + len(scope.visible_curations) > 1000:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "This read exceeds the accepted-source budget."
            )
        bodies: list[ProjectionBody] = []
        payloads: list[dict[str, Any]] = []
        spaces = []
        curations: list[ManualNode | ManualRelation] = []
        for accepted in scope.projections:
            if (
                accepted.source not in scope.visible_sources
                and accepted.curation not in scope.visible_curations
            ):
                continue
            # Policy, authority and original-artifact failures stay fatal. Only
            # the requested derived backend's I/O is classified as unavailable.
            document, batch, receipts = await accepted_document(self.queue, scope.access, accepted)
            expected = projection_rows(document, batch)
            receipt = receipt_for(batch, branch, expected)
            if receipt not in receipts:
                raise inconsistent()
            if branch == "vector":
                binding = await self.queue.router.for_vectors(scope.access, scope.view.kg_id)
            else:
                graph = await self.queue.router.for_graph(scope.access, scope.view.kg_id)
            try:
                if branch == "vector":
                    valid = await VectorProjection(binding).verify(
                        expected, [list(row.vector) for row in document.rows]
                    )
                    valid = valid and await binding.verify(receipt)
                else:
                    valid = await GraphProjection(graph).verify(expected, lexical=True)
                    valid = valid and await graph.verify(receipt)
                if not valid:
                    raise inconsistent()
            except Exception as error:
                if (
                    isinstance(error, DomainError)
                    and error.code != ErrorCode.PROJECTION_INCONSISTENT
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
                raise BranchUnavailable(
                    "The requested retrieval backend is unavailable."
                ) from error
            bodies.extend(
                ProjectionBody.model_validate(row.model_dump(exclude={"vector"}))
                for row in document.rows
            )
            if isinstance(document, CurationEmbeddedDocument):
                curations.append(document.directive)
            payloads.extend(expected)
            if document.rows:
                spaces.append(document.profile.space_id)
            if len(bodies) > 20000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "This read exceeds the sealed-row budget."
                )
        await self.resolver.revalidate(scope)
        return EvidenceData(
            GraphData(scope, tuple(bodies), tuple(curations)), tuple(payloads), tuple(spaces)
        )
