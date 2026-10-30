"""Restore transforms typed source artifacts without altering evidence or raw hashes."""

import hashlib
from uuid import uuid4

import pytest
from test_restore_remapping import example

from domain_kg.errors import DomainError
from domain_kg.graph.models import DomainSchema, EntityRecord
from domain_kg.ingestion.models import (
    ExtractedEntity,
    ExtractionBatch,
    ParsedBlock,
    ParsedChunk,
    ParsedDocument,
)
from domain_kg.ingestion.resolved import ExtractionArtifact, ResolvedBatch, ResolvedDocument
from domain_kg.materials.models import BlobRef
from domain_kg.models import Namespace
from domain_kg.operations.remapping import RestoreRemapper


def documents():
    source, target, version, observations, _, _ = example()
    chunk_id = uuid4()
    observations = tuple(o.model_copy(update={"chunk_id": chunk_id}) for o in observations)
    text = observations[0].spans[0].quote
    blob = BlobRef(
        **version.model_dump(),
        object_id=uuid4(),
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        size_bytes=len(text.encode()),
        media_type="text/plain",
    )
    span = observations[0].spans[0]
    chunk = ParsedChunk(
        **version.model_dump(),
        id=chunk_id,
        text=text,
        spans=(span,),
        parser_version="fixture-v1",
        chunker_version="fixture-v1",
    )
    parsed = ParsedDocument(
        blob=blob,
        chunks=(chunk,),
        parser_version="fixture-v1",
        blocks=(
            ParsedBlock(
                **version.model_dump(), id=span.location.block_id, text=text, location=span.location
            ),
        ),
    )
    schema = DomainSchema(
        **source.model_dump(), id=uuid4(), revision=3, entity_types=("concept",), predicates=()
    )
    profile = uuid4()
    batch = ExtractionBatch(
        **version.model_dump(),
        chunk_id=chunk_id,
        schema_id=schema.id,
        schema_revision=schema.revision,
        provider_profile_id=profile,
        entities=tuple(
            ExtractedEntity(
                local_id=str(i),
                **o.model_dump(
                    include={"name", "entity_type", "description", "aliases", "identifier", "spans"}
                ),
            )
            for i, o in enumerate(observations)
        ),
        assertions=(),
    )
    extraction = ExtractionArtifact(
        **version.model_dump(),
        profile_id=profile,
        profile_revision=1,
        selection_revision=2,
        domain_schema=schema,
        chunk_artifact_id=uuid4(),
        batches=(batch,),
    )
    resolved = ResolvedDocument(
        **version.model_dump(),
        extraction=extraction,
        batches=(
            ResolvedBatch(
                **version.model_dump(),
                extraction_id=uuid4(),
                chunk_id=chunk_id,
                schema_id=schema.id,
                schema_revision=schema.revision,
                provider_profile_id=profile,
                entities=observations,
                assertions=(),
            ),
        ),
    )
    mapping = RestoreRemapper(
        source,
        target,
        versions=(version,),
        observations=observations,
        entities=tuple(EntityRecord(**o.entity.model_dump()) for o in observations),
    )
    return mapping, parsed, extraction, resolved


def test_source_documents_keep_raw_bytes_locations_and_historical_profile_provenance():
    from domain_kg.operations.restore_documents import RestoreDocuments

    mapping, parsed, extraction, resolved = documents()
    rewrite = RestoreDocuments(mapping, originals=(parsed.blob,))
    result = rewrite.parsed(parsed)
    assert result.blob.kg_id == mapping.target.kg_id and result.blob.sha256 == parsed.blob.sha256
    assert result.blob.size_bytes == parsed.blob.size_bytes
    assert result.blocks[0].text == parsed.blocks[0].text
    assert result.blocks[0].location == parsed.blocks[0].location
    assert result.chunks[0].text == parsed.chunks[0].text
    assert result.chunks[0].spans == parsed.chunks[0].spans
    restored = rewrite.extraction(extraction)
    assert restored.domain_schema.kg_id == mapping.target.kg_id
    assert restored.domain_schema.id == extraction.domain_schema.id
    assert restored.profile_id == extraction.profile_id and restored.profile_revision == 1
    assert restored.batches[0].entities == extraction.batches[0].entities
    final = rewrite.resolved(resolved)
    assert final.extraction == restored and final.batches[0].kg_id == mapping.target.kg_id
    assert final.batches[0].entities[0] == mapping.observation(resolved.batches[0].entities[0])
    assert parsed.blob.kg_id == mapping.origin.kg_id


