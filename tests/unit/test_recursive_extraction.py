"""Algorithm boundaries, not assertions about a mocked model's semantic ability."""

import importlib
from uuid import uuid4

import pytest

from domain_kg.graph.models import DomainSchema, PredicateDefinition
from domain_kg.ingestion.models import ParsedChunk, SourceLocation, Span


def api():
    return importlib.import_module("eval.recursive_loop")


def source(lines=("Cholesterol is a lipid. It", "is made by the liver.")):
    org, kg, material, version = (uuid4() for _ in range(4))
    spans = tuple(
        Span(
            location=SourceLocation(kind="text", block_id=uuid4(), paragraph=i),
            start=0,
            end=len(line),
            quote=line,
        )
        for i, line in enumerate(lines)
    )
    chunk = ParsedChunk(
        org_id=org,
        kg_id=kg,
        material_id=material,
        version_id=version,
        id=uuid4(),
        text="\n".join(lines),
        spans=spans,
        parser_version="test-v1",
        chunker_version="test-v1",
    )
    schema = DomainSchema(
        org_id=org,
        kg_id=kg,
        id=uuid4(),
        revision=1,
        entity_types=("substance", "anatomy", "concept"),
        predicates=tuple(
            PredicateDefinition(
                name=p,
                subject_types=("substance", "anatomy", "concept"),
                target_types=("substance", "anatomy", "concept"),
            )
            for p in ("produces", "is_a")
        ),
    )
    return chunk, schema


ENTITIES = {
    "entities": [
        {"name": "Cholesterol", "type": "substance", "refs": [0]},
        {"name": "lipid", "type": "substance", "refs": [0]},
        {"name": "liver", "type": "anatomy", "refs": [1]},
    ]
}


def relation(subject="liver", predicate="produces", target="Cholesterol", refs=(0, 1)):
    return {
        "subject": subject,
        "predicate": predicate,
        "target": target,
        "polarity": "positive",
        "refs": list(refs),
    }


def reviews(payload, status="supported"):
    return {
        "verdicts": [
            {"id": c["id"], "status": status, "refs": c["refs"]} for c in payload["candidates"]
        ]
    }


async def test_one_bad_fact_does_not_discard_an_independent_good_fact():
    chunk, schema = source()

    async def complete(stage, payload, response_model):
        if stage == "mentions":
            return ENTITIES
        if stage == "relations":
            return {"relations": [relation(), relation(target="invented")]}
        if stage == "verify":
            return reviews(payload)
        return {"relations": []}

    result = await api().run_recursive(chunk, schema, complete, max_repairs=0)
    assert len(result.accepted.assertions) == 1
    assert result.issues
    assert result.calls == 3


async def test_ref_repair_requires_revalidation_against_exact_source():
    chunk, schema = source()

    async def complete(stage, payload, response_model):
        if stage == "mentions":
            return ENTITIES
        if stage == "relations":
            return {"relations": [relation(refs=(1,))]}
        if stage == "repair":
            assert payload["issues"]
            return {"relations": [relation()]}
        return reviews(payload)

    result = await api().run_recursive(chunk, schema, complete, max_repairs=1)
    assert not result.snapshots["decomposed"].assertions
    assert len(result.accepted.assertions) == 1
    assert {s.quote for s in result.accepted.assertions[0].spans} == {s.quote for s in chunk.spans}


async def test_unsupported_semantics_and_infinite_repeated_repairs_are_not_accepted():
    chunk, schema = source()

    async def complete(stage, payload, response_model):
        if stage == "mentions":
            return ENTITIES
        if stage in {"relations", "repair"}:
            return {"relations": [relation("Cholesterol", "is_a", "liver")]}
        return reviews(payload, "unsupported")

    result = await api().run_recursive(chunk, schema, complete, max_repairs=2)
    assert not result.accepted.assertions
    assert result.calls <= 7
    assert len(result.snapshots["decomposed"].assertions) == 1
    assert result.issues


@pytest.mark.parametrize("kind", ["foreign", "duplicate", "omitted"])
async def test_invalid_review_bindings_cannot_approve_candidates(kind):
    chunk, schema = source()

    async def complete(stage, payload, response_model):
        if stage == "mentions":
            return ENTITIES
        if stage == "relations":
            return {"relations": [relation()]}
        if stage == "verify":
            verdicts = reviews(payload)["verdicts"]
            if kind == "foreign":
                verdicts[0]["id"] = "foreign"
            elif kind == "duplicate":
                verdicts *= 2
            else:
                verdicts = []
            return {"verdicts": verdicts}
        return {"relations": []}

    result = await api().run_recursive(chunk, schema, complete, max_repairs=0)
    assert not result.accepted.assertions
    assert result.issues


async def test_repairs_cannot_replace_a_previously_accepted_fact():
    chunk, schema = source()

    async def complete(stage, payload, response_model):
        if stage == "mentions":
            return ENTITIES
        if stage == "relations":
            return {"relations": [relation()]}
        if stage == "repair":
            return {"relations": [relation("Cholesterol", "produces", "liver")]}
        return reviews(
            payload,
            "supported" if payload["candidates"][0]["subject"] == "liver" else "unsupported",
        )

    result = await api().run_recursive(chunk, schema, complete, max_repairs=2)
    assert len(result.accepted.assertions) == 1
    names = {e.local_id: e.name for e in result.accepted.entities}
    assert names[result.accepted.assertions[0].subject] == "liver"


async def test_foreign_schema_is_rejected_before_any_model_call():
    chunk, schema = source()
    schema = schema.model_copy(update={"kg_id": uuid4()})

    async def complete(*args):
        pytest.fail("A foreign schema must never reach a model")

    with pytest.raises(ValueError, match="namespace"):
        await api().run_recursive(chunk, schema, complete)


async def test_reviewer_uses_short_ids_but_must_cite_both_endpoints():
    chunk, schema = source()

    async def complete(stage, payload, response_model):
        if stage == "mentions":
            return ENTITIES
        if stage == "relations":
            return {"relations": [relation()]}
        if stage == "verify":
            assert all(len(c["id"]) <= 3 for c in payload["candidates"])
            verdicts = reviews(payload)
            verdicts["verdicts"][0]["refs"] = [0]  # This line does not contain liver.
            return verdicts
        return {"relations": []}

    result = await api().run_recursive(chunk, schema, complete, max_repairs=0)
    assert not result.accepted.assertions
    assert result.issues


@pytest.mark.parametrize("value", [-1, 3, True])
async def test_recursive_budget_cannot_be_expanded(value):
    chunk, schema = source()
    with pytest.raises(ValueError):
        await api().run_recursive(chunk, schema, None, max_repairs=value)
