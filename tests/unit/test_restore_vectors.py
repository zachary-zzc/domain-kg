"""Vector reuse requires the same approved embedding space and identical row text."""

from uuid import uuid4

import pytest
from test_restore_documents import documents

from domain_kg.errors import DomainError
from domain_kg.jobs.models import Checkpoint
from domain_kg.operations.restore_documents import RestoreDocuments
from domain_kg.providers.embeddings import EmbeddingProfile
from domain_kg.revisions.projections import EmbeddedDocument, ProjectionRow


def vector_document():
    mapping, parsed, extraction, resolved = documents()
    profile = EmbeddingProfile(
        **mapping.origin.model_dump(),
        id=extraction.profile_id,
        revision=1,
        model="synthetic",
        dimensions=3,
        space_id="a" * 64,
    )
    job = uuid4()
    chunk = Checkpoint(
        **mapping.origin.model_dump(),
        job_id=job,
        stage="chunking",
        artifact_id=extraction.chunk_artifact_id,
        input_hash="b" * 64,
        output_hash="c" * 64,
    )
    resolved_cp = Checkpoint(
        **mapping.origin.model_dump(),
        job_id=job,
        stage="resolving",
        artifact_id=uuid4(),
        input_hash="d" * 64,
        output_hash="e" * 64,
    )
    source = RestoreDocuments.owner(parsed.blob)
    original = EmbeddedDocument(
        **source.model_dump(),
        chunk_checkpoint=chunk,
        resolved_checkpoint=resolved_cp,
        profile=profile,
        profile_selection_revision=1,
        schema_id=extraction.domain_schema.id,
        schema_revision=extraction.domain_schema.revision,
        rows=tuple(
            ProjectionRow(
                **mapping.origin.model_dump(),
                id=o.id,
                kind="observation",
                source=source,
                observation=o,
                vector=(1.0, 0.0, 0.0),
            )
            for o in resolved.batches[0].entities
        ),
    )
    selected = profile.model_copy(
        update=mapping.target.model_dump() | {"id": uuid4(), "revision": 2}
    )
    return mapping, parsed, original, selected


def test_unchanged_text_reuses_vectors_only_in_the_same_embedding_space():
    mapping, parsed, original, selected = vector_document()
    rewrite = RestoreDocuments(mapping, originals=(parsed.blob,))

    def checkpoint(cp):
        return cp.model_copy(update=mapping.target.model_dump() | {"output_hash": "f" * 64})

    result = rewrite.embedded(original, selected, checkpoint)
    assert result.profile == selected and result.kg_id == mapping.target.kg_id
    assert result.chunk_checkpoint == checkpoint(original.chunk_checkpoint)
    assert result.resolved_checkpoint == checkpoint(original.resolved_checkpoint)
    assert result.rows[0].vector == original.rows[0].vector
    assert result.rows[0].observation.entity.kg_id == mapping.target.kg_id
    assert result.rows[0].text == original.rows[0].text


def test_different_space_or_dimension_requires_fresh_bounded_vectors():
    mapping, parsed, original, selected = vector_document()
    rewrite = RestoreDocuments(mapping, originals=(parsed.blob,))

    def checkpoint(cp):
        return cp.model_copy(update=mapping.target.model_dump())

    changed = selected.model_copy(update={"space_id": "b" * 64, "dimensions": 2, "model": "new"})
    with pytest.raises(DomainError):
        rewrite.embedded(original, changed, checkpoint)
    result = rewrite.embedded(original, changed, checkpoint, vectors=((1.0, 0.0), (0.0, 1.0)))
    assert result.profile == changed and result.rows[0].vector == (1.0, 0.0)
    with pytest.raises(DomainError):
        rewrite.embedded(original, changed, checkpoint, vectors=((1.0, 0.0),))
    with pytest.raises(DomainError):
        rewrite.embedded(original, changed, checkpoint, vectors=((float("nan"), 0.0), (0.0, 1.0)))
    with pytest.raises(DomainError):
        rewrite.embedded(original, selected.model_copy(update={"kg_id": uuid4()}), checkpoint)


def test_manual_relation_reembeds_changed_identity_text_without_inventing_source_spans():
    from datetime import UTC, datetime

    from test_restore_remapping import example, remapper

    from domain_kg.graph.manual_relations import ManualRelation, RelationFields
    from domain_kg.graph.models import SupportRecord
    from domain_kg.models import ManualProvenance, ScopedRef
    from domain_kg.revisions.projections import CurationEmbeddedDocument

    values = example()
    source, target, _, _, fact, curation = values
    mapping = remapper(values)
    provenance = ManualProvenance(actor="historical", reason=f"Keep literal {source.kg_id}")
    support = SupportRecord(
        **curation.model_dump(),
        assertion=ScopedRef(**source.model_dump(), id=fact.id),
        status="manual",
        curation=curation,
        provenance=provenance,
    )
    directive = ManualRelation(
        curation=curation,
        logical_assertion=ScopedRef(**source.model_dump(), id=fact.id),
        fields=RelationFields(description=provenance.reason),
        fact=fact,
        support=support,
        provenance=provenance,
        revision=1,
        created_at=datetime.now(UTC),
        public=True,
    )
    profile = EmbeddingProfile(
        **source.model_dump(),
        id=uuid4(),
        revision=1,
        model="synthetic",
        dimensions=3,
        space_id="a" * 64,
    )
    row = ProjectionRow(
        **source.model_dump(),
        id=curation.id,
        curation=curation,
        kind="curation",
        manual_relation=directive,
        manual_text=directive.text,
        provenance=provenance,
        vector=(1.0, 0.0, 0.0),
    )
    document = CurationEmbeddedDocument(
        **source.model_dump(),
        curation=curation,
        directive=directive,
        profile=profile,
        profile_selection_revision=1,
        schema_id=uuid4(),
        schema_revision=1,
        rows=(row,),
    )
    selected = profile.model_copy(update=target.model_dump())
    rewrite = RestoreDocuments(mapping, originals=())
    with pytest.raises(DomainError):
        rewrite.curation(document, selected)
    restored = rewrite.curation(document, selected, vectors=((0.0, 1.0, 0.0),))
    assert restored.rows[0].vector == (0.0, 1.0, 0.0)
    assert restored.rows[0].manual_text == restored.directive.text
    assert restored.directive.provenance == provenance
    assert restored.directive.support.version is None and restored.directive.support.spans == ()
    assert restored.directive.support.status == "manual"
    assert str(source.kg_id) in restored.directive.fields.description
    assert restored.directive.fact.subject.kg_id == target.kg_id
