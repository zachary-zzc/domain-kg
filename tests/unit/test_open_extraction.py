"""Content-derived vocabulary retains source checks and emits connected graph facts."""

import json
from uuid import uuid4

import httpx
import pytest
from test_extraction_validation import source
from test_provider_extraction import configured

from domain_kg.errors import DomainError
from domain_kg.graph.models import DomainSchema
from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.extraction import Extractor, validate_extraction
from domain_kg.ingestion.models import ExtractedAssertion, ExtractedEntity, ExtractionBatch
from domain_kg.models import Namespace
from domain_kg.providers.extraction import ExtractionClient, extraction_payload


def sample(profile="general"):
    chunk = source("PanelX requires Saliva. Flyer.")
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), profile)
    batch = ExtractionBatch(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        material_id=chunk.material_id,
        version_id=chunk.version_id,
        chunk_id=chunk.id,
        schema_id=schema.id,
        schema_revision=schema.revision,
        provider_profile_id=uuid4(),
        entities=tuple(
            ExtractedEntity(local_id=key, name=name, entity_type=kind, spans=chunk.spans)
            for key, name, kind in (
                ("a", "PanelX", "assay_panel"),
                ("b", "Saliva", "specimen"),
                ("c", "Flyer", "document"),
            )
        ),
        assertions=(
            ExtractedAssertion(
                subject="a",
                target="b",
                predicate="requires_sample",
                confidence=0.9,
                spans=chunk.spans,
            ),
        ),
    )
    return chunk, schema, batch


@pytest.mark.parametrize("profile", ["general", "medical", "scientific"])
def test_builtin_profiles_accept_content_derived_types_and_predicates(profile):
    chunk, schema, batch = sample(profile)
    assert validate_extraction(chunk, schema, batch) == batch


def test_legacy_schema_stays_closed_without_changing_its_serialization():
    chunk, _, batch = sample()
    legacy = {
        "org_id": str(chunk.org_id),
        "kg_id": str(chunk.kg_id),
        "id": str(batch.schema_id),
        "revision": batch.schema_revision,
        "entity_types": ["concept"],
        "predicates": [],
    }
    schema = DomainSchema.model_validate(legacy)
    assert schema.model_dump(mode="json") == legacy
    with pytest.raises(DomainError):
        validate_extraction(chunk, schema, batch)


@pytest.mark.parametrize("field", ["name", "spans", "namespace", "predicate"])
def test_open_vocabulary_does_not_bypass_source_or_label_validation(field):
    chunk, schema, batch = sample()
    if field == "name":
        batch = batch.model_copy(
            update={
                "entities": (
                    batch.entities[0].model_copy(update={"name": "Invented"}),
                    *batch.entities[1:],
                )
            }
        )
    elif field == "spans":
        assertion = batch.assertions[0].model_copy(
            update={
                "spans": (chunk.spans[0].model_copy(update={"quote": "x" * len(chunk.text)}),),
            }
        )
        batch = batch.model_copy(update={"assertions": (assertion,)})
    elif field == "namespace":
        batch = batch.model_copy(update={"kg_id": uuid4()})
    else:
        batch = batch.model_copy(
            update={
                "assertions": (batch.assertions[0].model_copy(update={"predicate": "x" * 201}),)
            }
        )
    with pytest.raises(DomainError):
        validate_extraction(chunk, schema, batch)


@pytest.mark.parametrize("has_relation", [True, False])
async def test_extractor_keeps_only_endpoints_of_validated_relations(has_relation):
    chunk, schema, batch = sample()
    if not has_relation:
        batch = batch.model_copy(update={"assertions": ()})

    class Provider:
        id = batch.provider_profile_id

        async def complete(self, incoming, incoming_schema):
            return batch.model_dump(mode="json", include={"entities", "assertions"})

    result = await Extractor(Provider()).extract(chunk, schema)
    assert {entity.name for entity in result.entities} == (
        {"PanelX", "Saliva"} if has_relation else set()
    )
    assert result.assertions == batch.assertions
    assert chunk.text == "PanelX requires Saliva. Flyer."


async def test_native_mapping_preserves_content_types_containing_structural_punctuation():
    chunk, schema, batch = sample()
    batch = batch.model_copy(
        update={
            "entities": (
                batch.entities[0].model_copy(update={"entity_type": "Test/Service"}),
                batch.entities[1].model_copy(update={"entity_type": "Specimen (biological)"}),
            )
        }
    )

    class Provider:
        id = batch.provider_profile_id

        async def complete(self, incoming, incoming_schema):
            return batch.model_dump(mode="json", include={"entities", "assertions"})

    result = await Extractor(Provider()).extract(chunk, schema)
    assert {entity.entity_type for entity in result.entities} == {
        "Test/Service",
        "Specimen (biological)",
    }
    assert result.assertions == batch.assertions


