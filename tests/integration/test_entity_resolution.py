"""Real source artifacts and authority transactions; no global display-field merging."""

from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for
from domain_kg.identity.models import Action
from domain_kg.ingestion.models import (
    ExtractedAssertion,
    ExtractedEntity,
    ExtractionBatch,
    ParsedChunk,
    Span,
)
from domain_kg.materials.models import AddMaterial
from domain_kg.models import Namespace

pytestmark = pytest.mark.storage
TEXT = "Alpha (阿尔法, urn:fixture:alpha) uses Beta (贝塔, urn:fixture:beta)."


async def test_approved_provider_pipeline_and_owner_boundary(material_service, team, mongo_db):
    import json

    import httpx
    from pydantic import SecretStr

    from domain_kg.ingestion.pipeline import Pipeline, extraction_steps, preparation_steps
    from domain_kg.jobs.queue import JobQueue
    from domain_kg.jobs.runner import JobRunner
    from domain_kg.providers.profiles import (
        ProviderConfig,
        ProviderConnection,
        ProviderProfile,
        ProviderRegistry,
    )
    from domain_kg.providers.service import ProviderService

    materials, kg = material_service
    _, policy, principals, _, org, _ = team
    owner = await policy.resolve_access(principals["owner"], [kg], Action.CONFIGURE)
    editor = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    profile = ProviderProfile(
        id=uuid4(),
        connection_ref="fixture",
        extraction_model="synthetic-test-model",
        embedding_model="synthetic-embedding",
        embedding_dimensions=3,
        data_policy_approved=True,
        allowed_namespaces=(Namespace(org_id=org, kg_id=kg),),
    )
    registry = ProviderRegistry(
        ProviderConfig(
            profiles=(profile,),
            connections=(
                ProviderConnection(
                    ref="fixture",
                    endpoint="https://provider.example/v1",
                    api_key=SecretStr("fixture"),
                ),
            ),
        )
    )

    def response(request):
        chunk = ParsedChunk.model_validate(
            json.loads(json.loads(request.content)["messages"][1]["content"])["source"]
        )
        batch = batch_for(chunk, profile.id)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": batch.model_dump_json(include={"entities", "assertions"})
                        },
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    providers = ProviderService(
        policy, materials.router, registry, transport=httpx.MockTransport(response)
    )
    with pytest.raises(DomainError):
        await providers.bind(editor, kg, profile.id, expected_revision=0, idempotency_key="denied")
    assert (
        await providers.bind(owner, kg, profile.id, expected_revision=0, idempotency_key="select")
        == 1
    )
    assert (
        await providers.bind(owner, kg, profile.id, expected_revision=0, idempotency_key="select")
        == 1
    )
    with pytest.raises(DomainError):
        await providers.bind(owner, kg, profile.id, expected_revision=0, idempotency_key="stale")
    receipt = await materials.add(
        editor,
        AddMaterial(
            kg_id=kg, title="Public fixture", inline_text=TEXT, idempotency_key="provider-pipeline"
        ),
    )
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    preparation = preparation_steps(queue)
    steps = preparation + extraction_steps(queue, providers, preparation[-1])
    await JobRunner(queue, Pipeline(steps)).run_job(await queue.claim("provider-test"))
    raw = await mongo_db.jobs.find_one({"id": str(receipt.job_id)})
    assert raw["stage"] == "repair_required"  # No publication stages yet.
    assert await mongo_db.supports.count_documents({"org_id": str(org), "kg_id": str(kg)}) == 1
    assert (
        await mongo_db.job_checkpoints.count_documents(
            {"job_id": str(receipt.job_id), "stage": "resolving"}
        )
        == 1
    )
    usage = await mongo_db.provider_usage.find_one({"org_id": str(org), "kg_id": str(kg)})
    assert usage["calls"] == 1 and usage["output_tokens"] == 50
    kg_row = await mongo_db.kgs.find_one({"id": str(kg)})
    assert kg_row["published_revision"] == 0
    chunk_row = await mongo_db.source_chunks.find_one({"version_id": str(receipt.version_id)})
    chunk = ParsedChunk.model_validate(
        {field: chunk_row[field] for field in ParsedChunk.model_fields}
    )
    await mongo_db.material_access.update_one(
        {"org_id": str(org), "kg_id": str(kg), "id": str(receipt.material_id)},
        {"$set": {"acl_mode": "restricted"}},
    )
    schema = schema_for(Namespace(org_id=org, kg_id=kg), "general")
    with pytest.raises(DomainError):
        await providers.extract(owner, chunk, schema)
    usage = await mongo_db.provider_usage.find_one({"org_id": str(org), "kg_id": str(kg)})
    assert usage["calls"] == 1  # Restricted content never reached the provider.


async def test_conflicting_claims_keep_distinct_polarity_and_idempotent_supports(
    material_service, team, mongo_db
):
    from domain_kg.ingestion.resolution import ResolutionService

    negative = TEXT.replace(" uses ", " does not use ")
    access, chunk = await prepared(
        material_service, team, mongo_db, TEXT + "\n" + negative, "conflict"
    )
    batch = batch_for(chunk, uuid4())
    positive = next(span for span in chunk.spans if span.quote.rstrip("\n") == TEXT)
    neg = next(span for span in chunk.spans if span.quote == negative)
    batch = batch.model_copy(
        update={
            "assertions": (
                batch.assertions[0].model_copy(update={"spans": (positive,)}),
                batch.assertions[0].model_copy(update={"polarity": "negative", "spans": (neg,)}),
            )
        }
    )
    materials, _ = material_service
    service = ResolutionService(team[1], materials.router)
    result = await service.resolve_entities(access, batch)
    assert len({item.assertion.id for item in result.assertions}) == 2
    assert {item.assertion.polarity for item in result.assertions} == {"positive", "negative"}
    assert await service.resolve_entities(access, batch) == result
    assert await mongo_db.supports.count_documents({"version.id": str(chunk.version_id)}) == 2


