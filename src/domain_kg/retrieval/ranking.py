"""Pure ranking over already-authorized evidence; never establishes eligibility.

The offline adapter imports these functions too. Predicate cues are bounded
lexical hints, not semantic entailment, inferred facts, or entity resolution.
"""

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from uuid import UUID

from domain_kg.graph.views import SubgraphView
from domain_kg.imports.evidence import visible_status
from domain_kg.retrieval.models import EvidenceCandidate, GraphStep

STOP = frozenset(
    "a an the of in on at to for and or is was were are be been by with which what "
    "who where when how did does do has have had that this whose it its as from "
    "city country company person name number year".split()
)
PREDICATE_CUES = {
    "born_in": "born birth birthplace hometown",
    "born_on": "born birth birthday",
    "died_on": "died death",
    "created_by": "author wrote written writer creator created directed director composed composer",
    "operated_by": "operated operator manages managed runs",
    "part_of": "parent subsidiary division belongs owned owner",
    "located_in": "located location situated city country where",
    "member_of": "member membership joined",
    "affiliated_with": "affiliated affiliation associated",
    "founded_on": "founded established foundation",
    "released_on": "released release published publication",
    "occurred_on": "occurred happened date",
    "participated_in": "participated played competed",
    "won": "won winner award",
    "uses": "use uses used using",
    "precedes": "before earlier precedes preceding",
    "causes": "cause causes caused",
    "supports": "support supports supported evidence",
    "opposes": "opposes opposed against",
}


