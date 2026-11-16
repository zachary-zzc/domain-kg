"""Frozen API matrix: semantic retrieval and answer quality on shared evidence budgets."""

import json
from collections import Counter
from typing import Annotated, Literal

from pydantic import Field

from eval import kg_quality_bbio as bbio
from eval.dual_path_data import json_object
from eval.kg_quality_api import API_BASE, JUDGE, WORK_MODELS, chat_request
from eval.scale_retrieval import RELAXED
from eval.scale_retrieval import parse_reader as legacy_reader
from eval.scale_retrieval import reader_request as shared_reader_request
from eval.source_role_eval import Strict, digest

VERSION = "kg-quality-v7-independent-metrics"
ARMS = ("vector_rag", "domain_kg")
KS = (1, 3, 5, 10, 12)
PROTOCOL = {
    "version": VERSION,
    "arms": ARMS,
    "working_models": WORK_MODELS,
    "inference": "remote_api_only",
    "api_endpoint": API_BASE,
    "remote_ollama": {
        "host": bbio.HOST,
        "tunnel_endpoint": bbio.BASE,
        "models": bbio.MODELS,
        "options": bbio.OPTIONS,
        "deadline_seconds": bbio.DEADLINE,
        "inference": "bbio CPU; localhost endpoint is SSH forwarding only",
    },
    "local_model_inference": False,
    "generative_retrieval": None,
    "judge": JUDGE,
    "embedding": "text-embedding-v4",
    "dimensions": 1024,
    "top_k": 12,
    "max_content_chars": 14000,
    "retrieval_k": KS,
    "primary_retrieval": "evidence_sufficient",
    "primary_qa": "grounded_correct",
    "split": "development",
    "unknown_policy": "coverage, known-only mean and all-planned bounds; never impute zero",
    "scope": "offline production retrieval core; no accepted publication or DB latency claim",
}
Text = Annotated[str, Field(min_length=1, max_length=4000)]


class QuestionReview(Strict):
    id: Text
    original_sha256: Text
    question: Text
    decision: Literal["pending", "include", "exclude"]
    track: Literal["multi_hop", "control"]
    reviewer: Text
    reason: Text
    answers: list[list[Text]] | None = None
    support: list[Text] | None = None
    requirements: Annotated[list[Text], Field(max_length=12)] = []


class SourceAssessment(Strict):
    id: Text
    relevance: Literal["useful", "partial", "irrelevant", "uncertain"]


class RequirementAssessment(Strict):
    id: Text
    coverage: Literal["covered", "partial", "missing", "uncertain"]


class ClaimAssessment(Strict):
    claim: Text
    grounding: Literal["supported", "unsupported", "uncertain"]


class EvidenceAssessment(Strict):
    answer: Annotated[str, Field(max_length=2400)]
    citations: Annotated[list[str], Field(max_length=12)]
    sources: Annotated[list[SourceAssessment], Field(max_length=12)]
    reason: Text | None = None
    requirements: Annotated[list[RequirementAssessment], Field(max_length=12)]


class AnswerAssessment(Strict):
    correctness: Literal["correct", "partial", "incorrect", "abstained", "uncertain"]
    citations: Literal["supported", "partial", "unsupported", "none", "uncertain"]
    reference_status: Literal["consistent", "alternative_supported", "defective", "uncertain"]
    path_status: Literal[
        "none", "faithful_relevant", "faithful_irrelevant", "unsupported", "mixed", "uncertain"
    ]
    reason: Text
    requirements: Annotated[list[RequirementAssessment], Field(max_length=12)]
    claims: Annotated[list[ClaimAssessment], Field(max_length=32)]


class CorrectnessAssessment(Strict):
    correctness: Literal["correct", "partial", "incorrect", "abstained", "uncertain"]
    reference_status: Literal["consistent", "alternative_supported", "defective", "uncertain"]
    requirements: Annotated[list[RequirementAssessment], Field(max_length=12)]
    reason: Text


