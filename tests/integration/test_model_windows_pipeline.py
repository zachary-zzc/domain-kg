"""The native ingestion worker seals model-sized chunks with intact original spans."""

import pytest
from test_publication import make_publishing, prepare, ready
from test_retrieval_corpus import corpus_extraction

from domain_kg.materials.models import AddMaterial
from domain_kg.providers.budget import extraction_fits
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.publication import accepted_document

pytestmark = pytest.mark.storage


@pytest.mark.parametrize(
    "chat_context,embedding_context,fixed,grows",
    [(32768, 1536, False, False), (32768, 8192, False, True), (32768, 8192, True, False)],
    ids=["small-context", "large-context", "explicit-size"],
)
async def test_default_windows_adapt_to_model_context_and_preserve_explicit_sizes(
    material_service, team, chat_context, embedding_context, fixed, grows
):
    from domain_kg.graph.schema import schema_for
    from domain_kg.ingestion.chunking import tokens
    from domain_kg.ingestion.models import ChunkConfig
    from domain_kg.ingestion.pipeline import preparation_steps
    from domain_kg.ingestion.repository import SourceRepository
    from domain_kg.models import Namespace

    publishing = await make_publishing(
        material_service,
        team,
        extraction_factory=corpus_extraction,
        profile_overrides={
            "extraction_context_tokens": chat_context,
            "max_completion_tokens": 512,
            "schema_in_context": False,
            "embedding_context_tokens": embedding_context,
        },
    )
    materials, access, queue, coordinator, _, _ = publishing
    steps = preparation_steps(
        queue,
        providers=coordinator.providers,
        chunk_config=ChunkConfig(target_tokens=64, overlap_tokens=10) if fixed else None,
    )
    text = "Neutral evidence. " * 600
    await materials.add(
        access,
        AddMaterial(
            kg_id=access.kg_ids[0],
            title="adaptive context",
            inline_text=text,
            idempotency_key="adaptive-context",
        ),
    )
    job = await queue.claim("adaptive-context")
    for step in steps:
        checkpoint = await step.execute(job)
        await queue.checkpoint(job, checkpoint)
    document = await SourceRepository(queue).read(job, checkpoint)
    sizes = [tokens(c.text) for c in document.chunks]
    assert (max(sizes) > 700) == grows
    if fixed:
        assert max(sizes) <= 64
    profile = next(iter(coordinator.providers.registry.profiles.values()))
    schema = schema_for(Namespace(org_id=job.org_id, kg_id=job.kg_id), "general")
    covered = set()
    for chunk in document.chunks:
        assert extraction_fits(chunk, schema, profile)
        assert len(chunk.text.encode()) + 128 <= embedding_context
        for span in chunk.spans:
            assert span.quote == text[span.start : span.end]
            covered.update(range(span.start, span.end))
    assert covered == set(range(len(text)))


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
    for left, right in zip(chunks, chunks[1:], strict=False):
        previous, following = left.spans[-1], right.spans[0]
        assert previous.location == following.location
        assert previous.start < following.start < previous.end
        from domain_kg.ingestion.chunking import tokens

        assert 0 < tokens(text[following.start : previous.end]) <= 100
