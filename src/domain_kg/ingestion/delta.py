"""Reuse only sealed artifacts of this material's currently accepted predecessor.

No global content cache: equal text in another material does not establish either
ownership or permission. All copied evidence is rebound to the new source ledger.
"""

from collections import Counter
from dataclasses import dataclass
from functools import cached_property
from uuid import UUID

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.ingestion.extraction import validate_extraction
from domain_kg.ingestion.models import (
    ExtractionBatch,
    ParsedChunk,
    ParsedDocument,
    SourceLocation,
    Span,
)
from domain_kg.ingestion.resolved import ResolvedDocument
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.revisions.journal import PublicationJournal, read_artifact
from domain_kg.revisions.projections import EmbeddedDocument, digest
from domain_kg.storage.neo4j import inconsistent


def window_key(chunk: ParsedChunk) -> str:
    # Physical positions may move. Require the complete input and span layout,
    # including contextual headings, to match; repeated windows are ambiguous.
    return digest(
        {
            "text": chunk.text,
            "parser": chunk.parser_version,
            "chunker": chunk.chunker_version,
            "spans": [
                {
                    "start": span.start,
                    "end": span.end,
                    "quote": span.quote,
                    "kind": span.location.kind,
                    "heading": span.location.heading,
                    "table": span.location.table is not None,
                }
                for span in chunk.spans
            ],
        }
    )


@dataclass(frozen=True)
class AcceptedDelta:
    embedded: EmbeddedDocument
    parsed: ParsedDocument
    resolved: ResolvedDocument
    current: ParsedDocument

    @cached_property
    def pairs(self) -> dict[str, ParsedChunk]:
        counts = Counter(map(window_key, self.current.chunks))
        old_counts = Counter(map(window_key, self.parsed.chunks))
        return {
            window_key(item): item
            for item in self.parsed.chunks
            if counts[window_key(item)] == old_counts[window_key(item)] == 1
        }

    @cached_property
    def current_chunks(self) -> dict[object, ParsedChunk]:
        return {chunk.id: chunk for chunk in self.current.chunks}

    def pair(self, chunk: ParsedChunk) -> ParsedChunk | None:
        if self.current_chunks.get(chunk.id) != chunk:
            raise inaccessible_reference()
        return self.pairs.get(window_key(chunk))

    def extraction(self, chunk: ParsedChunk) -> ExtractionBatch | None:
        previous = self.pair(chunk)
        if previous is None:
            return None
        batches = [
            item for item in self.resolved.extraction.batches if item.chunk_id == previous.id
        ]
        if len(batches) != 1:
            raise inconsistent()
        old = batches[0]
        validate_extraction(previous, self.resolved.extraction.domain_schema, old)
        locations: dict[UUID, SourceLocation] = {}
        for before, after in zip(previous.spans, chunk.spans, strict=True):
            if before.location.block_id in locations and locations[before.location.block_id] != (
                after.location
            ):
                return None
            locations[before.location.block_id] = after.location
        if len(set(locations.values())) != len(locations):
            return None

        def span(value: Span) -> Span:
            location = locations.get(value.location.block_id)
            if location is None:
                raise inconsistent()
            return Span.model_validate(value.model_copy(update={"location": location}))

        entities = tuple(
            item.model_copy(update={"spans": tuple(map(span, item.spans))}) for item in old.entities
        )
        assertions = tuple(
            item.model_copy(
                update={
                    "spans": tuple(map(span, item.spans)),
                    "qualifiers": tuple(
                        qualifier.model_copy(update={"spans": tuple(map(span, qualifier.spans))})
                        for qualifier in item.qualifiers
                    ),
                }
            )
            for item in old.assertions
        )
        rebound = ExtractionBatch(
            **{field: getattr(chunk, field) for field in SourceVersion.model_fields},
            chunk_id=chunk.id,
            schema_id=old.schema_id,
            schema_revision=old.schema_revision,
            provider_profile_id=old.provider_profile_id,
            entities=entities,
            assertions=assertions,
            warnings=old.warnings,
        )
        return validate_extraction(chunk, self.resolved.extraction.domain_schema, rebound)

    def entity_lineage(self, batch: ExtractionBatch, chunk: ParsedChunk) -> dict[str, ScopedRef]:
        rebound = self.extraction(chunk)
        if rebound is None:
            return {}
        if rebound != batch:
            raise inconsistent()
        previous = self.pair(chunk)
        assert previous is not None
        old_batch = next(
            item for item in self.resolved.extraction.batches if item.chunk_id == previous.id
        )
        old_resolved = next(item for item in self.resolved.batches if item.chunk_id == previous.id)
        result = {}
        for proposal, observation in zip(old_batch.entities, old_resolved.entities, strict=True):
            if proposal.model_dump(exclude={"local_id"}) != observation.model_dump(
                include=set(type(proposal).model_fields) - {"local_id"}
            ):
                raise inconsistent()
            result[proposal.local_id] = observation.entity
        return result


