"""The native ingestion worker seals model-sized chunks with intact original spans."""

import pytest
from test_publication import make_publishing, prepare, ready
from test_retrieval_corpus import corpus_extraction

from domain_kg.materials.models import AddMaterial
from domain_kg.providers.budget import extraction_fits
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.publication import accepted_document

pytestmark = pytest.mark.storage


async def test_selected_model_limits_drive_sealed_source_windows(material_service, team):
    publishing = await make_publishing(
        material_service,
        team,
        extraction_factory=corpus_extraction,
        profile_overrides={
            "extraction_context_tokens": 4096,
            "max_completion_tokens": 512,
            "schema_in_context": False,
            "embedding_context_tokens": 1024,
            "embedding_batch_size": 1,
        },
    )
    _, access, queue, coordinator, _, _ = publishing
    text = "细胞 evidence 🔬. " * 70
    receipt, job, checkpoint = await prepare(
        publishing,
        command=AddMaterial(
            kg_id=access.kg_ids[0],
            title="model windows",
            inline_text=text,
            idempotency_key="model-windows",
        ),
    )
    await coordinator.publish(access, await ready(publishing, job, checkpoint))
    view = await PublicationJournal(queue).load(access, job.kg_id)
    document, _, _ = await accepted_document(queue, access, view.projections[0])
    chunks = [row.chunk for row in document.rows if row.chunk]
    assert len(chunks) > 1
    profile = next(iter(coordinator.providers.registry.profiles.values()))
    from domain_kg.graph.schema import schema_for
    from domain_kg.models import Namespace

    schema = schema_for(Namespace(org_id=job.org_id, kg_id=job.kg_id), "general")
    covered = set()
    for chunk in chunks:
        assert chunk.material_id == receipt.material_id and extraction_fits(chunk, schema, profile)
        assert len(chunk.text.encode()) + 128 <= 1024
        for span in chunk.spans:
            assert span.quote == text[span.start : span.end]
            covered.update(range(span.start, span.end))
    assert covered == set(range(len(text)))
