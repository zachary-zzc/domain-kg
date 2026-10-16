"""Tiny accepted corpus shared by vector/text branch checks."""

import pytest
from test_entity_resolution import TEXT, batch_for
from test_publication import make_publishing, prepare, ready

from domain_kg.graph.schema import schema_for
from domain_kg.identity.models import Action
from domain_kg.ingestion.models import ExtractionBatch
from domain_kg.materials.models import AddMaterial
from domain_kg.models import Namespace


def corpus_extraction(chunk, profile):
    if "Alpha" in chunk.text and "Beta" in chunk.text:
        return batch_for(chunk, profile)
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    return ExtractionBatch(
        **{key: getattr(chunk, key) for key in ("org_id", "kg_id", "material_id", "version_id")},
        chunk_id=chunk.id,
        schema_id=schema.id,
        schema_revision=schema.revision,
        provider_profile_id=profile,
        entities=(),
        assertions=(),
    )


def corpus_embedding(text):
    if "PRIVATE_NEAREST" in text or text == "needle":
        return [1.0, 0.0, 0.0]
    if "PUBLIC_BEST" in text:
        return [0.99, 0.1, 0.0]
    return [0.0, 1.0, 0.0]


@pytest.fixture
async def retrieval_corpus(material_service, team):
    publishing = await make_publishing(
        material_service,
        team,
        extraction_factory=corpus_extraction,
        embedding_factory=corpus_embedding,
        restricted=True,
    )
    materials, access, queue, coordinator, _, _ = publishing
    receipts = {}
    for key, text, private in (
        ("public", "PUBLIC_BEST\n" + TEXT, False),
        (
            "plain",
            "阿尔法使用贝塔。\nIdentifier urn:fixture:identifier-A7. "
            "Plain text without extracted assertions.",
            False,
        ),
        ("hidden", "PRIVATE_NEAREST\n" + TEXT, True),
    ):
        receipt, job, checkpoint = await prepare(
            publishing,
            command=AddMaterial(
                kg_id=access.kg_ids[0],
                title=key,
                tags=(key,),
                inline_text=text,
                author="fixture-author",
                language="zh" if key == "plain" else "en",
                acl_mode="restricted" if private else "kg",
                idempotency_key=key,
            ),
        )
        await coordinator.publish(access, await ready(publishing, job, checkpoint))
        receipts[key] = receipt
    reader = await queue.policy.resolve_access(team[2]["reader"], [access.kg_ids[0]], Action.SEARCH)
    return publishing, reader, receipts