async def accepted_delta(
    queue: JobQueue, job: LeasedJob, current: ParsedDocument
) -> AcceptedDelta | None:
    try:
        return await _accepted_delta(queue, job, current)
    except (ValidationError, KeyError, TypeError, ValueError):
        raise inconsistent() from None
    except DomainError as error:
        if error.code == ErrorCode.EVIDENCE_VALIDATION_FAILED:
            raise inconsistent() from None
        raise


async def _accepted_delta(
    queue: JobQueue, job: LeasedJob, current: ParsedDocument
) -> AcceptedDelta | None:
    # The durable replacement command, never a request cache key, selects the
    # sole possible predecessor. Current ACL and active-version fences apply.
    access = await queue.authorize(job)
    raw = await queue._current(job)
    if job.kind != "replace":
        return None
    if (current.blob.org_id, current.blob.kg_id, current.blob.version_id) != (
        job.org_id,
        job.kg_id,
        job.target_id,
    ) or str(current.blob.material_id) != raw["material_id"]:
        raise inaccessible_reference()
    view = await PublicationJournal(queue).load_metadata(access, job.kg_id)
    sources = [
        binding
        for binding in view.projections
        if binding.source and str(binding.source.material_id) == raw["material_id"]
    ]
    if (
        len(sources) != 1
        or sources[0].source is None
        or str(sources[0].source.version_id) != raw["expected_active_version"]
    ):
        raise inconsistent()
    from domain_kg.revisions.publication import accepted_document, source_of

    embedded, _, _ = await accepted_document(queue, access, sources[0])
    source = source_of(embedded)
    parsed = ParsedDocument.model_validate_json(
        await read_artifact(queue, access, embedded.chunk_checkpoint, source)
    )
    resolved = ResolvedDocument.model_validate_json(
        await read_artifact(queue, access, embedded.resolved_checkpoint, source)
    )
    if (
        source_of(parsed.blob) != source
        or source_of(resolved) != source
        or resolved.extraction.chunk_artifact_id != embedded.chunk_checkpoint.artifact_id
        or {chunk.id for chunk in parsed.chunks} != {batch.chunk_id for batch in resolved.batches}
        or {chunk.id for chunk in parsed.chunks}
        != {batch.chunk_id for batch in resolved.extraction.batches}
        or len(parsed.chunks) != len(resolved.batches)
        or len(parsed.chunks) != len(resolved.extraction.batches)
        or (resolved.extraction.domain_schema.id, resolved.extraction.domain_schema.revision)
        != (embedded.schema_id, embedded.schema_revision)
    ):
        raise inconsistent()
    originals = {item.chunk_id: item for item in resolved.extraction.batches}
    for batch in resolved.batches:
        extracted = originals[batch.chunk_id]
        if len(batch.entities) != len(extracted.entities) or any(
            proposal.model_dump(exclude={"local_id"})
            != observation.model_dump(include=set(type(proposal).model_fields) - {"local_id"})
            for proposal, observation in zip(extracted.entities, batch.entities, strict=True)
        ):
            raise inconsistent()
    await queue.authorize(job)
    return AcceptedDelta(embedded, parsed, resolved, current)
