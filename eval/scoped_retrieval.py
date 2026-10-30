"""Public, offline adapter for the actual traversal/ranking/fusion implementation.

No provider or database client is opened. Public saved data is not an accepted
production KG. Equal dataset-scoped names are retrieval hints, NOT verified
identity merges. Production authorization is tested separately against real stores.
"""

from collections import defaultdict
from types import SimpleNamespace
from uuid import uuid5

from domain_kg.graph.repository import GraphRepository
from domain_kg.graph.views import EdgeView, NodeView
from domain_kg.models import SourceVersion
from domain_kg.retrieval.graph import expansion_seed_limit, source_citation
from domain_kg.retrieval.models import EvidenceCandidate, SearchFilters, SubgraphRequest
from domain_kg.retrieval.ranking import (
    BM25,
    PREDICATE_CUES,
    STOP,
    SeedIndex,
    rank_graph_candidates,
    terms,
    trace_paths,
)


class OfflineRepository:
    """Only the I/O seam is replaced; traverse is the production method."""

    def __init__(self, index):
        self.index = index
        self.resolver = self

    async def revalidate(self, scope):
        if scope is not self.index.scope:
            raise ValueError("offline scope mismatch")

    def node(self, data, identifier):
        return self.index.nodes.get(identifier)

    def edge(self, row, *, data):
        return row

    async def adjacent(self, data, frontier, limit):
        edges = {
            edge.support_id: edge
            for identifier in frontier
            for edge in self.index.adjacency.get(identifier, ())
        }
        return [edges[key] for key in sorted(edges, key=str)[:limit]]

    async def traverse(self, scope, request, *, data):
        return await GraphRepository.traverse(self, scope, request, data=data)


class OfflineIndex:
    def __init__(self, documents, facts, *, kg_id, bm25=None):
        self.kg_id = kg_id
        self.org_id = uuid5(kg_id, "offline-public-evaluation")
        self.documents = {doc["id"]: doc for doc in documents}
        if len(self.documents) != len(documents):
            raise ValueError("duplicate document ID")
        self.by_chunk = {}
        self.candidates = {}
        self.nodes = {}
        self.labels = {}
        self.edges = {}
        self.facts = {}
        self.adjacency = defaultdict(list)
        self.doc_nodes = defaultdict(set)
        for doc in documents:
            ident = doc["id"]
            chunk = uuid5(kg_id, "chunk/" + ident)
            self.by_chunk[chunk] = ident
            material, version = uuid5(kg_id, "material/" + ident), uuid5(kg_id, "version/" + ident)
            self.candidates[ident] = EvidenceCandidate(
                kind="source",
                kg_id=kg_id,
                material_id=material,
                version_id=version,
                chunk_id=chunk,
                text=doc["title"] + "\n" + doc["text"],
                citation_ids=(source_citation(self.org_id, kg_id, material, version, chunk),),
            )
        for index, fact in enumerate(facts):
            doc = self.documents.get(fact["d"])
            if (
                doc is None
                or not fact["q"]
                or fact["q"] not in doc["text"]
                or any(fact[k] not in doc["title"] + "\n" + doc["text"] for k in ("s", "o"))
                or fact["n"] not in ("positive", "negative", "uncertain")
            ):
                raise ValueError("saved graph grounding failed")
            endpoints = []
            for field in ("s", "o"):
                name = fact[field]
                # Dataset namespace prevents accidental joins between unrelated corpora.
                ident = uuid5(kg_id, doc["dataset"] + "/label/" + " ".join(terms(name)))
                endpoints.append(ident)
                self.labels.setdefault(ident, set()).add(name)
                self.doc_nodes[doc["id"]].add(ident)
            support = uuid5(kg_id, f"support/{index}/{doc['id']}")
            candidate = self.candidates[doc["id"]]
            edge = EdgeView(
                subject_id=endpoints[0],
                target_id=endpoints[1],
                assertion_id=uuid5(kg_id, f"assertion/{index}/{doc['id']}"),
                support_id=support,
                predicate=fact["p"],
                polarity=fact["n"],
                confidence=None,
                source=SourceVersion(
                    org_id=self.org_id,
                    kg_id=kg_id,
                    material_id=candidate.material_id,
                    version_id=candidate.version_id,
                ),
                spans=(),
            )
            self.edges[support], self.facts[support] = edge, fact
            for endpoint in set(endpoints):
                self.adjacency[endpoint].append(edge)
        for ident, names in self.labels.items():
            names = sorted(names)
            self.nodes[ident] = NodeView(
                kg_id=kg_id,
                id=ident,
                name=names[0],
                aliases=tuple(names[1:]),
                descriptions=(),
                entity_types=("offline_hint",),
                observations=(),
                observation_count=0,
                support_count=len(self.adjacency[ident]),
                neighbor_count=len(
                    {
                        e.target_id if e.subject_id == ident else e.subject_id
                        for e in self.adjacency[ident]
                    }
                ),
            )
        self.scope = SimpleNamespace(
            view=SimpleNamespace(kg_id=kg_id),
            filters=SearchFilters(),
            current_view=SimpleNamespace(revision=0),
        )
        self.data = SimpleNamespace(scope=self.scope)
        self.repository = OfflineRepository(self)
        self.bm25 = bm25 or BM25({key: value.text for key, value in self.candidates.items()})
        self.seed_index = SeedIndex({key: tuple(sorted(v)) for key, v in self.labels.items()})

    def document_id(self, candidate):
        if candidate.kg_id != self.kg_id:
            raise ValueError("offline evidence belongs to a different KG")
        return self.by_chunk[candidate.chunk_id]

    async def retrieve(self, query, vector_ids, *, max_hops=2):
        vectors = [self.candidates[key] for key in vector_ids[:50]]
        scores = self.bm25.scores(query)
        lexical_ids = sorted(scores, key=lambda key: (-scores[key], key))[:50]
        lexical = [self.candidates[key] for key in lexical_ids]
        vector_seeds = tuple(
            dict.fromkeys(
                node for key in vector_ids[:50] for node in sorted(self.doc_nodes[key], key=str)
            )
        )
        seeds = self.seed_index.rank(query, vector_seeds)[: expansion_seed_limit(200, max_hops)]
        if seeds:
            view = await self.repository.traverse(
                self.scope,
                SubgraphRequest(kg_id=self.kg_id, seed_ids=seeds, max_hops=max_hops),
                data=self.data,
            )
        else:
            from domain_kg.graph.views import SubgraphView

            view = SubgraphView(kg_id=self.kg_id, revision=0, nodes=(), edges=())
        paths = trace_paths(view, seeds, max_hops)
        grouped = defaultdict(list)
        for support, path in paths.items():
            grouped[self.facts[support]["d"]].append(path)
        graph_candidates = [
            self.candidates[key].model_copy(
                update={
                    "graph_paths": tuple(values),
                    "entity_ids": tuple(sorted(self.doc_nodes[key], key=str)),
                    "assertion_ids": tuple(
                        sorted({step.assertion_id for path in values for step in path}, key=str)
                    ),
                }
            )
            for key, values in grouped.items()
        ]
        ranked = rank_graph_candidates(query, graph_candidates, view)[:50]
        return {"vector": vectors, "lexical": lexical, "graph": ranked}, view


