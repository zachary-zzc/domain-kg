"""Replay saved production extraction; describe structure without claiming semantic validity."""

import hashlib
from collections import Counter, defaultdict
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from domain_kg.graph.views import NodeView
from domain_kg.ingestion.models import ExtractionBatch, ParsedChunk
from domain_kg.retrieval.ranking import BM25, SeedIndex, terms
from eval.kg_quality import validate_corpus
from eval.scoped_retrieval import OfflineIndex
from eval.source_role_eval import digest
from eval.source_role_run import read


def topology(nodes, edges):
    """Weak components; degree counts distinct OTHER neighbors; loops counted separately."""
    adjacency = {n: set() for n in nodes}
    incoming, outgoing, pairs = Counter(), Counter(), set()
    dangling = loops = 0
    for edge in edges:
        a, b = edge.subject_id, edge.target_id
        if a not in nodes or b not in nodes:
            dangling += 1
            continue
        incoming[b] += 1
        outgoing[a] += 1
        if a == b:
            loops += 1
            continue
        pairs.add((a, b))
        adjacency[a].add(b)
        adjacency[b].add(a)
    remaining, components = set(nodes), []
    while remaining:
        start = remaining.pop()
        stack, size = [start], 0
        while stack:
            node = stack.pop()
            size += 1
            neighbors = adjacency[node] & remaining
            remaining.difference_update(neighbors)
            stack.extend(neighbors)
        components.append(size)
    n = len(nodes)
    # A self-loop is incident to a node, so a loop-only node is not isolated.
    isolates = sum(not incoming[v] and not outgoing[v] for v in nodes)
    return {
        "nodes": n,
        "edge_observations": len(edges),
        "dangling_edges": dangling,
        "self_loops": loops,
        "isolates": isolates,
        "isolate_rate": isolates / n if n else None,
        "weak_components": len(components),
        "component_size_histogram": dict(Counter(components)),
        "largest_component_nodes": max(components, default=0),
        "largest_component_fraction": max(components) / n if n else None,
        "unique_directed_pairs_without_loops": len(pairs),
        "directed_density": len(pairs) / (n * (n - 1)) if n > 1 else 0,
        "mean_neighbor_degree": sum(map(len, adjacency.values())) / n if n else None,
        "neighbor_degree_histogram": dict(Counter(len(v) for v in adjacency.values())),
        "in_degree_histogram": dict(Counter(incoming[n] for n in nodes)),
        "out_degree_histogram": dict(Counter(outgoing[n] for n in nodes)),
    }


def covering_quote(spans, text):
    positions = []
    for span in spans:
        start = text.find(span.quote)
        if start < 0:
            raise ValueError("extraction quote absent from original source")
        positions.append((start, start + len(span.quote)))
    return text[min(a for a, _ in positions) : max(b for _, b in positions)]


def load_graph(root, arm, *, expected_model=None):
    if arm not in ("baseline", "revised"):
        raise ValueError("unknown saved extraction arm")
    docs, windows = read(root / "corpus.json"), read(root / "windows.json")
    by_doc, _ = validate_corpus(docs)
    hashes, rows, chunks = {}, [], set()
    for window in windows:
        chunk = ParsedChunk.model_validate(window["chunk"]).model_dump(mode="json")
        if chunk["id"] in chunks or window["document"] not in by_doc:
            raise ValueError("duplicate window or unknown source")
        chunks.add(chunk["id"])
        path = root / arm / (chunk["id"] + ".json")
        row = read(path)
        if expected_model is not None and row.get("model") != expected_model:
            raise ValueError("saved construction model mismatch")
        hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        if row["document"] != window["document"]:
            raise ValueError("extraction source changed")
        if "error" not in row:
            batch = ExtractionBatch.model_validate(row["batch"])
            if str(batch.chunk_id) != chunk["id"] or any(
                str(getattr(batch, field)) != chunk[field]
                for field in ("org_id", "kg_id", "material_id", "version_id")
            ):
                raise ValueError("extraction source scope changed")
        rows.append(row)
    if {w["document"] for w in windows} != set(by_doc):
        raise ValueError("corpus documents missing extraction windows")
    failed = {r["document"] for r in rows if "error" in r}
    facts, observations, local_edges = [], [], []
    for row in rows:
        if row["document"] in failed:
            continue  # Publication unit is a whole document, as in the saved experiment.
        batch = ExtractionBatch.model_validate(row["batch"])
        doc = by_doc[row["document"]]
        text = doc["title"] + "\n" + doc["text"]
        entities = {e.local_id: e for e in batch.entities}
        for entity in batch.entities:
            covering_quote(entity.spans, text)
            if entity.name not in text:
                raise ValueError("extraction name absent from source")
            observations.append(
                {
                    "id": str(batch.chunk_id) + "/" + entity.local_id,
                    "d": doc["id"],
                    "name": entity.name,
                    "type": entity.entity_type,
                }
            )
        for assertion in batch.assertions:
            facts.append(
                {
                    "d": doc["id"],
                    "s": entities[assertion.subject].name,
                    "o": entities[assertion.target].name,
                    "p": assertion.predicate,
                    "n": assertion.polarity,
                    "q": covering_quote(assertion.spans, text),
                    "qualifiers": [
                        {"name": q.name, "value": q.value, "q": covering_quote(q.spans, text)}
                        for q in assertion.qualifiers
                    ],
                }
            )
            local_edges.append(
                {
                    "subject_id": str(batch.chunk_id) + "/" + assertion.subject,
                    "target_id": str(batch.chunk_id) + "/" + assertion.target,
                }
            )
    return {
        "facts": facts,
        "observations": observations,
        "local_edges": local_edges,
        "construction": {
            "windows": len(windows),
            "failed_windows": sum("error" in r for r in rows),
            "failed_documents": sorted(failed),
            "document_atomicity": "any failed window excludes whole document graph",
        },
        "input_hashes": hashes,
    }


