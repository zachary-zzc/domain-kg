import json

from eval import scale_single as scale


def test_one_original_per_correction_request_avoids_batch_omissions():
    doc = {"id": "d1", "title": "Work", "text": "Work was written by Ada.", "answers": ["SECRET"]}
    payload = scale.single_request("qwen3.5-122b-a10b", doc, [], [], [])
    body = json.loads(payload["messages"][1]["content"])
    assert body["source"]["id"] == "d1"
    assert "items" not in body
    assert "SECRET" not in json.dumps(payload)
    assert payload["model"] == "qwen3.5-122b-a10b"
    assert payload["max_tokens"] == 4096
