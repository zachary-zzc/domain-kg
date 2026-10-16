"""Offline bounded import decoding; input text never selects commands or accepted IDs.

PhageAgent's graph_rag.py CSV column contract and versioned manifest/normalization
ideas informed this adapter. Implementation is new; no upstream source or corpus
is copied. See docs/code-reuse.md for the inspected source paths and limitations.
"""

import csv
import hashlib
import io
import json
import unicodedata
from uuid import UUID, uuid5
from xml.etree.ElementTree import Element

from defusedxml import ElementTree

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.imports.legacy_models import (
    ImportEdge,
    ImportNode,
    LegacyEntity,
    LegacyGraph,
    LegacyRelation,
    Polarity,
)
from domain_kg.imports.lightrag import snapshot_records
from domain_kg.models import Namespace

MAX_BYTES = 2 * 1024 * 1024
MAX_RECORDS = 1000
CSV_FIELDS = {
    "entity1",
    "entity1_type",
    "relation",
    "entity2",
    "entity2_type",
    "pdf_name",
    "source",
}


def invalid() -> DomainError:
    return DomainError(
        ErrorCode.VALIDATION_ERROR, "The legacy graph format or ownership is invalid."
    )


def polarity(value: str) -> Polarity:
    match value.strip():
        case "positive":
            return "positive"
        case "negative":
            return "negative"
        case "uncertain":
            return "uncertain"
        case _:
            raise invalid()


def stable(namespace: Namespace, *parts: str) -> UUID:
    return uuid5(
        namespace.org_id, json.dumps([str(namespace.kg_id), *parts], separators=(",", ":"))
    )


def csv_records(text: str) -> tuple[tuple[ImportNode, ...], tuple[ImportEdge, ...]]:
    reader = csv.DictReader(io.StringIO(text), strict=True)
    if (
        reader.fieldnames is None
        or set(reader.fieldnames) != CSV_FIELDS
        or len(reader.fieldnames) != 7
    ):
        raise invalid()
    nodes: dict[str, ImportNode] = {}
    edges = []
    for index, row in enumerate(reader):
        if index >= MAX_RECORDS or None in row or any(value is None for value in row.values()):
            raise invalid()
        source = row["pdf_name"].strip() or row["source"].strip() or "missing-original"
        for key in ("entity1", "entity2"):
            # Bare names are legacy keys only, never accepted database IDs.
            node = ImportNode(id=row[key], name=row[key], entity_type=row[key + "_type"])
            if node.id in nodes and nodes[node.id] != node:
                raise invalid()
            nodes[node.id] = node
        edge_id = json.dumps(
            [row["entity1"], row["relation"], row["entity2"], source], separators=(",", ":")
        )
        edges.append(
            ImportEdge(
                id=edge_id,
                source=row["entity1"],
                target=row["entity2"],
                predicate=row["relation"],
                source_ids=(source,),
            )
        )
    return tuple(nodes.values()), tuple(edges)


def graphml_records(body: bytes) -> tuple[tuple[ImportNode, ...], tuple[ImportEdge, ...]]:
    root = ElementTree.fromstring(body, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    if root.tag != "{http://graphml.graphdrawing.org/xmlns}graphml":
        raise invalid()
    prefix = "{http://graphml.graphdrawing.org/xmlns}"
    if any(child.tag not in (prefix + "key", prefix + "graph") for child in root):
        raise invalid()
    if any(key.rsplit("}", 1)[-1] in ("org_id", "kg_id") for key in root.attrib):
        raise invalid()
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    keys = {}
    for key in root.findall("g:key", ns):
        identifier, name = key.get("id"), key.get("attr.name")
        if not identifier or not name or identifier in keys or name in ("org_id", "kg_id"):
            raise invalid()
        keys[identifier] = name
    graphs = root.findall("g:graph", ns)
    if len(graphs) != 1 or graphs[0].get("edgedefault") != "directed":
        raise invalid()

    def data(element: Element) -> dict[str, str]:
        if any(child.tag != prefix + "data" for child in element):
            raise invalid()
        fields = {}
        for child in element.findall("g:data", ns):
            key = child.get("key", "")
            if key not in keys or keys[key] in fields or len(child):
                raise invalid()
            fields[keys[key]] = child.text or ""
        return fields

    graph = graphs[0]
    if set(graph.attrib) - {"id", "edgedefault"} or any(
        child.tag not in (prefix + "node", prefix + "edge") for child in graph
    ):
        raise invalid()
    nodes = []
    for index, element in enumerate(graph.findall("g:node", ns)):
        if index >= MAX_RECORDS:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "The legacy graph exceeds its record budget."
            )
        if set(element.attrib) - {"id"}:
            raise invalid()
        fields = data(element)
        identifier = element.get("id", "")
        nodes.append(
            ImportNode(
                id=identifier,
                name=fields.get("entity_name", identifier),
                entity_type=fields.get("entity_type", "concept"),
                description=fields.get("description", ""),
            )
        )
    edges = []
    for index, element in enumerate(graph.findall("g:edge", ns)):
        if index >= MAX_RECORDS:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "The legacy graph exceeds its record budget."
            )
        if set(element.attrib) - {"id", "source", "target", "directed"} or element.get(
            "directed", "true"
        ) not in ("true", "1"):
            raise invalid()
        fields = data(element)
        sources = fields.get("source_id", "missing-original").split("<SEP>")
        edges.append(
            ImportEdge(
                id=element.get("id", str(index)),
                source=element.get("source", ""),
                target=element.get("target", ""),
                predicate=fields.get("relation", ""),
                polarity=polarity(fields.get("polarity", "positive")),
                source_ids=tuple(sources),
            )
        )
    if len(nodes) > MAX_RECORDS or len(edges) > MAX_RECORDS:
        raise invalid()
    return tuple(nodes), tuple(edges)