class SupportAssessment(Strict):
    citations: Literal["supported", "partial", "unsupported", "none", "uncertain"]
    path_status: Literal[
        "none", "faithful_relevant", "faithful_irrelevant", "unsupported", "mixed", "uncertain"
    ]
    claims: Annotated[list[ClaimAssessment | Text], Field(max_length=32)]
    reason: Text | None = None


def validate_corpus(docs):
    by_id, units = {}, {}
    for doc in docs:
        if doc["id"] in by_id:
            raise ValueError("duplicate document")
        if not all(isinstance(doc[k], str) for k in ("id", "dataset", "title", "text")):
            raise ValueError("invalid document text")
        by_id[doc["id"]] = doc
        for unit in doc["units"]:
            if unit["id"] in units or not unit["text"] or unit["text"] not in doc["text"]:
                raise ValueError("duplicate unit or invalid source unit")
            units[unit["id"]] = (doc["id"], unit["text"])
    return by_id, units


def review_template(cases):
    return [
        {
            "id": c["id"],
            "original_sha256": digest(c),
            "question": c["question"],
            "decision": "pending",
            "track": "multi_hop" if c["dataset"] in ("hotpot", "musique") else "control",
            "reviewer": "UNREVIEWED",
            "reason": "Review scope, premise, answer and all supporting evidence before inclusion.",
            "requirements": [],
        }
        for c in cases
    ]


def reviewed_cases(cases, docs, reviews):
    by_doc, units = validate_corpus(docs)
    original = {c["id"]: c for c in cases}
    parsed = [QuestionReview.model_validate(r) for r in reviews]
    if len(original) != len(cases) or len({r.id for r in parsed}) != len(parsed):
        raise ValueError("duplicate question/review")
    if {r.id for r in parsed} != set(original):
        raise ValueError("review every question, including exclusions")
    result = []
    for r in parsed:
        c = original[r.id]
        if r.original_sha256 != digest(c):
            raise ValueError("original question/annotation changed")
        if r.decision == "pending" or r.reviewer == "UNREVIEWED":
            raise ValueError("pending question review")
        if r.decision == "exclude":
            continue
        if not r.requirements or len(set(r.requirements)) != len(r.requirements):
            raise ValueError("included question needs reviewed question-only requirements")
        answers = c["answers"] if r.answers is None else r.answers
        support = c["support"] if r.support is None else r.support
        if not c["answerable"] or not answers or any(not a for a in answers) or not support:
            raise ValueError("v2 primary track requires answerable, supported questions")
        if (
            not set(support) <= set(units)
            or not set(c["documents"]) <= set(by_doc)
            or len(support) != len(set(support))
        ):
            raise ValueError("question references missing source")
        if any(by_doc[units[u][0]]["dataset"] != c["dataset"] for u in support):
            raise ValueError("cross-dataset question support")
        result.append(
            {
                **c,
                "original_question": c["question"],
                "original_answers": c["answers"],
                "original_support": c["support"],
                "answers": answers,
                "support": support,
                "requirements": [
                    {"id": f"r{i + 1}", "text": text} for i, text in enumerate(r.requirements)
                ],
                "question": r.question,
                "review": r.model_dump(),
                "track": r.track,
                "evaluation_split": "development",
            }
        )
    if not result:
        raise ValueError("no included questions")
    return result


def rank_bundle(cases, docs, ranks):
    return {
        "corpus_sha256": digest(docs),
        "model": PROTOCOL["embedding"],
        "dimensions": PROTOCOL["dimensions"],
        "rows": [
            {"id": c["id"], "query_sha256": digest(c["question"]), "documents": rank}
            for c, rank in zip(cases, ranks, strict=True)
        ],
    }


def validate_ranks(bundle, cases, docs):
    if bundle["corpus_sha256"] != digest(docs):
        raise ValueError("ranking corpus changed")
    if (bundle["model"], bundle["dimensions"]) != (PROTOCOL["embedding"], PROTOCOL["dimensions"]):
        raise ValueError("ranking embedding profile changed")
    rows = {r["id"]: r for r in bundle["rows"]}
    if len(rows) != len(bundle["rows"]) or set(rows) != {c["id"] for c in cases}:
        raise ValueError("duplicate or missing ranking row")
    allowed = {d["id"] for d in docs}
    for c in cases:
        row = rows[c["id"]]
        if row["query_sha256"] != digest(c["question"]):
            raise ValueError("query changed; regenerate embeddings")
        ids = row["documents"]
        if len(set(ids)) != len(ids) or not set(ids) <= allowed or not ids:
            raise ValueError("duplicate/unknown/empty ranked document")
    return {k: v["documents"] for k, v in rows.items()}


