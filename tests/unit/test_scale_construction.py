import json

from eval import scale_construction as scale

DOC = {"id": "d1", "dataset": "one", "title": "Work", "text": "Work was written by Ada."}
FACT = {"s": "Work", "p": "created_by", "o": "Ada", "n": "positive", "q": DOC["text"]}


def test_correction_blinds_gold_and_uses_own_model():
    request = scale.correction_request(
        "qwen3.5-122b-a10b",
        [{**DOC, "answers": ["DO NOT LEAK"], "support": ["DO NOT LEAK"]}],
        {"d1": [FACT]},
        {"d1": [{**DOC, "id": "d2", "answers": ["DO NOT LEAK"]}]},
        {"d1": [FACT]},
    )
    assert request["model"] == "qwen3.5-122b-a10b"
    assert "DO NOT LEAK" not in json.dumps(request)
    assert json.loads(request["messages"][1]["content"])["items"][0]["id"] == "s0"


def test_batch_validation_isolates_borrowed_quotes_and_missing_sources():
    other = {**DOC, "id": "d2", "text": "Work was performed by Ada."}
    raw = json.dumps({"items": [{"id": "s0", "facts": [FACT, {**FACT, "q": other["text"]}]}]})
    result = scale.parse_correction(raw, [DOC, other])
    assert len(result["d1"]["facts"]) == 1
    assert len(result["d1"]["rejections"]) == 1
    assert result["d2"]["error"] == "missing_or_duplicate_source"
    duplicate = json.dumps({"items": [{"id": "s0", "facts": [FACT]}] * 2})
    assert scale.parse_correction(duplicate, [DOC])["d1"]["facts"] == []


def test_related_context_never_crosses_datasets_or_leaks_gold():
    docs = [DOC, {**DOC, "id": "d2"}, {**DOC, "id": "d3", "dataset": "other"}]
    related = scale.related_sources(docs)
    assert [d["id"] for d in related["d1"]] == ["d2"]


def test_unsupported_wire_flags_are_omitted_for_distilled_model():
    request = scale.correction_request(scale.MODELS[-1], [DOC], {}, {}, {})
    assert "enable_thinking" not in request
    assert "response_format" not in request
