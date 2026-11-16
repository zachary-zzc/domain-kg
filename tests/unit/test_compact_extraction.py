"""Numbered evidence reduces model work without weakening canonical validation."""

import json

import httpx
import pytest
from test_extraction_validation import proposal, source
from test_provider_extraction import configured

from domain_kg.errors import DomainError
from domain_kg.ingestion.models import ExtractionResult
from domain_kg.providers.budget import extraction_fits
from domain_kg.providers.extraction import ExtractionClient, extraction_payload
from domain_kg.providers.profiles import ProviderProfile


def compact_profile(chunk, format="source_refs_v1"):
    profile, connection = configured(chunk)
    profile = ProviderProfile.model_validate(
        profile.model_copy(
            update={
                "extraction_format": format,
                "extraction_context_tokens": 4096,
                "max_completion_tokens": 1024,
                "schema_in_context": False,
            }
        )
    )
    return profile, connection


def facts():
    return {
        "entities": [
            {"name": "Alpha", "type": "concept", "refs": [0]},
            {"name": "Beta", "type": "concept", "refs": [0]},
        ],
        "assertions": [
            {
                "subject": 0,
                "target": 1,
                "predicate": "uses",
                "polarity": "positive",
                "confidence": 0.9,
                "refs": [0],
                "qualifiers": [],
            }
        ],
    }


async def call(chunk, value, *, authorize=None, format="source_refs_v1"):
    profile, connection = compact_profile(chunk, format)
    _, schema = proposal(chunk)
    usage = []

    async def noop(_):
        pass

    async def record(item):
        usage.append(item)

    client = ExtractionClient(
        profile,
        connection,
        authorize=authorize or noop,
        record_usage=record,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(value)}}
                    ],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 100},
                },
            )
        ),
    )
    try:
        result = ExtractionResult.model_validate(await client.complete(chunk, schema))
        return result, usage
    finally:
        await client.close()


async def test_compact_request_fits_small_model_and_restores_exact_source_locations():
    chunk = source("Alpha uses Beta.")
    profile, _ = compact_profile(chunk)
    _, schema = proposal(chunk)
    assert extraction_fits(chunk, schema, profile)
    request = extraction_payload(chunk, schema, profile)
    data = json.loads(request["messages"][1]["content"])
    assert data["sources"] == [{"id": 0, "text": "Alpha uses Beta."}]
    assert "tools" not in request and str(chunk.org_id) in request["messages"][0]["content"]
    result, usage = await call(chunk, facts())
    assert [e.name for e in result.entities] == ["Alpha", "Beta"]
    assert result.assertions[0].subject == result.entities[0].local_id
    assert result.assertions[0].target == result.entities[1].local_id
    assert result.assertions[0].spans == chunk.spans
    assert result.entities[0].spans == chunk.spans
    assert usage[0].input_tokens == 120


@pytest.mark.parametrize("format", ["source_refs_v1", "source_names_v1"])
async def test_unrelated_negative_sentence_does_not_invalidate_positive_evidence(format):
    chunk = source("Alpha uses Beta. Gamma does not use Delta.")
    original = chunk.model_dump_json()
    profile, _ = compact_profile(chunk, format)
    _, schema = proposal(chunk)
    payload = extraction_payload(chunk, schema, profile)
    assert json.loads(payload["messages"][1]["content"])["sources"] == [
        {"id": 0, "text": chunk.text},
    ]
    value = facts()
    if format == "source_names_v1":
        value["assertions"][0].update(subject="Alpha", target="Beta")
    result, usage = await call(chunk, value, format=format)
    assert len(usage) == 1
    assert result.assertions[0].spans[0].quote == chunk.text
    assert result.assertions[0].spans[0].location == chunk.spans[0].location
    assert chunk.model_dump_json() == original


async def test_source_refs_preserve_offsets_and_model_assigned_negation():
    chunk = source("Other facts. Alpha does not use Beta.")
    span = chunk.spans[0].model_copy(update={"start": 37, "end": 37 + len(chunk.text)})
    chunk = chunk.model_copy(update={"spans": (span,)})
    value = facts()
    value["assertions"][0]["polarity"] = "negative"
    result, _ = await call(chunk, value)
    evidence = result.assertions[0].spans[0]
    assert result.assertions[0].polarity == "negative"
    assert evidence.quote == chunk.text
    assert evidence.start == 37
    assert evidence.end == span.end


async def test_multiple_citations_do_not_override_claim_polarity_from_keywords():
    from test_chunking import document_blocks

    from domain_kg.ingestion.chunking import chunk_document
    from domain_kg.ingestion.models import ChunkConfig

    chunk = chunk_document(
        document_blocks(["Alpha uses Beta.", "Gamma does not use Delta."]), ChunkConfig()
    )[0]
    value = facts()
    value["assertions"][0]["refs"] = [0, 1]
    result, _ = await call(chunk, value)
    assert result.assertions[0].polarity == "positive"
    assert result.assertions[0].spans == chunk.spans
    assert not result.warnings