def build_index(docs, graph):
    validate_corpus(docs)
    # Full title+body supports canonical title-spanning quotes. Replace the adapter's
    # extra leading newline afterward, so the source text remains exactly title+body.
    index = OfflineIndex(
        [{**d, "title": "", "text": d["title"] + "\n" + d["text"]} for d in docs],
        graph["facts"],
        kg_id=uuid5(NAMESPACE_URL, "kg-quality-v2-offline"),
        bm25=BM25({d["id"]: d["title"] + "\n" + d["text"] for d in docs}),
    )
    for d in docs:
        index.candidates[d["id"]] = index.candidates[d["id"]].model_copy(
            update={"text": d["title"] + "\n" + d["text"]}
        )
    for observation in graph["observations"]:
        doc = index.documents[observation["d"]]
        ident = uuid5(
            index.kg_id, doc["dataset"] + "/label/" + " ".join(terms(observation["name"]))
        )
        index.doc_nodes[observation["d"]].add(ident)
        index.labels.setdefault(ident, set()).add(observation["name"])
        if ident not in index.nodes:
            index.nodes[ident] = NodeView(
                kg_id=index.kg_id,
                id=ident,
                name=observation["name"],
                aliases=(),
                descriptions=(),
                entity_types=(observation["type"],),
                observations=(),
                observation_count=1,
                support_count=0,
                neighbor_count=0,
            )
    index.seed_index = SeedIndex({k: tuple(sorted(v)) for k, v in index.labels.items()})
    return index


def graph_statistics(docs, graph, index=None):
    index = index or build_index(docs, graph)
    facts, observations = graph["facts"], graph["observations"]
    predicates = Counter(f["p"] for f in facts)
    node_types = Counter(o["type"] for o in observations)
    incidence = defaultdict(set)
    for doc, nodes in index.doc_nodes.items():
        for node in nodes:
            incidence[node].add(doc)
    unique_claims = {
        digest({k: f.get(k, []) for k in ("d", "s", "p", "o", "n", "qualifiers")}) for f in facts
    }
    documents_with_edges = {f["d"] for f in facts}
    return {
        "scope": "offline saved extraction; not accepted production graph",
        "identity": "dataset-scoped normalized names are unverified lexical joins",
        "semantic_precision": None,
        "semantic_recall": None,
        "validity_note": "shape/endpoints/source correspondence validated; entailment not scored",
        "construction": graph["construction"],
        "documents": len(docs),
        "documents_with_relations": len(documents_with_edges),
        "document_relation_coverage": len(documents_with_edges) / len(docs) if docs else None,
        "documents_without_relations": sorted({d["id"] for d in docs} - documents_with_edges),
        "node_observations": len(observations),
        "relation_observations": len(facts),
        "unique_document_claims_with_qualifiers": len(unique_claims),
        "duplicate_claim_observations": len(facts) - len(unique_claims),
        "literal_node_types": dict(node_types),
        "literal_predicates": dict(predicates),
        "predicate_type_count": len(predicates),
        "singleton_predicate_types": sum(n == 1 for n in predicates.values()),
        "node_type_count": len(node_types),
        "nodes_in_multiple_documents": sum(len(v) > 1 for v in incidence.values()),
        "observation_topology": topology(
            {o["id"] for o in observations}, [SimpleNamespace(**e) for e in graph["local_edges"]]
        ),
        "retrieval_topology": topology(set(index.nodes), list(index.edges.values())),
    }
