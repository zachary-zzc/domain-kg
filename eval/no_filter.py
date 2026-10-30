"""Offline retention experiment: graph recall may append, never evict vectors."""

import json

from eval.hybrid_run import answer_request


def contexts(chunks, vector, graph, query):
    texts = {row["id"]: row["text"] for row in chunks}
    if len(texts) != len(chunks) or not set(vector + graph) <= texts.keys():
        raise ValueError("Duplicate or out-of-scope evidence")

    def fits(keys, chars):
        request = answer_request(
            "domain-kg-extraction-7b", query, [{"id": key, "text": texts[key]} for key in keys]
        )
        size = len(json.dumps(request["messages"], ensure_ascii=False).encode())
        return sum(len(texts[key]) for key in keys) <= chars and size + 128 + 256 <= 4096

    def add(keys, ranking, limit, chars):
        selected = list(keys)
        for key in dict.fromkeys(ranking):
            if key not in selected and len(selected) < limit and fits(selected + [key], chars):
                selected.append(key)
        if not selected:
            raise ValueError("No whole evidence fits the request")
        return selected

    v3 = add([], vector, 3, 1800)
    v6 = add([], vector, 6, 2400)
    union = add(v6, graph[:6], 12, 2400)
    return {
        "V3": v3,
        "V6": v6,
        "U": union,
        "omitted_graph": [key for key in dict.fromkeys(graph[:6]) if key not in union],
    }
