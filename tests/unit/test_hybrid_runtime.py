"""Freeze isolation and review provenance before spending real inference."""

import importlib

import pytest


def api():
    return importlib.import_module("eval.hybrid_run")


def test_requests_use_only_original_context_and_question_never_gold_or_arm():
    request = api().answer_request("local", "Which gland?", [{"id": "c1", "text": "A fact."}])
    message = request["messages"][1]["content"]
    assert "A fact." in message and "Which gland?" in message
    assert "expected" not in message and "arm" not in message
    assert request["seed"] == 19 and request["max_completion_tokens"] == 128
    assert request["response_format"]["json_schema"]["schema"]["properties"]["citations"]["items"][
        "enum"
    ] == ["c1"]


def test_review_requires_every_grounded_candidate_exact_hash_and_reason():
    facts = [
        {"id": "a", "validation": "span_only_pending_review"},
        {"id": "b", "validation": "span_only_pending_review"},
    ]
    review = {
        "candidate_sha256": api().fingerprint(facts),
        "decisions": [
            {"id": "a", "supported": True, "reason": "Correct direction in literal source."},
            {"id": "b", "supported": False, "reason": "Source is explicitly negative."},
        ],
    }
    accepted = api().reviewed_facts(facts, review)
    assert len(accepted) == 1 and accepted[0]["validation"] == "agent_reviewed_supported"
    review["decisions"].pop()
    with pytest.raises(ValueError):
        api().reviewed_facts(facts, review)


def test_review_cannot_be_replayed_for_changed_candidates():
    with pytest.raises(ValueError):
        api().reviewed_facts([{"id": "other"}], {"candidate_sha256": "old", "decisions": []})


def test_extraction_prompt_is_bounded_and_not_a_retrieval_question():
    request = api().extraction_request("local", {"text": "Source only."})
    assert request["max_completion_tokens"] == 384
    assert request["messages"][1]["content"] == "Source only."
    schema = request["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["relations"]["maxItems"] == 2


def test_embedding_window_override_can_only_reduce_the_existing_allocation():
    from scripts.native_model import embedding_window

    assert embedding_window((2048, 2048, 2048), 512) == (512, 512, 512)
    assert embedding_window((2048, 2048, 2048), None) == (2048, 2048, 2048)
    for value in (4096, 0, True, "512"):
        with pytest.raises(ValueError):
            embedding_window((2048, 2048, 2048), value)


def test_short_window_partition_preserves_every_original_character():
    text = "Alpha uses Flux-7.  中文证据 remains intact. " * 8
    left, right = api().split_window(text)
    assert left and right and left + right == text
    assert len(left) < len(text) and len(right) < len(text)
