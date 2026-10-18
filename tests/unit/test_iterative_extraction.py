"""Bounded iteration accounting and literal grounding of the compact experiment."""

import importlib

import pytest


def api():
    return importlib.import_module("eval.iterative_extraction")


def test_five_failed_attempts_stop_without_resetting_on_a_new_model(tmp_path):
    tracker = api().Iterations(tmp_path / "iterations.json")
    for number in range(1, 6):
        tracker.begin(number, hypothesis=f"hypothesis {number}")
        tracker.finish(number, success=False, result={"model": f"model {number}"})
    with pytest.raises(RuntimeError, match="five"):
        tracker.begin(6, hypothesis="another model cannot reset failures")
    assert len(tracker.records) == 5


def test_an_in_progress_attempt_cannot_be_overwritten(tmp_path):
    tracker = api().Iterations(tmp_path / "iterations.json")
    tracker.begin(1, hypothesis="one")
    with pytest.raises(RuntimeError, match="already"):
        tracker.begin(1, hypothesis="silently replace it")


def test_compact_endpoints_align_to_source_case_without_inventing_text():
    from test_recursive_extraction import source

    chunk, schema = source()
    proposals = {
        "relations": [
            {
                "subject": "cholesterol",
                "predicate": "is_a",
                "target": "lipid",
                "polarity": "positive",
                "refs": [0],
            },
            {
                "subject": "liver",
                "predicate": "produces",
                "target": "Cholesterol",
                "polarity": "positive",
                "refs": [0, 1],
            },
            {
                "subject": "invented",
                "predicate": "is_a",
                "target": "lipid",
                "polarity": "positive",
                "refs": [0],
            },
        ]
    }
    content, rejected = api().ground(proposals, chunk, schema)
    assert len(content.assertions) == 2
    assert any(e.name == "Cholesterol" for e in content.entities)
    assert len(rejected) == 1


def test_compact_refs_cannot_escape_the_source():
    from test_recursive_extraction import source

    chunk, schema = source()
    content, rejected = api().ground(
        {
            "relations": [
                {
                    "subject": "liver",
                    "predicate": "produces",
                    "target": "Cholesterol",
                    "polarity": "positive",
                    "refs": [25],
                },
            ]
        },
        chunk,
        schema,
    )
    assert not content.assertions and rejected


def test_shared_endpoints_cannot_collide_when_new_entities_follow_existing_ones():
    from test_recursive_extraction import source

    chunk, schema = source(("X is a B. X is a C. D is a X.",))
    content, rejected = api().ground(
        {
            "relations": [
                {
                    "subject": s,
                    "predicate": "is_a",
                    "target": t,
                    "polarity": "positive",
                    "refs": [0],
                }
                for s, t in [("X", "B"), ("X", "C"), ("D", "X")]
            ]
        },
        chunk,
        schema,
    )
    assert not rejected and len(content.assertions) == 3
    assert len({e.local_id for e in content.entities}) == 4


def test_predicate_question_exposes_only_one_relation_choice():
    import json

    from test_recursive_extraction import source

    from eval.iterative_run import compact_request

    chunk, schema = source()
    request = compact_request(chunk, schema, "local", predicate="produces")
    response = request["response_format"]["json_schema"]["schema"]
    assert response["$defs"]["FlatRelation"]["properties"]["predicate"]["enum"] == ["produces"]
    assert set(json.loads(request["messages"][1]["content"])["predicates"]) == {"produces"}


def test_surface_mapping_reverses_a_literal_passive_and_rejects_cooccurrence():
    from test_recursive_extraction import source

    chunk, schema = source(
        (
            "A is a substance known as a lipid and is needed for the body. It",
            "is mainly made by the organ.",
        )
    )
    raw = {
        "relations": [
            {"subject": "A", "verb": "made by", "target": "organ", "refs": [0, 1]},
            {"subject": "A", "verb": "known as", "target": "lipid", "refs": [0]},
            {"subject": "A", "verb": "known as", "target": "body", "refs": [0]},
        ]
    }
    content, rejected = api().ground_surface(raw, chunk, schema)
    from eval.recursive_runtime import claims

    assert ["organ", "produces", "A", "positive"] in claims(content)
    assert ["A", "is_a", "lipid", "positive"] in claims(content)
    assert len(rejected) == 1


def test_surface_coreference_cannot_jump_over_another_sentence():
    from test_recursive_extraction import source

    chunk, schema = source(("A is a lipid. B is a protein. It is made by the organ.",))
    content, rejected = api().ground_surface(
        {
            "relations": [
                {"subject": "A", "verb": "made by", "target": "organ", "refs": [0]},
            ]
        },
        chunk,
        schema,
    )
    assert not content.assertions and rejected


def test_surface_request_only_allows_verbs_that_occur_in_the_actual_source():
    from test_recursive_extraction import source

    from eval.iterative_run import surface_request

    chunk, schema = source()
    request = surface_request(chunk, "local")
    props = request["response_format"]["json_schema"]["schema"]["$defs"]["SurfaceRelation"][
        "properties"
    ]
    assert "made by" in props["verb"]["enum"]
    assert "uses" not in props["verb"]["enum"]
