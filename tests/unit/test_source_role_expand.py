import json

import httpx
import pytest

from eval.source_role_eval import EXTRACTOR
from eval.source_role_expand import CumulativeLedger, repair_request, validated_facts
from eval.source_role_run import Ledger

DOC = {"id": "d1", "title": "Work", "text": "Work was written by Ada in 1990."}
FACT = {"s": "Work", "p": "created_by", "o": "Ada", "n": "positive", "q": DOC["text"]}


def test_per_fact_provenance_retains_valid_neighbors_and_reports_every_rejection():
    content = json.dumps({"facts": [FACT, {**FACT, "q": "invented"}, FACT]})
    result = validated_facts(content, DOC, 8)
    assert result["facts"] == [{**FACT, "qualifiers": []}]
    assert [r["reason"] for r in result["rejections"]] == ["provenance_or_shape", "duplicate"]
    assert result["raw_count"] == 3


def test_repair_cannot_receive_reference_inventory_or_question_answers():
    doc = {**DOC, "question": "secret question", "answers": ["secret answer"]}
    request = repair_request(doc, [FACT], [{**DOC, "id": "d2"}], [FACT])
    assert "secret" not in json.dumps(request)
    data = json.loads(request["messages"][1]["content"])
    assert set(data) == {"source", "proposals", "related_sources", "graph_hints"}
    assert "primary source" in request["messages"][0]["content"]
    assert request["model"] != EXTRACTOR


async def test_budget_counts_prior_phase_and_concurrent_reservations(tmp_path):
    import asyncio

    payload = {
        "model": EXTRACTOR,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": "Public"}],
    }
    prior = tmp_path / "prior"
    Ledger(prior).reserve("unknown-charge", payload)
    ledger = CumulativeLedger(tmp_path / "new", [prior], max_calls=3, max_rmb=500)
    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        rows = await asyncio.gather(
            *(ledger.call(client, str(i), payload, "secret") for i in range(3)),
            return_exceptions=True,
        )
    assert len(seen) == 2
    assert sum(isinstance(r, ValueError) for r in rows) == 1
    assert len(ledger.launches) == 3
    assert ledger.reserved > 0


def test_related_context_stays_in_dataset_and_does_not_borrow_support():
    from domain_kg.retrieval.ranking import BM25
    from eval.source_role_expand import related_context

    a = {**DOC, "dataset": "one"}
    b = {**a, "id": "d2", "text": "Ada performed Work."}
    hidden = {**b, "id": "d3", "dataset": "other"}
    rows = {d["id"]: {"arms": {"coverage": {"facts": [FACT]}}} for d in (a, b, hidden)}
    related, hints = related_context(
        a, [a, b, hidden], rows, BM25({d["id"]: d["text"] for d in (a, b, hidden)})
    )
    assert [r["id"] for r in related] == ["d2"]
    assert hints == []  # FACT's quoted support does not occur in d2.
    borrowed = {**FACT, "q": b["text"]}
    assert validated_facts(json.dumps({"facts": [borrowed]}), a, 8)["facts"] == []


def test_wide_judgment_requires_all_ids_and_supported_semantic_matches():
    from eval.source_role_expand import parse_wide_judgments

    facts = [
        {"id": str(i), "verdict": "supported", "reason": "Source entails this", "matches": ["r"]}
        for i in range(27)
    ]
    value = {
        "facts": facts,
        "references": [{"id": "r", "verdict": "supported", "reason": "Stated"}],
    }
    expected = {str(i) for i in range(27)}
    assert len(parse_wide_judgments(json.dumps(value), expected, {"r"})["facts"]) == 27
    value["references"][0]["matches"] = []
    assert parse_wide_judgments(json.dumps(value), expected, {"r"})
    value["references"][0]["matches"] = ["invented"]
    with pytest.raises(ValueError):
        parse_wide_judgments(json.dumps(value), expected, {"r"})
    value["references"][0].pop("matches")
    value["facts"][0]["verdict"] = "contradicted"
    with pytest.raises(ValueError):
        parse_wide_judgments(json.dumps(value), expected, {"r"})


async def test_two_stage_construction_preserves_blinding_and_reuses_identical_repair(tmp_path):
    from eval.source_role_expand import construct, repair
    from eval.source_role_run import read

    docs = [{**DOC, "dataset": "test"}]
    seen = []

    def respond(request):
        payload = json.loads(request.content)
        data = json.loads(payload["messages"][1]["content"])
        seen.append(data)
        if len(seen) == 1:
            value = {"references": [{"id": "r1", "claim": "Ada wrote Work", "q": DOC["text"]}]}
        elif "candidates" in data:
            value = {
                "facts": [
                    {"id": f["id"], "verdict": "supported", "reason": "Stated", "matches": ["r1"]}
                    for f in data["candidates"]
                ],
                "references": [{"id": "r1", "verdict": "supported", "reason": "Stated"}],
            }
        else:
            assert "references" not in data
            value = {"facts": [FACT]}
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 50},
            },
        )

    ledger = CumulativeLedger(tmp_path / "calls", [])
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        await construct(tmp_path, ledger, client, "secret", docs)
        await repair(tmp_path, ledger, client, "secret", docs)
        await repair(tmp_path, ledger, client, "secret", docs)
    result = read(tmp_path / "documents/d1.json")
    assert len(seen) == 5
    assert result["errors"] == {}
    assert result["context_identical_to_source_repair"]
    assert set(result["arms"]) == {"baseline", "coverage", "source_repair", "context_repair"}