def reader_request(packet):
    base = shared_reader_request(packet)
    return chat_request(
        packet["model"],
        base["messages"][0]["content"]
        + " Before answering, follow the source-supported relationships to identify the "
        "requested target. Keep each attribute attached to its subject; check relation "
        "direction and temporal order. Return the kind of answer requested, including a "
        "number for a count or the selected item for a comparison. Cite passages for both "
        "the necessary bridges and final fact. If a necessary link is absent, abstain "
        "with an empty answer and citations; never combine a substantive answer with abstain.",
        json_object(base["messages"][1]["content"]),
        2048,
    )


def evidence_request(packet, *, judge=JUDGE):
    return chat_request(
        judge,
        RELAXED + "Evaluate retrieval using ONLY the question and original candidate passages. "
        "No reader answer or reference annotation is supplied. Sufficient means the "
        "passages together support a complete answer, including necessary bridges. "
        "Partial means useful but missing necessary facts; insufficient means no "
        "material evidence; uncertain means you cannot determine the status. "
        "Useful intermediate bridge evidence counts as partial even if no final answer "
        "can yet be derived. Do not label evidence insufficient when a source is useful. "
        "Give your independently derived answer and cite its sources. Evaluate each "
        "source as useful to answering/bridging, irrelevant, or uncertain. Alternate "
        "evidence is valid. For every question requirement, judge whether its needed "
        "information is covered, partial, missing, or uncertain. Accept any valid alternative "
        "reasoning chain; requirements specify requested meaning, not a prescribed source path. "
        "Do not require the reader to spell out intermediate steps. Do not output a separate "
        "sufficiency label: the evaluator derives it from source usefulness and requirement "
        "coverage. Return ONLY JSON: "
        '{"answer":"supported answer or empty","citations":["source ID"],'
        '"sources":[{"id":"each source exactly once",'
        '"relevance":"useful|irrelevant|uncertain"}],'
        '"requirements":[{"id":"each requirement ID exactly once",'
        '"coverage":"covered|partial|missing|uncertain"}],"reason":"brief explanation"}.',
        {
            "question": packet["question"],
            "requirements": packet["requirements"],
            "sources": packet["evidence"]["sources"],
        },
        4096,
    )


def parse_evidence(raw, packet):
    value = EvidenceAssessment.model_validate(json_object(raw)).model_dump()
    # A source can supply only a bridge and still be useful. Source precision
    # measures contribution, not whether each individual source answers everything.
    for source in value["sources"]:
        if source["relevance"] == "partial":
            source["relevance"] = "useful"
    ids = {s["id"] for s in packet["evidence"]["sources"]}
    rated = [s["id"] for s in value["sources"]]
    useful = {s["id"] for s in value["sources"] if s["relevance"] == "useful"}
    cites = value["citations"]
    validate_requirements(value["requirements"], packet)
    statuses = [r["coverage"] for r in value["requirements"]]
    if statuses and all(s == "covered" for s in statuses):
        sufficiency = "sufficient"
    elif useful and ("missing" in statuses or "partial" in statuses):
        sufficiency = "partial"
    elif "uncertain" in statuses or any(s["relevance"] == "uncertain" for s in value["sources"]):
        sufficiency = "uncertain"
    else:
        sufficiency = "partial" if useful else "insufficient"
    value["sufficiency"] = sufficiency
    if (
        not set(rated) <= ids
        or len(set(rated)) != len(rated)
        or not set(cites) <= ids
        or len(set(cites)) != len(cites)
        or (value["sufficiency"] == "sufficient" and (not cites or not value["answer"].strip()))
        or (value["sufficiency"] == "sufficient" and not set(cites) <= useful)
    ):
        raise ValueError("invalid evidence assessment coverage/citations")
    missing = ids - set(rated)
    if missing:
        # A missing usefulness label is unknown, never implicitly irrelevant.
        # It must not erase independent requirement coverage and cited support.
        value["sources"].extend({"id": key, "relevance": "uncertain"} for key in sorted(missing))
        value["protocol_warnings"] = ["source_ratings_incomplete"]
    return value


