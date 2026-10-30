import json

from eval.source_role_audit import audit_request
from eval.source_role_eval import EXTRACTOR


def test_role_audit_preserves_semantic_equivalence_and_blinds_extraction_arm():
    doc = {
        "id": "d",
        "title": "Journal",
        "text": "Journal is published by Publisher.",
        "answers": ["private answer"],
        "question": "private question",
    }
    facts = [
        {
            "id": "f",
            "s": "Journal",
            "p": "created_by",
            "o": "Publisher",
            "n": "positive",
            "qualifiers": [],
        }
    ]
    request = audit_request(doc, facts, [], EXTRACTOR)
    system = request["messages"][0]["content"]
    assert "publisher, owner or subject credits alone do not establish authorship" in system
    assert "NOT exact" in system and "equivalent" in system
    data = json.loads(request["messages"][1]["content"])
    assert data["candidates"] == facts
    assert "private" not in json.dumps(request)
    assert "source_repair" not in json.dumps(request)
