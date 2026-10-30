import pytest

from eval.scale_verify import verify_ledger, verify_packet


def packet():
    return {"evidence": {"sources": [{"id": "s", "text": "Title\nAlpha uses Beta."}], "paths": []}}


def test_verification_rejects_substituted_source_text_and_extra_budget():
    docs = {"s": {"title": "Title", "text": "Alpha uses Beta."}}
    verify_packet(packet(), docs, set())
    changed = packet()
    changed["evidence"]["sources"][0]["text"] += " Invented evidence."
    with pytest.raises(ValueError, match="source text"):
        verify_packet(changed, docs, set())
    with pytest.raises(ValueError, match="budget"):
        verify_packet(packet(), docs, set(), max_chars=5)


def test_verification_rejects_path_without_matching_model_fact():
    docs = {"s": {"title": "Title", "text": "Alpha uses Beta."}}
    changed = packet()
    changed["evidence"]["paths"] = [
        [
            {
                "document": "s",
                "subject": "Alpha",
                "predicate": "uses",
                "object": "Beta",
                "polarity": "positive",
                "quote": "Alpha uses Beta.",
                "qualifiers": [],
            }
        ]
    ]
    with pytest.raises(ValueError, match="path fact"):
        verify_packet(changed, docs, set())


def test_ledger_keeps_ambiguous_attempts_and_rejects_changed_payload(tmp_path):
    import json

    from eval.source_role_eval import digest

    payload = {"model": "qwen3.8-27b", "max_tokens": 10}
    label = "construction/example:0"
    path = tmp_path / (digest(label) + ".launch.json")
    row = {"label": label, "payload": payload, "request_sha256": digest(payload)}
    path.write_text(json.dumps(row))
    result = verify_ledger(tmp_path)
    assert (
        result["attempts"] == result["unknown_cost_attempts"] == result["ambiguous_launches"] == 1
    )
    row["payload"]["max_tokens"] += 1
    path.write_text(json.dumps(row))
    with pytest.raises(ValueError, match="ledger payload"):
        verify_ledger(tmp_path)