def parse_reader(raw, ids):
    value = legacy_reader(raw, ids)
    answer = value["answer"]
    spans = [answer] if isinstance(answer, str) else answer
    if value["abstain"]:
        consistent = answer == "" and value["citations"] == []
    else:
        consistent = (
            bool(spans) and len(spans) <= 8 and all(s.strip() and len(s) <= 2400 for s in spans)
        )
    value["protocol_valid"] = bool(value["protocol_valid"] and consistent)
    return value


def qa_request(packet, reader, *, judge=JUDGE):
    return chat_request(
        judge,
        RELAXED + "Assess the reader answer on THREE separate axes: semantic correctness "
        "for the question; support by candidate original passages; and support by "
        "the reader's cited passages (including bridges). The reference is a fallible "
        "assessment aid, NOT evidence the reader saw. A correct guess can be "
        "unsupported. Accept aliases, lists vs joined strings, paraphrases, unit/date "
        "equivalence and supported alternatives. Never mark content incorrect solely "
        "for formatting/citation failure. An abstaining reader has correctness "
        "abstained, empty claims and citations none; abstention is not an answered "
        "question. Use uncertain for unresolved cases. Check path fidelity against "
        "original sources separately. path_status refers ONLY to the supplied "
        "candidate_evidence.paths array, not to your reasoning or arithmetic: use none "
        "exactly when that array is empty. For every question requirement assess how completely "
        "the reader correctly answered that requested aspect; do not demand an explanation "
        "when a short answer suffices. List every distinct factual claim in the reader answer, "
        "with its support by candidate passages. Do not add reference facts to this claim list. "
        "Empty claims for an empty abstention. Judge each claim against ALL candidate original "
        "passages, regardless of which ones the reader cited. Do not output an overall "
        "grounding label: the evaluator derives it from these claim labels. Judge citations "
        "against the cited subset separately. Return ONLY JSON, all fields required: "
        '{"correctness":"correct|partial|incorrect|abstained|uncertain",'
        '"citations":"supported|partial|unsupported|none|uncertain",'
        '"reference_status":"consistent|alternative_supported|defective|uncertain",'
        '"path_status":"none|faithful_relevant|faithful_irrelevant|unsupported|mixed|'
        'uncertain","requirements":[{"id":"each requirement ID exactly once",'
        '"coverage":"covered|partial|missing|uncertain"}],'
        '"claims":[{"claim":"reader factual claim",'
        '"grounding":"supported|unsupported|uncertain"}],'
        '"reason":"brief source-based explanation"}.',
        {
            "question": packet["question"],
            "requirements": packet["requirements"],
            "candidate_evidence": packet["evidence"],
            "reader": reader,
            "reference_answers": packet["answers"],
            "reference_sources": packet["reference_sources"],
        },
        4096,
    )


def grounding_request(packet, reader, *, judge=JUDGE):
    return chat_request(
        judge,
        RELAXED + "Assess ONLY support for the reader's factual claims by the candidate "
        "original passages. You have no reference answer or gold passages. A fact can be "
        "true but unsupported here; do not use outside knowledge as evidence. List each "
        "distinct factual claim asserted in reader.answer only. Citation metadata is not "
        "an additional answer claim: never add a claim that a citation supports the answer "
        "or a claim about your evaluation. Assess citation errors exclusively in citations. "
        "Judge the actual answer claims against ALL supplied "
        "candidate passages, regardless of which ones were cited. Judge citation support "
        "against the reader's cited subset separately. Claims not established by these "
        "passages are unsupported, even if familiar or plausible. Use uncertain when "
        "support cannot be resolved. An empty abstention has empty claims and citations "
        "none. Check the supplied graph paths against their original sources separately; "
        "path_status is none exactly when candidate_evidence.paths is empty. Return ONLY "
        'JSON {"citations":"supported|partial|unsupported|none|uncertain",'
        '"path_status":"none|faithful_relevant|faithful_irrelevant|unsupported|mixed|uncertain",'
        '"claims":[{"claim":"reader factual claim",'
        '"grounding":"supported|unsupported|uncertain"}],"reason":"brief source-based reason"}.',
        {
            "question": packet["question"],
            "candidate_evidence": packet["evidence"],
            "reader": reader,
        },
        4096,
    )