async def prepared(material_service, team, mongo_db, text, key):
    from domain_kg.ingestion.pipeline import Pipeline, preparation_steps
    from domain_kg.jobs.queue import JobQueue
    from domain_kg.jobs.runner import JobRunner

    materials, kg = material_service
    _, policy, principals, _, org, _ = team
    access = await policy.resolve_access(principals["editor"], [kg], Action.INGEST)
    receipt = await materials.add(
        access,
        AddMaterial(kg_id=kg, title="Resolution fixture", inline_text=text, idempotency_key=key),
    )
    await mongo_db.jobs.update_many(
        {
            "org_id": str(org),
            "kg_id": str(kg),
            "id": {"$ne": str(receipt.job_id)},
            "stage": "repair_required",
        },
        {"$set": {"stage": "failed"}},
    )
    queue = JobQueue(policy, materials.router)
    await queue.initialize()
    await JobRunner(queue, Pipeline(preparation_steps(queue))).run_job(
        await queue.claim("preparer")
    )
    raw = await mongo_db.source_chunks.find_one(
        {"org_id": str(org), "kg_id": str(kg), "version_id": str(receipt.version_id)}
    )
    chunk = ParsedChunk.model_validate({field: raw[field] for field in ParsedChunk.model_fields})
    return access, chunk


def batch_for(chunk, profile):
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    return ExtractionBatch(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        material_id=chunk.material_id,
        version_id=chunk.version_id,
        chunk_id=chunk.id,
        schema_id=schema.id,
        schema_revision=1,
        provider_profile_id=profile,
        entities=(
            ExtractedEntity(
                local_id="a",
                name="Alpha",
                entity_type="concept",
                description="Alpha",
                aliases=("阿尔法",),
                identifier="urn:fixture:alpha",
                spans=chunk.spans,
            ),
            ExtractedEntity(
                local_id="b",
                name="Beta",
                entity_type="concept",
                description="Beta",
                aliases=("贝塔",),
                identifier="urn:fixture:beta",
                spans=chunk.spans,
            ),
        ),
        assertions=(
            ExtractedAssertion(
                subject="a", target="b", predicate="uses", confidence=0.9, spans=chunk.spans
            ),
        ),
    )


async def test_resolution_keeps_independent_support_and_source_owned_fields(
    material_service, team, mongo_db
):
    from domain_kg.ingestion.resolution import ResolutionService

    service = ResolutionService(team[1], material_service[0].router)
    access, first = await prepared(material_service, team, mongo_db, TEXT, "first")
    profile = uuid4()
    result = await service.resolve_entities(access, batch_for(first, profile))
    assert await service.resolve_entities(access, batch_for(first, profile)) == result
    access, second = await prepared(material_service, team, mongo_db, TEXT, "second")
    other = await service.resolve_entities(access, batch_for(second, profile))
    assert [entity.entity.id for entity in result.entities] == [
        entity.entity.id for entity in other.entities
    ]
    assert result.assertions[0].assertion.id == other.assertions[0].assertion.id
    assert result.assertions[0].support.id != other.assertions[0].support.id
    ns = {"org_id": str(access.org_id), "kg_id": str(first.kg_id)}
    assert await mongo_db.entities.count_documents(ns) == 2
    assert await mongo_db.supports.count_documents(ns) == 2
    assert await mongo_db.observations.count_documents(ns) == 4
    assert not any(
        "name" in row or "description" in row for row in await mongo_db.entities.find(ns).to_list()
    )
    assert await mongo_db.engine_names.count_documents(ns) == 4
    assert (await mongo_db.kgs.find_one({"id": str(first.kg_id)}))["published_revision"] == 0
    with pytest.raises(DomainError):
        await service.resolve_entities(
            access, batch_for(first, profile).model_copy(update={"kg_id": uuid4()})
        )


async def test_homonyms_without_grounded_identity_are_not_name_merged(
    material_service, team, mongo_db
):
    from domain_kg.ingestion.resolution import ResolutionService

    text = "Bank grants loans. Bank lines the river."
    access, chunk = await prepared(material_service, team, mongo_db, text, "banks")
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    split = text.index(" Bank")
    a = Span(location=chunk.spans[0].location, start=0, end=split, quote=text[:split])
    b = Span(
        location=chunk.spans[0].location, start=split + 1, end=len(text), quote=text[split + 1 :]
    )
    batch = ExtractionBatch(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        material_id=chunk.material_id,
        version_id=chunk.version_id,
        chunk_id=chunk.id,
        schema_id=schema.id,
        schema_revision=1,
        provider_profile_id=uuid4(),
        entities=(
            ExtractedEntity(
                local_id="a",
                name="Bank",
                entity_type="concept",
                description="grants loans",
                spans=(a,),
            ),
            ExtractedEntity(
                local_id="b",
                name="Bank",
                entity_type="concept",
                description="lines the river",
                spans=(b,),
            ),
        ),
        assertions=(),
    )
    result = await ResolutionService(team[1], material_service[0].router).resolve_entities(
        access, batch
    )
    assert result.entities[0].entity.id != result.entities[1].entity.id and not result.assertions
