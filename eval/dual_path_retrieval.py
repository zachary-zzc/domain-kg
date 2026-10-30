"""Independent lexical graph recall and vector-retaining source fusion.

Equal labels link retrieval hints only. Source-scoped facts remain separate;
the reader receives original passages, never a generated inferred graph fact.
"""

import json
import math
import re
from collections import Counter, defaultdict, deque


def tokens(text):
    return re.findall(r"\w+", text.casefold())


class GraphIndex:
    def __init__(self, facts):
        self.facts = facts
        self.edges = defaultdict(list)
        self.labels = {}
        for i, f in enumerate(facts):
            s, o = " ".join(tokens(f["s"])), " ".join(tokens(f["o"]))
            self.labels[s], self.labels[o] = f["s"], f["o"]
            self.edges[s].append((o, i))
            self.edges[o].append((s, i))
        self.df = Counter(t for name in self.labels for t in set(tokens(name)))
        self.postings = defaultdict(set)
        for name in self.labels:
            for t in set(tokens(name)):
                self.postings[t].add(name)

    def recall(self, query, limit=6):
        q = set(tokens(query))
        candidates = set().union(*(self.postings[t] for t in q)) if q else set()

        def weight(name):
            words = set(tokens(name))
            overlap = q & words
            score = sum(math.log(1 + len(self.labels) / (1 + self.df[t])) for t in overlap)
            return score / max(1, len(words)) ** 0.5 + (8 if words <= q else 0)

        seeds = sorted(candidates, key=lambda n: (-weight(n), n))[:4]
        queue = deque((n, 0, weight(n)) for n in seeds)
        seen, scores, edge_count = set(seeds), defaultdict(float), 0
        while queue and len(seen) <= 200 and edge_count < 400:
            node, depth, score = queue.popleft()
            if depth >= 3:
                continue
            for other, i in self.edges[node]:
                edge_count += 1
                if edge_count > 400:
                    break
                fact = self.facts[i]
                # Original negated sources remain retrievable; this is not inference.
                scores[fact["d"]] = max(scores[fact["d"]], score / (depth + 1))
                if other not in seen and len(seen) < 200:
                    seen.add(other)
                    queue.append((other, depth + 1, score * 0.8))
        return sorted(scores, key=lambda d: (-scores[d], d))[:limit]


def source_units(ids, documents):
    units, seen = [], set()
    for ident in ids:
        for unit in documents[ident]["units"]:
            if unit["id"] not in seen:
                units.append(unit)
                seen.add(unit["id"])
    return units


def pack(ids, documents, budget=12000, retained=()):
    result = list(retained)
    for ident in ids:
        if ident in result:
            continue
        candidate = result + [ident]
        if len(json.dumps(source_units(candidate, documents), ensure_ascii=False)) <= budget:
            result.append(ident)
    return result


def contexts(vector_rank, graph_rank, documents, budget=12000):
    v = pack(vector_rank[:6], documents, budget)
    return {
        "V6": v,
        "V12": pack(vector_rank[:12], documents, budget),
        "G6": pack(graph_rank[:6], documents, budget),
        "U12": pack(graph_rank[:6], documents, budget, retained=v),
    }


def support_scores(case, ids, documents):
    gold = set(case["support"])
    supplied = {u["id"] for u in source_units(ids, documents)}
    return {
        "complete": float(gold <= supplied) if gold else None,
        "recall": len(gold & supplied) / len(gold) if gold else None,
        "documents": len(ids),
        "source_characters": sum(len(u["text"]) for u in source_units(ids, documents)),
    }