def correctness_request(packet, reader, *, judge=JUDGE):
    return chat_request(
        judge,
        RELAXED + "Assess ONLY whether the reader correctly answers the question and how "
        "completely it correctly answers each requirement. Reference answers/sources are "
        "fallible aids: accept supported equivalent or alternative answers. Do not mark "
        "content incorrect solely for formatting or citation failure. Do not assess "
        "grounding or citation support in this request; a separate reference-free assessor "
        "does that. Do not require explanations when a short answer suffices. A reader "
        "with an empty valid abstention has correctness abstained and all requirements "
        "missing. Use uncertain for unresolved cases. Return ONLY JSON "
        '{"correctness":"correct|partial|incorrect|abstained|uncertain",'
        '"reference_status":"consistent|alternative_supported|defective|uncertain",'
        '"requirements":[{"id":"each requirement ID exactly once",'
        '"coverage":"covered|partial|missing|uncertain"}],"reason":"brief factual reason"}.',
        {
            "question": packet["question"],
            "requirements": packet["requirements"],
            "candidate_evidence": packet["evidence"],
            "reader": reader,
            "reference_answers": packet["answers"],
            "reference_sources": packet["reference_sources"],
        },
        4096,
    )


def parse_correctness(raw, packet, reader):
    value = CorrectnessAssessment.model_validate(json_object(raw)).model_dump()
    validate_requirements(value["requirements"], packet)
    abstained = reader["abstain"] is True and reader["protocol_valid"]
    if (
        (value["correctness"] == "abstained") != abstained
        or (abstained and any(r["coverage"] != "missing" for r in value["requirements"]))
        or (
            value["correctness"] == "correct"
            and any(r["coverage"] != "covered" for r in value["requirements"])
        )
    ):
        raise ValueError("correctness assessment contradicts reader/requirements")
    return value


def parse_support(raw, packet, reader):
    value = SupportAssessment.model_validate(json_object(raw)).model_dump()
    if any(isinstance(claim, str) for claim in value["claims"]):
        value["claims"] = [
            {"claim": claim, "grounding": "uncertain"} if isinstance(claim, str) else claim
            for claim in value["claims"]
        ]
        value["protocol_warnings"] = ["claim_grounding_missing"]
    abstained = reader["abstain"] is True and reader["protocol_valid"]
    claim_labels = {c["grounding"] for c in value["claims"]}
    if not claim_labels:
        value["grounding"] = "none"
    elif claim_labels == {"supported"}:
        value["grounding"] = "supported"
    elif claim_labels == {"unsupported"}:
        value["grounding"] = "unsupported"
    elif "unsupported" in claim_labels:
        value["grounding"] = "partial"
    else:
        value["grounding"] = "uncertain"
    if (
        (abstained and (value["claims"] or value["citations"] != "none"))
        or (not abstained and reader["answer"] and not value["claims"])
        or (
            value["citations"] == "supported"
            and (not reader["citations"] or reader["invalid_citations"])
        )
    ):
        raise ValueError("support assessment contradicts reader state")
    if (value["path_status"] == "none") == bool(packet["evidence"]["paths"]):
        value["path_status"] = "uncertain"
        value["path_error"] = "inconsistent_path_status"
    return value


def parse_separate_qa(correctness_raw, support_raw, packet, reader):
    correct = parse_correctness(correctness_raw, packet, reader)
    support = parse_support(support_raw, packet, reader)
    return {**support, **correct, "support_reason": support["reason"]}