def parse_legacy(namespace: Namespace, format: str, body: bytes, *, origin: str) -> LegacyGraph:
    namespace = Namespace.model_validate(namespace)
    if len(body) > MAX_BYTES:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The legacy import exceeds its byte budget.")
    try:
        text = body.decode("utf-8-sig")
        if format == "csv":
            nodes, edges = csv_records(text)
        elif format == "graphml":
            nodes, edges = graphml_records(body)
        elif format == "lightrag":
            nodes, edges = snapshot_records(body)
        else:
            raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "The legacy format is not supported.")
        by_id = {node.id: node for node in nodes}
        if len(by_id) != len(nodes) or len(by_id) > MAX_RECORDS or len(edges) > MAX_RECORDS:
            raise invalid()
        entities: dict[tuple[str, str], LegacyEntity] = {}
        relations: dict[tuple[str, str], LegacyRelation] = {}
        for edge in edges:
            if (
                edge.source not in by_id
                or edge.target not in by_id
                or len(set(edge.source_ids)) != len(edge.source_ids)
            ):
                raise invalid()
            for source in edge.source_ids:
                for key in (edge.source, edge.target):
                    node = by_id[key]
                    entities[(source, key)] = LegacyEntity(
                        id=stable(namespace, origin, "observation", source, key),
                        entity_id=stable(namespace, origin, "entity", key),
                        legacy_id=key,
                        source_id=source,
                        name=node.name,
                        entity_type=node.entity_type,
                    )
                relation = LegacyRelation(
                    id=stable(namespace, origin, "support", source, edge.id),
                    legacy_id=edge.id,
                    assertion_id=stable(
                        namespace,
                        origin,
                        "assertion",
                        edge.source,
                        edge.predicate,
                        edge.target,
                        edge.polarity,
                    ),
                    subject_id=entities[(source, edge.source)].entity_id,
                    target_id=entities[(source, edge.target)].entity_id,
                    source_id=source,
                    predicate=edge.predicate,
                    polarity=edge.polarity,
                )
                existing = relations.get((source, edge.id))
                if existing is not None and existing != relation:
                    raise invalid()
                relations[(source, edge.id)] = relation
                if len(relations) > MAX_RECORDS or len(entities) > 2000:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED,
                        "The legacy graph exceeds its source fan-out budget.",
                    )
        referenced = {key for _, key in entities}
        for key, node in by_id.items():
            if key in referenced:
                continue
            source = "missing-original"
            entities[(source, key)] = LegacyEntity(
                id=stable(namespace, origin, "observation", source, key),
                entity_id=stable(namespace, origin, "entity", key),
                legacy_id=key,
                source_id=source,
                name=node.name,
                entity_type=node.entity_type,
            )
            if len(entities) > 2000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "The legacy graph exceeds its entity budget."
                )
        normalized = unicodedata.normalize("NFC", text.replace("\r\n", "\n").replace("\r", "\n"))
        return LegacyGraph(
            namespace=namespace,
            origin=origin,
            raw_sha256=hashlib.sha256(body).hexdigest(),
            normalized_sha256=hashlib.sha256(
                json.dumps(
                    [str(namespace.org_id), str(namespace.kg_id), origin, normalized],
                    ensure_ascii=False,
                ).encode()
            ).hexdigest(),
            entities=tuple(sorted(entities.values(), key=lambda item: str(item.id))),
            relations=tuple(sorted(relations.values(), key=lambda item: str(item.id))),
            warnings=("AGGREGATED_DESCRIPTION_DROPPED",)
            if any(node.description for node in nodes)
            else (),
        )
    except DomainError:
        raise
    except Exception:
        raise invalid() from None