def test_unknown_original_blob_or_cross_kg_schema_cannot_be_rebound():
    from domain_kg.operations.restore_documents import RestoreDocuments

    mapping, parsed, extraction, _ = documents()
    rewrite = RestoreDocuments(mapping, originals=(parsed.blob,))
    with pytest.raises(DomainError):
        rewrite.parsed(
            parsed.model_copy(
                update={"blob": parsed.blob.model_copy(update={"object_id": uuid4()})}
            )
        )
    schema = extraction.domain_schema.model_copy(update={"kg_id": uuid4()})
    with pytest.raises(DomainError):
        rewrite.extraction(extraction.model_copy(update={"domain_schema": schema}))
    with pytest.raises(DomainError):
        RestoreDocuments(mapping, originals=(parsed.blob.model_copy(update={"org_id": uuid4()}),))


def test_nested_cross_source_extraction_and_schema_refs_are_rejected():
    from domain_kg.operations.restore_documents import RestoreDocuments

    mapping, parsed, extraction, resolved = documents()
    rewrite = RestoreDocuments(mapping, originals=(parsed.blob,))
    batch = extraction.batches[0].model_copy(update={"version_id": uuid4()})
    with pytest.raises((DomainError, ValueError)):
        rewrite.extraction(extraction.model_copy(update={"batches": (batch,)}))
    batch = resolved.batches[0].model_copy(update={"schema_id": uuid4()})
    with pytest.raises(DomainError):
        rewrite.resolved(resolved.model_copy(update={"batches": (batch,)}))
    assert Namespace(org_id=mapping.target.org_id, kg_id=mapping.target.kg_id) == mapping.target


def test_restore_retains_image_assets_and_remaps_their_parent_scope():
    import base64
    from pathlib import Path

    from domain_kg.ingestion.formats import Blocks
    from domain_kg.ingestion.images import capture
    from domain_kg.ingestion.models import ImageOrigin, ParseOptions, SourceImage
    from domain_kg.operations.restore_documents import RestoreDocuments

    mapping, parsed, *_ = documents()
    data = (Path(__file__).parents[1] / "fixtures/materials/scanned-bilingual.png").read_bytes()
    blocks = Blocks(parsed.blob, ParseOptions(ocr=False), "fixture")
    image = capture(blocks, data, ImageOrigin(part="word/media/image.png"))
    # Asset metadata must name one of the backup's exact original image objects.
    raw = BlobRef(
        **{f: getattr(parsed.blob, f) for f in ("org_id", "kg_id", "material_id", "version_id")},
        object_id=uuid4(),
        sha256=image.sha256,
        size_bytes=len(data),
        media_type="image/png",
    )
    preview_data = base64.b64decode(image.preview_base64)
    preview = raw.model_copy(
        update={
            "object_id": uuid4(),
            "sha256": image.preview_sha256,
            "size_bytes": len(preview_data),
        }
    )
    stored = SourceImage.model_validate(
        image.model_dump()
        | {"data_base64": None, "blob": raw, "preview_base64": None, "preview_blob": preview}
    )
    source = parsed.model_copy(update={"images": (stored,)})
    restored = RestoreDocuments(
        mapping, originals=(parsed.blob,), image_assets=(raw, preview)
    ).parsed(source)
    assert restored.images[0].blob.kg_id == mapping.target.kg_id
    assert restored.images[0].sha256 == image.sha256
    assert restored.images[0].preview_blob.kg_id == mapping.target.kg_id
    with pytest.raises(DomainError):
        RestoreDocuments(mapping, originals=(parsed.blob,)).parsed(source)