def test_canonical_evidence_preserves_names_abbreviations_decimals_and_chinese():
    from domain_kg.providers.compact import evidence_passages

    text = "Dr. A. Smith uses 1.5 mg of Beta. 阿尔法不使用贝塔。伽马使用德尔塔。"
    spans = evidence_passages(source(text))
    assert [s.quote for s in spans] == [text]
    assert all(text[s.start:s.end] == s.quote for s in spans)


@pytest.mark.parametrize(
    "bad",
    [
        "foreign_ref",
        "negative_ref",
        "bool_ref",
        "invented_name",
        "predicate",
        "type",
        "endpoint",
        "duplicate",
        "command",
    ],
)
async def test_bad_compact_fields_cannot_be_accepted(bad):
    chunk, value = source("Alpha uses Beta."), facts()
    if bad == "foreign_ref":
        value["entities"][0]["refs"] = [999]
    if bad == "negative_ref":
        value["entities"][0]["refs"] = [-1]
    if bad == "bool_ref":
        value["entities"][0]["refs"] = [False]
    if bad == "invented_name":
        value["entities"][0]["name"] = "Invented"
    if bad == "predicate":
        value["assertions"][0]["predicate"] = "grant_owner"
    if bad == "type":
        value["entities"][0]["type"] = "admin"
    if bad == "endpoint":
        value["assertions"][0]["target"] = 99
    if bad == "duplicate":
        value["entities"][1]["name"] = "Alpha"
    if bad == "command":
        value["commands"] = ["MATCH (n) DELETE n"]
    if bad in ("negative_ref", "bool_ref", "command"):
        with pytest.raises(DomainError):
            await call(chunk, value)
    else:
        from domain_kg.ingestion.extraction import validate_extraction

        result, usage = await call(chunk, value)
        assert not result.assertions and result.warnings
        assert len(usage) == 2
        template, schema = proposal(chunk)
        accepted = template.model_copy(
            update={
                "entities": result.entities,
                "assertions": result.assertions,
                "warnings": result.warnings,
            }
        )
        assert validate_extraction(chunk, schema, accepted) == accepted
        assert all(e.name != "Invented" and e.entity_type != "admin" for e in result.entities)


async def test_compact_preserves_negation_qualifiers_and_post_call_revocation():
    chunk = source("Alpha does not use Beta during winter.")
    value = facts()
    value["assertions"][0].update(
        polarity="negative", qualifiers=[{"name": "time", "value": "winter", "refs": [0]}]
    )
    result, _ = await call(chunk, value)
    assert result.assertions[0].polarity == "negative"
    assert result.assertions[0].qualifiers[0].value == "winter"
    count = 0

    async def revoke(_):
        nonlocal count
        count += 1
        if count == 2:
            raise DomainError("FORBIDDEN", "Revoked")

    with pytest.raises(DomainError):
        await call(chunk, value, authorize=revoke)


async def test_names_refer_to_exact_entities_without_model_generated_array_indices():
    value = facts()
    value["assertions"][0].update(subject="Alpha", target="Beta")
    result, _ = await call(source("Alpha uses Beta."), value, format="source_names_v1")
    assert result.assertions[0].subject == result.entities[0].local_id
    assert result.assertions[0].target == result.entities[1].local_id
    for target in ("Invented", "Alpha"):
        value["assertions"][0]["target"] = target
        result, _ = await call(source("Alpha uses Beta."), value, format="source_names_v1")
        assert not result.assertions
        assert result.warnings == ("extraction_rejected_assertions:1",)


def test_medical_schema_can_express_the_leaflet_without_inventing_relations():
    from domain_kg.graph.schema import schema_for
    from domain_kg.ingestion.extraction import validate_extraction
    from domain_kg.models import Namespace

    chunk = source("The thyroid gland produces a hormone.")
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "medical")
    batch, _ = proposal(chunk)
    entities = tuple(
        e.model_copy(
            update={
                "name": name,
                "entity_type": kind,
                "description": "",
                "aliases": (),
                "identifier": None,
            }
        )
        for e, name, kind in zip(
            batch.entities, ("thyroid gland", "hormone"), ("anatomy", "substance"), strict=True
        )
    )
    batch = batch.model_copy(
        update={
            "schema_id": schema.id,
            "entities": entities,
            "assertions": (batch.assertions[0].model_copy(update={"predicate": "produces"}),),
        }
    )
    assert validate_extraction(chunk, schema, batch).assertions[0].predicate == "produces"
