import copy
import json

import pytest

from eval.source_role_eval import (
    extraction_request,
    parse_facts,
    parse_judgments,
    reference_request,
    select_sources,
)

DOC = {"id": "d1", "dataset": "test", "title": "A", "text": "A uses B in 1990."}
FACT = {"s": "A", "p": "uses", "o": "B", "n": "positive", "q": DOC["text"]}


def test_quote_provenance_and_qualifier_values_are_validated_separately_from_meaning():
    valid = {**FACT, "qualifiers": [{"name": "time", "value": "1990", "q": "in 1990"}]}
    assert len(parse_facts(json.dumps({"facts": [valid]}), DOC, 3)) == 1
    # A reversed relation still passes provenance, so must go to semantic review.
    reversed_fact = {**FACT, "s": "B", "o": "A"}
    assert parse_facts(json.dumps({"facts": [reversed_fact]}), DOC, 3)
    for change in ({"q": "fabricated"}, {"p": "grant_owner"}, {"s": "missing"}):
        with pytest.raises(ValueError):
            parse_facts(json.dumps({"facts": [{**FACT, **change}]}), DOC, 3)
    broken = copy.deepcopy(valid)
    broken["qualifiers"][0]["value"] = "2000"
    with pytest.raises(ValueError):
        parse_facts(json.dumps({"facts": [broken]}), DOC, 3)
    with pytest.raises(ValueError):
        parse_facts(json.dumps({"facts": [FACT] * 4}), DOC, 3)


def test_construction_and_reference_requests_cannot_include_qa_answers():
    private = {**DOC, "question": "secret question", "answer": "secret answer"}
    for arm in ("baseline", "roles", "coverage"):
        request = extraction_request(private, arm)
        assert "secret" not in json.dumps(request)
        assert request["max_tokens"] == 4096
    assert "secret" not in json.dumps(reference_request(private))
    assert "source_roles_v1" not in json.dumps(extraction_request(DOC, "baseline"))
    assert "source_roles_v1" in json.dumps(extraction_request(DOC, "roles"))


def test_judge_must_cover_every_blind_fact_and_reference_without_duplicate_ids():
    value = {
        "facts": [
            {"id": "f1", "verdict": "supported", "reason": "Equivalent wording", "matches": ["r1"]}
        ],
        "references": [{"id": "r1", "verdict": "supported", "reason": "Source states it"}],
    }
    assert parse_judgments(json.dumps(value), {"f1"}, {"r1"})
    value["facts"][0]["matches"] = ["unknown"]
    with pytest.raises(ValueError):
        parse_judgments(json.dumps(value), {"f1"}, {"r1"})
    value["facts"] = []
    with pytest.raises(ValueError):
        parse_judgments(json.dumps(value), {"f1"}, {"r1"})


def test_selection_is_source_only_reproducible_and_excludes_reviewed_sources():
    docs = [{**DOC, "id": f"d{i}", "dataset": str(i % 4)} for i in range(40)]
    selected = select_sources(docs, ["d0"], {"d1", "d2"}, per_dataset=2)
    assert selected == select_sources(list(reversed(docs)), ["d0"], {"d1", "d2"}, 2)
    assert len(selected) == 9
    assert not {"d1", "d2"} & {s["id"] for s in selected}
    assert selected[0]["split"] == "development"
