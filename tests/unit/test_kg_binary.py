import json

import pytest

from eval import kg_binary as binary
from eval import kg_quality as quality
from eval.source_role_eval import digest

JUDGE = "qwen3.5-397b-a17b"


def packet(arm="vector_rag"):
    return {
        "id": "q",
        "arm": arm,
        "dataset": "test",
        "model": binary.WORK_MODEL,
        "question": "Where was the author of Blue born?",
        "answers": [["Rome"]],
        "requirements": [{"id": "r1", "text": "Identify the birthplace."}],
        "reference_sources": [{"id": "gold", "text": "REFERENCE-ONLY"}],
        "evidence": {
            "sources": [
                {"id": "s1", "text": "Ada wrote Blue."},
                {"id": "s2", "text": "Ada was born in Rome."},
            ],
            "paths": [],
        },
    }


def reader_results(p):
    # An invalid citation must not prevent semantic evaluation of a valid answer.
    return {
        digest(quality.reader_request(p)): {
            "content": '{"answer":"Rome","citations":["bad-id"],"abstain":false}',
            "error": None,
        }
    }


def test_each_metric_has_one_request_and_inputs_are_isolated():
    p = packet()
    results = reader_results(p)
    retrieval = binary.jobs_for("retrieval", [p], results, JUDGE)
    answer = binary.jobs_for("answer", [p], results, JUDGE)
    assert len(retrieval) == len(answer) == 1
    data = json.loads(retrieval[0]["request"]["messages"][1]["content"])
    assert data == {"question": p["question"], "passages": p["evidence"]["sources"]}
    data = json.loads(answer[0]["request"]["messages"][1]["content"])
    assert data == {
        "question": p["question"],
        "reference_answers": [["Rome"]],
        "candidate_answer": "Rome",
    }
    assert {j["request"]["model"] for j in retrieval + answer} == {JUDGE}
    assert "independently verify" not in json.dumps(retrieval + answer).lower()


def test_answer_accuracy_is_independent_of_retrieval_sufficiency_and_citations():
    p = packet()
    results = reader_results(p)
    results[digest(binary.retrieval_request(p, JUDGE))] = {
        "content": '{"correct":false}',
        "error": None,
    }
    results[digest(binary.answer_request(p, "Rome", JUDGE))] = {
        "content": '{"correct":true}',
        "error": None,
    }
    rows = binary.score_packets([p], results, JUDGE)
    assert rows[0]["errors"] == {}
    metrics = binary.summarize(rows)["vector_rag"]
    assert metrics["retrieval"]["accuracy"] == 0
    assert metrics["answer"]["accuracy"] == 1


@pytest.mark.parametrize("raw", ['{"correct":"true"}', '{"correct":1}', "{}"])
def test_invalid_api_labels_are_failures_not_semantic_wrong_answers(raw):
    request = binary.retrieval_request(packet(), JUDGE)
    result, error = binary.judgment_result(
        request, {digest(request): {"content": raw, "error": None}}
    )
    assert result is None and error == "invalid_judgment_response"


def test_connection_failure_is_not_counted_as_a_wrong_answer_or_a_new_class():
    p = packet()
    results = reader_results(p)
    results[digest(binary.retrieval_request(p, JUDGE))] = {"content": None, "error": "ConnectError"}
    results[digest(binary.answer_request(p, "Rome", JUDGE))] = {
        "content": '{"correct":true,"reason":"Equivalent answer."}',
        "error": None,
    }
    rows = binary.score_packets([p], results, JUDGE)
    metrics = binary.summarize(rows)["vector_rag"]
    assert metrics["retrieval"] == {
        "correct": 0,
        "evaluated": 0,
        "planned": 1,
        "failed": 1,
        "accuracy": None,
    }
    assert metrics["answer"]["accuracy"] == 1


def test_plain_text_reader_answer_is_judged_semantically_despite_missing_json():
    p = packet()
    results = {digest(quality.reader_request(p)): {"content": "Rome", "error": None}}
    jobs = binary.jobs_for("answer", [p], results, JUDGE)
    assert len(jobs) == 1
    assert json.loads(jobs[0]["request"]["messages"][1]["content"])["candidate_answer"] == "Rome"
    assert len(binary.jobs_for("retrieval", [p], results, JUDGE)) == 1


def test_failed_reader_call_does_not_block_retrieval_evaluation():
    p = packet()
    results = {digest(quality.reader_request(p)): {"content": None, "error": "ConnectError"}}
    assert binary.jobs_for("answer", [p], results, JUDGE) == []
    assert len(binary.jobs_for("retrieval", [p], results, JUDGE)) == 1
    assert binary.score_packets([p], results, JUDGE)[0]["errors"]["answer"] == "ConnectError"


def test_incomplete_run_never_uses_a_smaller_accuracy_denominator():
    rows = [
        {"arm": "vector_rag", "retrieval": {"correct": True}, "answer": {"correct": True}},
        {"arm": "vector_rag", "retrieval": None, "answer": None},
    ]
    metrics = binary.summarize(rows)["vector_rag"]
    for metric in ("retrieval", "answer"):
        assert metrics[metric]["planned"] == 2
        assert metrics[metric]["evaluated"] == 1
        assert metrics[metric]["accuracy"] is None