@pytest.mark.parametrize("format", ["full", "source_refs_v1", "source_names_v1"])
async def test_provider_formats_accept_new_labels_without_an_enumerated_vocabulary(format):
    chunk, schema, batch = sample()
    profile, connection = configured(chunk)
    profile = profile.model_copy(
        update={
            "extraction_format": format,
            "extraction_context_tokens": 32768,
        }
    )
    if format == "full":
        content = batch.model_dump(mode="json", include={"entities", "assertions"})
    else:
        content = {
            "entities": [
                {"name": entity.name, "type": entity.entity_type, "refs": [0]}
                for entity in batch.entities
            ],
            "assertions": [
                {
                    "subject": "PanelX" if format == "source_names_v1" else 0,
                    "target": "Saliva" if format == "source_names_v1" else 1,
                    "predicate": "requires_sample",
                    "polarity": "positive",
                    "confidence": 0.9,
                    "refs": [0],
                    "qualifiers": [],
                }
            ],
        }
        shape = extraction_payload(chunk, schema, profile)["response_format"]["json_schema"][
            "schema"
        ]
        entity_shape = shape["$defs"]["RefEntity"]["properties"]["type"]
        assertion_shape = shape["$defs"][
            "NamedAssertion" if format == "source_names_v1" else "RefAssertion"
        ]["properties"]["predicate"]
        assert "enum" not in entity_shape and "enum" not in assertion_shape

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {"finish_reason": "stop", "message": {"content": json.dumps(content)}}
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 100},
                },
            )
        ),
    )
    try:
        result = await Extractor(client).extract(chunk, schema)
    finally:
        await client.close()
    assert len(result.entities) == 2
    assert result.assertions[0].predicate == "requires_sample"


@pytest.mark.parametrize("format", ["full", "source_refs_v1", "source_names_v1"])
def test_open_requests_do_not_anchor_the_model_to_preset_label_lists(format):
    chunk, schema, _ = sample()
    profile, _ = configured(chunk)
    profile = profile.model_copy(
        update={
            "extraction_format": format,
            "extraction_semantics": "source_roles_v1",
        }
    )
    payload = extraction_payload(chunk, schema, profile)
    data = json.loads(payload["messages"][1]["content"])
    assert "types" not in data and "predicates" not in data
    if format == "full":
        assert data["domain_schema"] == {"vocabulary": "open"}
    assert "related_to:" not in payload["messages"][0]["content"]


async def test_rejected_relation_cannot_leave_an_orphan_after_the_corrective_response():
    chunk, schema, batch = sample()
    # The orphan's mention is valid, but its proposed link has no endpoint evidence.
    relation = batch.assertions[0].model_copy(
        update={
            "target": "c",
            "spans": (
                chunk.spans[0].model_copy(
                    update={
                        "quote": "PanelX requires Saliva.",
                        "end": len("PanelX requires Saliva."),
                    }
                ),
            ),
        }
    )
    batch = batch.model_copy(update={"assertions": (*batch.assertions, relation)})
    profile, connection = configured(chunk)
    usage = []

    async def noop(_):
        pass

    async def record(value):
        usage.append(value)

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=record,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": batch.model_dump_json(
                                    include={"entities", "assertions"}
                                ),
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 100},
                },
            )
        ),
    )
    try:
        result = await Extractor(client).extract(chunk, schema)
    finally:
        await client.close()
    assert len(usage) == 2
    assert {e.name for e in result.entities} == {"PanelX", "Saliva"}
    assert len(result.assertions) == 1
    assert result.warnings == ("extraction_rejected_assertions:1",)


def test_closed_endpoint_constraints_remain_in_force():
    chunk, _, batch = sample()
    from domain_kg.graph.models import PredicateDefinition

    schema = DomainSchema(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        id=batch.schema_id,
        revision=batch.schema_revision,
        entity_types=("assay_panel", "specimen", "document"),
        predicates=(
            PredicateDefinition(
                name="requires_sample",
                subject_types=("assay_panel",),
                target_types=("specimen",),
            ),
        ),
    )
    assert validate_extraction(chunk, schema, batch) == batch
    reversed_claim = batch.assertions[0].model_copy(update={"subject": "b", "target": "a"})
    with pytest.raises(DomainError):
        validate_extraction(
            chunk, schema, batch.model_copy(update={"assertions": (reversed_claim,)})
        )
