"""Offline retrieval pilot: original evidence only; no accepted KG mutations."""

import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from itertools import combinations

import numpy as np

STOP = set(
    "a an the and or of to in on at by for from with as is are was were be been "
    "it its this that these those their they them we our your you has have had "
    "can may will would should which what who when how does do not no all some "
    "also then than but if so most actually".split()
)
PREDICATES = ("is_a", "uses", "produces", "measures")


def normalized(value):
    return re.sub(r"\s+", " ", value).strip().casefold()


def terms(text):
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", text.casefold())
    result = []
    for word in words:
        if word in STOP or len(word) < 3:
            continue
        if len(word) > 5 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        result.append(word)
    return result


def load_corpus(parent, config):
    result = []
    for row in config["chunks"]:
        if "blocks" in row:
            spans = [parent.spans[i] for i in row["blocks"]]
            result.append(
                {
                    "id": row["id"],
                    "document": "public-pdf",
                    "text": "\n".join(s.quote for s in spans),
                    "blocks": row["blocks"],
                    "kind": "public",
                    "source": {
                        "org_id": str(parent.org_id),
                        "kg_id": str(parent.kg_id),
                        "material_id": str(parent.material_id),
                        "version_id": str(parent.version_id),
                    },
                    "spans": [s.model_dump(mode="json") for s in spans],
                }
            )
        else:
            result.append(
                {
                    "id": row["id"],
                    "document": row["document"],
                    "text": row["text"],
                    "kind": "synthetic",
                }
            )
    if len({row["id"] for row in result}) != len(result):
        raise ValueError("Duplicate corpus IDs")
    if any(len(row["text"]) > config["context_chars"] for row in result):
        raise ValueError("A complete original chunk exceeds context budget")
    return result


def lexical_rank(chunks, query):
    documents = [Counter(terms(row["text"])) for row in chunks]
    df = Counter(token for doc in documents for token in doc)
    lengths = [sum(doc.values()) for doc in documents]
    average = sum(lengths) / max(1, len(documents)) or 1
    scores = {}
    for row, doc, length in zip(chunks, documents, lengths, strict=True):
        value = 0.0
        for token in set(terms(query)):
            freq = doc[token]
            idf = math.log(1 + (len(chunks) - df[token] + 0.5) / (df[token] + 0.5))
            value += idf * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * length / average))
        if value > 0:
            scores[row["id"]] = value
    return sorted(scores, key=lambda key: (-scores[key], key))


def rrf(*rankings, weights=None):
    weights = weights or [1.0] * len(rankings)
    scores = defaultdict(float)
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] += weight / (60 + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))


def entity_key(text):
    return re.sub(r"^(the|a|an) ", "", normalized(text))


def build_graph(chunks, facts=()):
    """Source-only lexical/structure hints; semantic augmentation requires review."""
    graph = {row["id"]: {} for row in chunks}

    def link(left, right, weight):
        if left == right:
            return
        graph[left][right] = graph[left].get(right, 0.0) + weight
        graph[right][left] = graph[right].get(left, 0.0) + weight

    inverted = defaultdict(set)
    for row in chunks:
        for token in set(terms(row["text"])):
            inverted[token].add(row["id"])
    for ids in inverted.values():
        if 2 <= len(ids) <= 4:
            weight = math.log((len(chunks) + 1) / len(ids)) ** 2
            for left, right in combinations(sorted(ids), 2):
                link(left, right, weight)
    documents = defaultdict(list)
    for row in chunks:
        documents[row["document"]].append(row)
    for rows in documents.values():
        rows = sorted(rows, key=lambda row: min(row.get("blocks", [0])))
        for left, right in zip(rows, rows[1:], strict=False):
            link(left["id"], right["id"], 0.25)
    entities = defaultdict(set)
    for fact in facts:
        if fact.get("validation") != "agent_reviewed_supported":
            raise ValueError("Unreviewed semantic relation")
        if fact["chunk_id"] not in graph:
            raise ValueError("Relation outside this corpus")
        if fact["polarity"] == "positive":
            for value in (fact["subject"], fact["target"]):
                entities[entity_key(value)].add(fact["chunk_id"])
    for ids in entities.values():
        for left, right in combinations(sorted(ids), 2):
            link(left, right, 8.0)
    return graph


