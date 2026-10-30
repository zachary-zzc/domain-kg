"""Offline diagnostics and predeclared source-only retrieval contrasts.

These are benchmark experiments, not serving branches or new answer scores.
Frozen graph facts, source IDs and evidence are never modified.
"""

import math
from collections import Counter, defaultdict, deque

from eval.dual_path_retrieval import pack, source_units, tokens


def fill_context(retained, vector_rank, documents, budget=12000):
    if any(d not in documents for d in [*retained, *vector_rank]):
        raise ValueError("source outside corpus")
    result = list(retained)
    for ident in vector_rank:
        if len(result) >= 12:
            break
        result = pack([ident], documents, budget=budget, retained=result)
    return result


def pool_context(vector_rank, graph_rank, documents, budget=12000):
    if any(d not in documents for d in [*vector_rank, *graph_rank]):
        raise ValueError("source outside corpus")
    retained = pack(vector_rank[:6], documents, budget=budget)
    scores = defaultdict(float)
    for branch in (vector_rank[6:], graph_rank[:24]):
        for rank, ident in enumerate(dict.fromkeys(branch), 1):
            scores[ident] += 1 / (60 + rank)
    ordered = sorted(scores, key=lambda d: (-scores[d], d))
    return fill_context(retained, ordered, documents, budget)


def trace_graph(index, query):
    """Instrument the frozen GraphIndex exactly, including insertion/cap effects."""
    q = set(tokens(query))
    candidates = set().union(*(index.postings[t] for t in q)) if q else set()

    def weight(name):
        words = set(tokens(name))
        score = sum(math.log(1 + len(index.labels) / (1 + index.df[t])) for t in sorted(q & words))
        return score / max(1, len(words)) ** 0.5 + (8 if words <= q else 0)

    seeds = sorted(candidates, key=lambda n: (-weight(n), n))[:4]
    queue = deque((n, 0, weight(n)) for n in seeds)
    seen, scores, edge_count = set(seeds), defaultdict(float), 0
    depths, encountered = {}, []
    while queue and len(seen) <= 200 and edge_count < 400:
        node, depth, score = queue.popleft()
        if depth >= 3:
            continue
        for other, i in index.edges[node]:
            edge_count += 1
            if edge_count > 400:
                break
            ident = index.facts[i]["d"]
            scores[ident] = max(scores[ident], score / (depth + 1))
            depths[ident] = min(depths.get(ident, 99), depth)
            encountered.append(i)
            if other not in seen and len(seen) < 200:
                seen.add(other)
                queue.append((other, depth + 1, score * 0.8))
    ordered = sorted(scores, key=lambda d: (-scores[d], d))
    sixth_score = scores[ordered[min(5, len(ordered) - 1)]] if ordered else None
    return {
        "rank": ordered,
        "scores": dict(scores),
        "depths": depths,
        "seeds": [{"label": n, "weight": weight(n), "degree": len(index.edges[n])} for n in seeds],
        "visited_nodes": len(seen),
        "credited_edges": min(edge_count, 400),
        "cap_hit": edge_count >= 400 or len(seen) >= 200,
        "top_score_ties": sum(v == scores[ordered[0]] for v in scores.values()) if ordered else 0,
        "sixth_score_ties": sum(v == sixth_score for v in scores.values()) if ordered else 0,
        "encountered_fact_indices": encountered,
    }


class SourceBM25:
    """Public frozen corpus only; serving use would require eligible-set statistics."""

    def __init__(self, documents):
        self.terms = {
            ident: Counter(tokens(doc["title"] + "\n" + doc["text"]))
            for ident, doc in documents.items()
        }
        self.lengths = {d: sum(words.values()) for d, words in self.terms.items()}
        self.average = sum(self.lengths.values()) / max(1, len(self.lengths))
        df = Counter(t for words in self.terms.values() for t in words)
        self.idf = {t: math.log(1 + (len(documents) - n + 0.5) / (n + 0.5)) for t, n in df.items()}

    def score(self, query, ident):
        words = self.terms[ident]
        norm = 1.2 * (0.25 + 0.75 * self.lengths[ident] / max(self.average, 1))
        return sum(
            self.idf.get(t, 0) * words[t] * 2.2 / (words[t] + norm)
            for t in sorted(set(tokens(query)))
            if words[t]
        )


def tie_rank(trace, index, query):
    return sorted(trace["scores"], key=lambda d: (-trace["scores"][d], -index.score(query, d), d))


def supplied_units(ids, documents):
    return {u["id"] for u in source_units(ids, documents)}
