import json

import pytest

from eval import kg_verify


def verify_reader(result, previous, ident="case-hash"):
    helper = getattr(kg_verify, "reader_call_label", None)
    assert callable(helper), "Reused evaluations need their original reader provenance"
    return helper(result, ident, previous)


def original(tmp_path, **changes):
    folder = tmp_path / "evaluations"
    folder.mkdir()
    row = {
        "id": "case-hash",
        "case_id": "question-id",
        "reader": {"answer": "Kansas City"},
        "calls": {"reader": "reader/request-hash:0"},
        **changes,
    }
    (folder / "case-hash.json").write_text(json.dumps(row))


def test_reused_reasoned_evaluation_replays_the_original_reader(tmp_path):
    original(tmp_path)
    result = {
        "case_id": "question-id",
        "reader": {"answer": "Kansas City"},
        "calls": {"blind": "reasoned-blind/other:0"},
        "reused_evaluation": "archived/reasoned/case-hash.json",
    }
    assert verify_reader(result, tmp_path) == "reader/request-hash:0"


@pytest.mark.parametrize(
    "changes",
    [{"reader": {"answer": "Boston"}}, {"id": "different-packet"}, {"case_id": "other-question"}],
)
def test_reused_reader_must_match_original_packet_identity_and_answer(tmp_path, changes):
    original(tmp_path, **changes)
    result = {
        "case_id": "question-id",
        "reader": {"answer": "Kansas City"},
        "calls": {},
        "reused_evaluation": "archived/reasoned/case-hash.json",
    }
    with pytest.raises(ValueError, match="original reader"):
        verify_reader(result, tmp_path)


def test_new_evaluation_cannot_fall_back_to_an_unrelated_old_reader(tmp_path):
    original(tmp_path)
    with pytest.raises(ValueError, match="reader call"):
        verify_reader({"calls": {}}, tmp_path)


def test_new_evaluation_keeps_its_explicit_reader_call(tmp_path):
    assert verify_reader({"calls": {"reader": "reader/new:0"}}, tmp_path) == "reader/new:0"


@pytest.mark.parametrize("qualified", [False, True])
def test_initial_graph_replay_preserves_optional_qualifiers_and_source_identity(qualified):
    helper = getattr(kg_verify, "initial_fact_keys", None)
    assert callable(helper), "Historical facts may omit the empty qualifier list"
    quote = "Alpha joined Beta in 2020."
    fact = {"s": "Alpha", "p": "member_of", "o": "Beta", "n": "positive", "q": quote}
    qualifiers = [{"kind": "time", "value": "2020", "q": "2020"}] if qualified else []
    if qualified:
        fact["qualifiers"] = qualifiers
    step = {
        "document": "source-a",
        "subject": "Alpha",
        "predicate": "member_of",
        "object": "Beta",
        "polarity": "positive",
        "quote": quote,
        "qualifiers": qualifiers,
    }
    docs = {"source-a": {"title": "Club", "text": quote, "dataset": "fixture"}}
    packet = {
        "evidence": {
            "sources": [{"id": "source-a", "text": "Club\n" + quote}],
            "paths": [[step]],
        }
    }
    keys = helper({"source-a": [fact]})
    kg_verify.verify_packet(packet, docs, keys)
    assert ("qualifiers" in fact) is qualified
    with pytest.raises(ValueError, match="not in this model's graph"):
        kg_verify.verify_packet(packet, docs, helper({"different-source": [fact]}))
    step["qualifiers"] = [] if qualified else [{"kind": "time", "value": "2021", "q": "2021"}]
    with pytest.raises(ValueError, match="not in this model's graph"):
        kg_verify.verify_packet(packet, docs, keys)