def parse_qa(raw, packet, reader):
    value = AnswerAssessment.model_validate(json_object(raw)).model_dump()
    correct = {k: value[k] for k in CorrectnessAssessment.model_fields}
    support = {k: value[k] for k in SupportAssessment.model_fields}
    return parse_separate_qa(json.dumps(correct), json.dumps(support), packet, reader)


def review_request(request):
    """One independent second judgment, with exactly the original information boundary."""
    messages = [dict(m) for m in request["messages"]]
    messages[0]["content"] += (
        " Independently verify this assessment before returning labels. Work through the "
        "question's target and the source-supported reasoning chain, including required "
        "bridges. Check comparisons, direction and requested scope. Ensure each label "
        "agrees with your factual explanation and each cited source's usefulness. "
        "Use uncertain if unresolved. No other assessor's answer is supplied."
    )
    return {**request, "messages": messages}


def reviewed_assessment(first, second):
    """Keep only metric conclusions corroborated by the bounded independent review.

    Compare semantic labels/derived scores, never answer or claim wording. Preserve
    both raw judgments in the request store and disagreement details in scored rows.
    """
    if first is None or second is None:
        return None
    evidence = "sources" in first
    left = score(None, first if evidence else None, None if evidence else first)
    right = score(None, second if evidence else None, None if evidence else second)
    disagreements = {k for k in left if left[k] != right[k]}
    component_unknowns = []
    if first.get("grounding") != second.get("grounding"):
        disagreements.add("grounded_correct")
        component_unknowns.append("grounding")
    for field, metrics in (
        (
            "requirements",
            ("semantic_coverage", "evidence_sufficient")
            if evidence
            else ("answer_completeness", "semantic_correct", "grounded_correct"),
        ),
        ("sources", ("semantic_source_precision",)),
    ):
        a = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in first.get(field, [])}
        b = {r["id"]: {k: v for k, v in r.items() if k != "id"} for r in second.get(field, [])}
        if a != b:
            disagreements.update(metrics)
    return {
        **first,
        "review_disagreements": sorted(disagreements),
        "metric_unknowns": sorted(disagreements),
        "component_unknowns": component_unknowns,
    }


def validate_requirements(assessments, packet):
    ids = [r["id"] for r in assessments]
    if set(ids) != {r["id"] for r in packet["requirements"]} or len(ids) != len(set(ids)):
        raise ValueError("incomplete or duplicate semantic requirement assessment")


def coverage(assessment):
    values = (
        [
            {"covered": 1, "partial": 0.5, "missing": 0, "uncertain": None}[r["coverage"]]
            for r in assessment.get("requirements", [])
        ]
        if assessment
        else []
    )
    return sum(values) / len(values) if values and None not in values else None


def score(reader, evidence, qa):
    def known(value, target):
        return None if value is None or value == "uncertain" else int(value == target)

    sufficiency = evidence["sufficiency"] if evidence else None
    correctness = qa.get("correctness") if qa else None
    grounding = qa.get("grounding") if qa else None
    citation = qa.get("citations") if qa else None
    components = [known(correctness, "correct"), known(grounding, "supported")]
    grounded = 0 if 0 in components else None if None in components else 1
    operational = None if reader is None else 0 if not reader["protocol_valid"] else grounded
    relevance = [known(s["relevance"], "useful") for s in evidence["sources"]] if evidence else []
    claims = [known(c["grounding"], "unsupported") for c in qa.get("claims", [])] if qa else []
    values = {
        "evidence_sufficient": known(sufficiency, "sufficient"),
        "semantic_coverage": coverage(evidence),
        "semantic_source_precision": sum(relevance) / len(relevance)
        if relevance and None not in relevance
        else None,
        "semantic_correct": known(correctness, "correct"),
        "answer_completeness": coverage(qa),
        "unsupported_claim_rate": sum(claims) / len(claims)
        if claims and None not in claims
        else None,
        "grounded_correct": grounded,
        "citation_supported": known(citation, "supported"),
        "operational_success": operational,
        "reader_protocol_valid": int(reader["protocol_valid"]) if reader is not None else None,
        "abstained": int(reader["abstain"] is True) if reader else None,
    }

    for assessment in (evidence, qa):
        for metric in (assessment or {}).get("metric_unknowns", []):
            values[metric] = None
    # Grounded correctness is a conjunction of independent, possibly unknown
    # judgments. A definite failure of either axis remains a failure.
    grounded_support = (
        None
        if qa and "grounding" in qa.get("component_unknowns", [])
        else known(grounding, "supported")
    )
    components = [values["semantic_correct"], grounded_support]
    values["grounded_correct"] = 0 if 0 in components else None if None in components else 1
    values["operational_success"] = (
        None if reader is None else values["grounded_correct"] if reader["protocol_valid"] else 0
    )
    return values


