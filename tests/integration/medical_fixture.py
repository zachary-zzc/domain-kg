"""Controlled extraction responses from exact public-PDF wording, not model quality."""

import re

from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.models import ExtractedAssertion, ExtractedEntity, ExtractionBatch, Span
from domain_kg.models import Namespace


def medical_batch(chunk, profile):
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    matches = {}
    for term in ("diabetes", "annual review"):
        for span in chunk.spans:
            match = re.search(re.escape(term), span.quote, re.IGNORECASE)
            if match:
                matches[term] = Span(
                    location=span.location,
                    start=span.start + match.start(),
                    end=span.start + match.end(),
                    quote=match.group(),
                )
                break
    entities = assertions = ()
    if len(matches) == 2:
        entities = tuple(
            ExtractedEntity(
                local_id=term,
                name=span.quote,
                entity_type="concept",
                spans=(span,),
            )
            for term, span in matches.items()
        )
        assertions = (
            ExtractedAssertion(
                subject="annual review",
                target="diabetes",
                predicate="related_to",
                confidence=1.0,
                spans=tuple(matches.values()),
            ),
        )
    return ExtractionBatch(
        **{field: getattr(chunk, field) for field in Namespace.model_fields},
        material_id=chunk.material_id,
        version_id=chunk.version_id,
        chunk_id=chunk.id,
        schema_id=schema.id,
        schema_revision=schema.revision,
        provider_profile_id=profile,
        entities=entities,
        assertions=assertions,
    )