def terms(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    result = re.findall(r"[^\W_]+(?:[.:/@_-][^\W_]+)*", text)
    for sequence in re.findall(r"[\u3400-\u9fff]+", text):
        result.extend(sequence)
        result.extend(sequence[index : index + 2] for index in range(len(sequence) - 1))
    return result


class BM25:
    """Statistics are confined to the supplied visible corpus, never a global index."""

    def __init__(self, texts: Mapping[str, str]) -> None:
        self.counts = {key: Counter(terms(text)) for key, text in texts.items()}
        self.lengths = {key: sum(value.values()) for key, value in self.counts.items()}
        self.average = sum(self.lengths.values()) / max(1, len(texts)) or 1.0
        self.df: Counter[str] = Counter()
        self.postings: dict[str, list[str]] = {}
        for key, counts in self.counts.items():
            self.df.update(counts.keys())
            for term in counts:
                self.postings.setdefault(term, []).append(key)

    def scores(self, query: str) -> dict[str, float]:
        query_terms = set(terms(query))
        if len(query_terms) > 512:
            raise ValueError("The lexical query exceeds the term budget.")
        scores: dict[str, float] = {}
        for term in sorted(query_terms):
            inverse = math.log(1 + (len(self.counts) - self.df[term] + 0.5) / (self.df[term] + 0.5))
            for key in self.postings.get(term, ()):
                frequency = self.counts[key][term]
                scores[key] = scores.get(key, 0.0) + (
                    inverse
                    * frequency
                    * 2.2
                    / (frequency + 1.2 * (0.25 + 0.75 * self.lengths[key] / self.average))
                )
        return scores


class SeedIndex:
    """Request-local in the service; immutable public snapshot in offline replay."""

    def __init__(self, labels: Mapping[UUID, Sequence[str]]) -> None:
        self.labels = {
            identifier: [set(terms(name)) - STOP for name in names]
            for identifier, names in labels.items()
        }
        self.postings: dict[str, set[UUID]] = {}
        for identifier, names in self.labels.items():
            for name_terms in names:
                if not name_terms or all(term.isdecimal() for term in name_terms):
                    continue
                for term in name_terms:
                    self.postings.setdefault(term, set()).add(identifier)

    def rank(self, query: str, vector_seeds: tuple[UUID, ...] = ()) -> tuple[UUID, ...]:
        query_terms = set(terms(query)) - STOP
        scores = {
            identifier: 1 / (60 + rank)
            for rank, identifier in reversed(list(enumerate(vector_seeds, 1)))
            if identifier in self.labels
        }
        matches = set().union(*(self.postings.get(term, set()) for term in query_terms))
        for identifier in matches:
            best = 0.0
            for name_terms in self.labels[identifier]:
                if not name_terms or all(term.isdecimal() for term in name_terms):
                    continue
                overlap = name_terms & query_terms
                if overlap:
                    best = max(
                        best,
                        len(overlap) / math.sqrt(len(name_terms))
                        + (2.0 if name_terms <= query_terms else 0.0),
                    )
            scores[identifier] = scores.get(identifier, 0.0) + best
        return tuple(sorted(scores, key=lambda key: (-scores[key], str(key))))


def rank_seeds(
    query: str,
    labels: Mapping[UUID, Sequence[str]],
    vector_seeds: tuple[UUID, ...] = (),
) -> tuple[UUID, ...]:
    """Name/alias matches precede vector-only seeds; generic words are not anchors."""
    return SeedIndex(labels).rank(query, vector_seeds)


def trace_paths(
    graph: SubgraphView, seeds: tuple[UUID, ...], max_hops: int
) -> dict[UUID, tuple[GraphStep, ...]]:
    """Shortest supported paths within the already bounded visible subgraph."""
    paths: dict[UUID, tuple[GraphStep, ...]] = {seed: () for seed in seeds}
    supports: dict[UUID, tuple[GraphStep, ...]] = {}
    for _ in range(max_hops):
        additions: dict[UUID, tuple[GraphStep, ...]] = {}
        for edge in graph.edges:
            step = GraphStep(
                subject_id=edge.subject_id,
                assertion_id=edge.assertion_id,
                target_id=edge.target_id,
                support_id=edge.support_id,
                predicate=edge.predicate,
                polarity=edge.polarity,
                original_subject_id=edge.original_subject_id,
                original_target_id=edge.original_target_id,
                identity_mappings=edge.identity_mappings,
                support_status=visible_status(edge.status),
                import_origin=edge.import_origin,
            )
            for start, end in (
                (edge.subject_id, edge.target_id),
                (edge.target_id, edge.subject_id),
            ):
                if start not in paths or len(paths[start]) >= max_hops:
                    continue
                if any(item.support_id == edge.support_id for item in paths[start]):
                    continue
                path = (*paths[start], step)
                if edge.support_id not in supports or len(path) < len(supports[edge.support_id]):
                    supports[edge.support_id] = path
                if end not in paths and end not in additions:
                    additions[end] = path
        paths.update(additions)
    return supports


def rank_graph_candidates(
    query: str, candidates: list[EvidenceCandidate], graph: SubgraphView
) -> list[EvidenceCandidate]:
    if any(item.kg_id != graph.kg_id for item in candidates):
        raise ValueError("Graph ranking cannot mix KG scopes")
    scores = BM25({str(i): item.text for i, item in enumerate(candidates)}).scores(query)
    maximum = max(scores.values(), default=1.0)
    query_terms = set(terms(query))
    edges = {edge.support_id: edge for edge in graph.edges}
    ranked = []
    for index, item in enumerate(candidates):
        affinity = 0.0
        for path in item.graph_paths:
            if not path or any(step.support_id not in edges for step in path):
                continue
            matched = set()
            for step in path:
                predicate = edges[step.support_id].predicate.casefold()
                cues = set(terms(predicate.replace("_", " "))) - STOP
                cues.update(PREDICATE_CUES.get(predicate, "").split())
                matched.update(cues & query_terms)
            affinity = max(affinity, min(len(matched), 2) / 2 + 0.1 / len(path))
        score = scores.get(str(index), 0.0) / maximum + 0.75 * affinity
        ranked.append(item.model_copy(update={"score": score}))
    # IDs only break equal relevance, and never supply relevance themselves.
    ranked.sort(key=lambda item: (-item.score, str(item.chunk_id or item.curation_revision_id)))
    return [
        item.model_copy(update={"method_ranks": {"graph": rank}})
        for rank, item in enumerate(ranked, 1)
    ]
