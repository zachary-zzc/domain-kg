import json

import pytest

from eval.source_role_evidence import evidence_request, parse_evidence_judgment


def packet():
    return {
        "id": "q1",
        "question": "Who wrote the work?",
        "answers": [["Ada"]],
        "reference_sources": [{"id": "r", "text": "The work was written by Ada."}],
        "arms": {"RAG": {"sources": [{"id": "s1", "text": "Ada authored the work."}], "paths": []}},
    }


def test_evidence_review_accepts_semantics_and_keeps_answer_out_of_candidate_text():
    request = evidence_request(packet(), "RAG")
    assert "not exact" in request["messages"][0]["content"]
    data = json.loads(request["messages"][1]["content"])
    assert data["candidate_evidence"]["sources"][0]["text"] == "Ada authored the work."
    assert data["reference_answers"] == [["Ada"]]
    assert "RAG" not in json.dumps(data)  # arm label is blinded


def test_evidence_judge_requires_valid_citations_and_uncertainty_not_exact_answer():
    value = {
        "reference_status": "clear",
        "sufficiency": "sufficient",
        "answer": "Ada",
        "citations": ["s1"],
        "path_status": "none",
        "reason": "Author paraphrase is supported",
    }
    assert parse_evidence_judgment(json.dumps(value), {"s1"}, False)["answer"] == "Ada"
    value["citations"] = ["invented"]
    with pytest.raises(ValueError):
        parse_evidence_judgment(json.dumps(value), {"s1"}, False)


def test_path_keeps_required_qualifiers_and_omits_them_when_support_is_not_visible(monkeypatch):
    from types import SimpleNamespace

    from eval import source_role_evidence as module

    fact = {
        "d": "s1",
        "s": "A",
        "p": "uses",
        "o": "B",
        "n": "positive",
        "q": "A uses B",
        "qualifiers": [{"name": "time", "value": "1990", "q": "in 1990"}],
    }
    step = dict(
        zip(
            ("document", "subject", "predicate", "object", "polarity", "quote"),
            (fact[k] for k in ("d", "s", "p", "o", "n", "q")),
            strict=True,
        )
    )
    monkeypatch.setattr(module, "path_context", lambda *_: {"paths": [[step]]})
    index = SimpleNamespace(facts={1: fact}, document_id=lambda _: "s1")
    assert (
        module.qualified_paths(index, [SimpleNamespace(text="A uses B in 1990.")], None)[0][0][
            "qualifiers"
        ]
        == fact["qualifiers"]
    )
    assert module.qualified_paths(index, [SimpleNamespace(text="A uses B")], None) == []


def test_cached_embedding_positions_are_preserved_without_provider_calls(tmp_path):
    from eval.public_qa_data import fingerprint
    from eval.source_role_evidence import vectors
    from eval.source_role_run import write_new

    docs, cases = [{"id": "a"}, {"id": "b"}], [{"id": "q"}]
    write_new(tmp_path / "corpus.json", docs)
    write_new(tmp_path / "cases.json", cases)
    a, b = [1.0] + [0.0] * 1023, [0.0, 1.0] + [0.0] * 1022
    for label, values in (("formal-docs:0", [a, b]), ("formal-queries:0", [a])):
        ident = fingerprint(["embedding", "text-embedding-v4", label])
        write_new(
            tmp_path / "calls" / (ident + ".json"),
            {"error": None, "request_sha256": "same", "vectors": values},
        )
    ranks, files = vectors(tmp_path, docs[::-1], cases)
    assert ranks == [["a", "b"]]
    assert len(files) == 2
