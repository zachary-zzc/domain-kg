"""A corrective model response cannot erase independently grounded original facts."""

import json

import httpx
import pytest
from test_extraction_validation import source
from test_provider_extraction import configured

from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.extraction import Extractor
from domain_kg.models import Namespace
from domain_kg.providers.extraction import ExtractionClient


@pytest.mark.parametrize("format", ["full", "source_refs_v1", "source_names_v1"])
@pytest.mark.parametrize("repeat_original", [False, True])
async def test_correction_preserves_valid_original_edges_and_remaps_colliding_ids(
    format, repeat_original
):
    chunk = source("PanelX requires Saliva. Flyer describes PanelX.")
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    profile, connection = configured(chunk)
    profile = profile.model_copy(update={"extraction_format": format})
    content = [
        {
            "entities": [
                {"name": "PanelX", "type": "panel", "refs": [0]},
                {"name": "Saliva", "type": "sample", "refs": [0]},
            ],
            "assertions": [
                {
                    "subject": 0,
                    "target": 1,
                    "predicate": "requires",
                    "polarity": "positive",
                    "confidence": 0.9,
                    "refs": [0],
                    "qualifiers": [],
                },
                {
                    "subject": 0,
                    "target": 99,
                    "predicate": "requires",
                    "polarity": "positive",
                    "confidence": 0.9,
                    "refs": [0],
                    "qualifiers": [],
                },
            ],
        },
        {
            # Index zero now denotes a different entity. Do not join by local ID.
            "entities": [
                {"name": "Flyer", "type": "document", "refs": [0]},
                {"name": "PanelX", "type": "panel", "refs": [0]},
            ],
            "assertions": [
                {
                    "subject": 0,
                    "target": 1,
                    "predicate": "describes",
                    "polarity": "positive",
                    "confidence": 0.9,
                    "refs": [0],
                    "qualifiers": [],
                },
            ],
        },
    ]
    if repeat_original:
        content[1]["entities"].append({"name": "Saliva", "type": "sample", "refs": [0]})
        content[1]["assertions"].append(
            {**content[0]["assertions"][0], "subject": 1, "target": 2, "confidence": 0.8}
        )
    for response in content:
        if format == "full":
            response["entities"] = [
                {
                    "local_id": f"e{i}",
                    "name": entity["name"],
                    "entity_type": entity["type"],
                    "spans": [chunk.spans[0].model_dump(mode="json")],
                }
                for i, entity in enumerate(response["entities"])
            ]
        for assertion in response["assertions"]:
            if format == "full":
                assertion["subject"] = f"e{assertion['subject']}"
                assertion["target"] = f"e{assertion['target']}"
                assertion.pop("refs")
                assertion["spans"] = [chunk.spans[0].model_dump(mode="json")]
            elif format == "source_names_v1":
                for endpoint in ("subject", "target"):
                    index = assertion[endpoint]
                    assertion[endpoint] = (
                        response["entities"][index]["name"]
                        if index < len(response["entities"])
                        else "Missing"
                    )
    calls = []

    def respond(request):
        item = content[len(calls)]
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(item)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )

    async def noop(_):
        pass

    client = ExtractionClient(
        profile,
        connection,
        authorize=noop,
        record_usage=noop,
        transport=httpx.MockTransport(respond),
    )
    try:
        batch = await Extractor(client).extract(chunk, schema)
    finally:
        await client.close()
    entities = {entity.local_id: entity.name for entity in batch.entities}
    assert {(entities[a.subject], a.predicate, entities[a.target]) for a in batch.assertions} == {
        ("PanelX", "requires", "Saliva"),
        ("Flyer", "describes", "PanelX"),
    }
    assert len(batch.entities) == 3 and len(calls) == 2
    assert not batch.warnings