def diffuse(graph, seeds, *, steps=2):
    if type(steps) is not int or not 0 <= steps <= 2 or not set(seeds) <= graph.keys():
        raise ValueError("Invalid bounded graph expansion")
    total = sum(seeds.values()) or 1
    seeds = {key: value / total for key, value in seeds.items()}
    mass = dict(seeds)
    for _ in range(steps):
        updated = defaultdict(float, {key: 0.6 * value for key, value in seeds.items()})
        for left, value in mass.items():
            neighbours = graph[left]
            denominator = sum(neighbours.values())
            if not denominator:
                updated[left] += 0.4 * value
            else:
                for right, weight in neighbours.items():
                    updated[right] += 0.4 * value * weight / denominator
        mass = dict(updated)
    return mass


def fit_context(ranking, chunks, *, top_k, max_chars):
    texts = {row["id"]: row["text"] for row in chunks}
    selected, used = [], 0
    for key in dict.fromkeys(ranking):
        if used + len(texts[key]) <= max_chars:
            selected.append(key)
            used += len(texts[key])
        if len(selected) == top_k:
            break
    return selected


def retrieve(chunks, embeddings, query_vector, query, arm, graph, *, top_k=3, max_chars=1800):
    matrix, vector = np.asarray(embeddings), np.asarray(query_vector)
    scores = matrix @ vector / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector))
    dense = [chunks[i]["id"] for i in np.argsort(-scores, kind="stable")]
    baseline = rrf(dense, lexical_rank(chunks, query))
    if arm == "A":
        ranking = baseline
    elif arm in ("B", "C"):
        mass = diffuse(graph, {key: 1 / rank for rank, key in enumerate(baseline[:3], 1)})
        expanded = sorted(mass, key=lambda key: (-mass[key], key))
        ranking = rrf(baseline, expanded, weights=[0.55, 0.45])
    else:
        raise ValueError("Unknown comparison arm")
    return fit_context(ranking, chunks, top_k=top_k, max_chars=max_chars)


def ground_fact(chunk, fact):
    if set(fact) != {"subject", "predicate", "target", "polarity", "quote"}:
        raise ValueError("Invalid fact shape")
    if fact["predicate"] not in PREDICATES or fact["polarity"] not in ("positive", "negative"):
        raise ValueError("Invalid predicate/polarity")
    if any(not isinstance(v, str) or not v.strip() or len(v) > 1800 for v in fact.values()):
        raise ValueError("Invalid fact fields")
    source, quote = normalized(chunk["text"]), normalized(fact["quote"])
    if quote not in source or any(normalized(fact[k]) not in quote for k in ("subject", "target")):
        raise ValueError("Fact quote/endpoints do not correspond to source")
    value = {"chunk_id": chunk["id"], **fact}
    value["id"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return {**value, "validation": "span_only_pending_review"}


def assess(question, selected, answer):
    expected = set(question["expected"])
    retrieved = set(selected)
    answer = answer or {}
    text = normalized(str(answer.get("answer", "")))
    refs = answer.get("citations", [])
    valid = (
        isinstance(refs, list)
        and all(isinstance(v, str) for v in refs)
        and len(refs) == len(set(refs))
        and set(refs) <= retrieved
        and bool(answer)
        and isinstance(answer.get("answer"), str)
    )

    def contains(value):
        return bool(re.search(r"(?<!\w)" + re.escape(normalized(value)) + r"(?!\w)", text))

    match = (
        bool(text)
        and all(any(contains(v) for v in group) for group in question["answers"])
        and not any(contains(v) for v in question["forbidden"])
    )
    complete = expected <= retrieved if expected else None
    supported = (
        bool(match and valid and complete and expected <= set(refs))
        if expected
        else bool(match and valid and not refs)
    )
    return {
        "recall": len(expected & retrieved) / len(expected) if expected else None,
        "complete_support": complete,
        "answer_match": bool(match),
        "citation_valid": bool(valid),
        "supported_correct": supported,
    }


def summarize(records):
    groups = defaultdict(list)
    for row in records:
        groups[row["arm"] + "/" + row["split"]].append(row)
        groups[row["arm"] + "/all"].append(row)
    result = {}
    for key, rows in groups.items():
        summary = {"queries": len(rows), "failed_answers": sum(bool(r.get("error")) for r in rows)}
        for metric in (
            "recall",
            "complete_support",
            "answer_match",
            "supported_correct",
            "citation_valid",
        ):
            values = [r["metrics"][metric] for r in rows if r["metrics"][metric] is not None]
            summary[metric] = sum(values) / len(values) if values else None
        result[key] = summary
    return result