def constrain_graph(query, candidates, view):
    """Offline experiment: named anchor AND a requested terminal predicate.

    This deliberately narrow lexical planner abstains when no entity or relation
    can be resolved. It does not claim natural-language semantic understanding.
    Re-trace within the same visible bounded graph from query anchors alone:
    vector-seeded intermediate nodes must not flatten multi-hop explanations.
    Neither graph direction nor polarity is inferred or changed.
    """
    query_terms = set(terms(query)) - STOP
    allowed = {
        predicate
        for predicate, cues in PREDICATE_CUES.items()
        if query_terms.intersection(cues.split())
    }
    if not allowed:
        return []
    anchors = tuple(
        node.id
        for node in view.nodes
        if any(
            (name_terms := set(terms(name)) - STOP)
            and not all(term.isdecimal() for term in name_terms)
            and name_terms <= query_terms
            for name in (node.name, *node.aliases)
        )
    )
    if not anchors:
        return []
    paths = trace_paths(view, anchors, 2)
    constrained = []
    for item in candidates:
        supports = {path[-1].support_id for path in item.graph_paths if path}
        matching = tuple(
            path
            for support, path in paths.items()
            if support in supports and path[-1].predicate in allowed
        )
        if matching:
            constrained.append(item.model_copy(update={"graph_paths": matching}))
    return rank_graph_candidates(query, constrained, view)


def evidence_coverage(case, selected, index):
    supplied = {
        unit["id"]
        for item in selected
        for unit in index.documents[index.document_id(item)]["units"]
        if unit["text"] in item.text
    }
    gold = set(case["support"])
    return {
        "complete": int(gold <= supplied) if gold else None,
        "recall": len(gold & supplied) / len(gold) if gold else None,
        "source_characters": sum(len(item.text) for item in selected),
    }


def path_context(index, selected, view, *, max_chars=2000):
    """Present only paths whose exact source quotes occur in the selected evidence."""
    import json

    chosen = {index.document_id(item): item for item in selected}
    visible = {edge.support_id: edge for edge in view.edges}
    paths, seen, omitted = [], set(), 0
    for item in selected:
        for path in item.graph_paths:
            key = tuple(step.support_id for step in path)
            if not key or key in seen:
                continue
            seen.add(key)
            rendered = []
            for step in path:
                if step.support_id not in visible:
                    break
                fact = index.facts[step.support_id]
                source = chosen.get(fact["d"])
                if source is None or fact["q"] not in source.text:
                    break
                rendered.append(
                    {
                        "subject": fact["s"],
                        "predicate": fact["p"],
                        "object": fact["o"],
                        "polarity": fact["n"],
                        "document": fact["d"],
                        "quote": fact["q"],
                        "citation": str(source.citation_ids[0]),
                    }
                )
            else:
                candidate = paths + [rendered]
                if len(json.dumps(candidate, ensure_ascii=False)) <= max_chars:
                    paths = candidate
                    continue
            omitted += 1
    return {
        "sources": [index.document_id(item) for item in selected],
        "paths": paths,
        "omitted_paths": omitted,
        "path_characters": len(json.dumps(paths, ensure_ascii=False)),
        "identity_status": "dataset-scoped label hints; no verified identity merge",
    }