def metric_summary(values):
    known = [v for v in values if v is not None]
    n, count = len(values), len(known)
    return {
        "planned": n,
        "known": count,
        "unknown": n - count,
        "mean_known": sum(known) / count if count else None,
        "lower_bound": sum(known) / n if n else None,
        "upper_bound": (sum(known) + n - count) / n if n else None,
    }


def paired(rows, ids, left, right, metric):
    lookup = {}
    for row in rows:
        key = (row["id"], row["arm"])
        if key in lookup:
            raise ValueError("duplicate result")
        lookup[key] = row["scores"].get(metric)
    deltas, lower, upper = [], [], []
    for ident in ids:
        a, b = lookup.get((ident, left)), lookup.get((ident, right))
        if a is not None and b is not None:
            deltas.append(b - a)
        lower.append((b if b is not None else 0) - (a if a is not None else 1))
        upper.append((b if b is not None else 1) - (a if a is not None else 0))
    return {
        "planned_pairs": len(ids),
        "known_pairs": len(deltas),
        "unknown_pairs": len(ids) - len(deltas),
        "wins": sum(d < 0 if metric == "unsupported_claim_rate" else d > 0 for d in deltas),
        "losses": sum(d > 0 if metric == "unsupported_claim_rate" else d < 0 for d in deltas),
        "ties": sum(d == 0 for d in deltas),
        "paired_delta": sum(deltas) / len(deltas) if deltas else None,
        "delta_bounds_all_planned": [sum(lower) / len(ids), sum(upper) / len(ids)]
        if ids
        else [None, None],
    }


RETRIEVAL_METRICS = ("evidence_sufficient", "semantic_coverage", "semantic_source_precision")


def summarize(cases, rows, *, models=WORK_MODELS):
    ids = {c["id"] for c in cases}
    if len(ids) != len(cases) or any(
        r["id"] not in ids or r["arm"] not in ARMS or r["model"] not in WORK_MODELS for r in rows
    ):
        raise ValueError("unplanned or duplicate question/result")
    lookup = {(r["model"], r["id"], r["arm"]): r for r in rows}
    if len(lookup) != len(rows):
        raise ValueError("duplicate result")
    groups = {"all": cases}
    for field in ("dataset", "track", "difficulty"):
        for value in sorted({c[field] for c in cases}):
            groups[field + ":" + value] = [c for c in cases if c[field] == value]
    selected = tuple(models)
    models = {}
    metrics = list(score(None, None, None))
    for model in selected:
        results = {}
        model_rows = [r for r in rows if r["model"] == model]
        for group, members in groups.items():
            members = [c["id"] for c in members]
            arms = {}
            for arm in ARMS:
                selected = [lookup.get((model, ident, arm), {}) for ident in members]
                arms[arm] = {
                    m: metric_summary([r.get("scores", {}).get(m) for r in selected])
                    for m in metrics
                }
                arms[arm]["errors"] = dict(
                    Counter(e for r in selected for e in r.get("errors", []))
                )
                arms[arm]["retrieval_at_k"] = {
                    str(k): {
                        m: metric_summary(
                            [r.get("retrieval_at_k", {}).get(str(k), {}).get(m) for r in selected]
                        )
                        for m in RETRIEVAL_METRICS
                    }
                    for k in KS
                }
            results[group] = {
                "arms": arms,
                "paired": {
                    m: paired(model_rows, members, "vector_rag", "domain_kg", m) for m in metrics
                },
            }
        models[model] = {"groups": results}
    return {"version": VERSION, "semantic_status": "provisional_model_judgments", "models": models}
