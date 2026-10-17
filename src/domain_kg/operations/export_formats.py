"""Bounded serialization of already authorized graph views; labels are always data."""

import csv
import io
import json
from typing import Any, Literal
from xml.etree import ElementTree as ET

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.views import EdgeView, NodeView
from domain_kg.models import Namespace

ExportFormat = Literal["jsonl", "csv", "graphml"]
MAX_BYTES = 50 * 1024 * 1024
XMLNS = "http://graphml.graphdrawing.org/xmlns"


def _scoped(value: Any, namespace: Namespace) -> None:
    if isinstance(value, dict):
        if "org_id" in value and (value.get("org_id"), value.get("kg_id")) != (
            str(namespace.org_id),
            str(namespace.kg_id),
        ):
            raise inaccessible_reference()
        if "kg_id" in value and value["kg_id"] != str(namespace.kg_id):
            raise inaccessible_reference()
        for child in value.values():
            _scoped(child, namespace)
    elif isinstance(value, list):
        for child in value:
            _scoped(child, namespace)


def _safe_cell(value: str) -> str:
    return (
        "'" + value
        if value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@"))
        or (value.startswith(("\t", "\r", "\n")))
        else value
    )


def _xml_text(value: str) -> str:
    if any(
        char not in "\t\n\r"
        and not (
            0x20 <= ord(char) <= 0xD7FF
            or 0xE000 <= ord(char) <= 0xFFFD
            or 0x10000 <= ord(char) <= 0x10FFFF
        )
        for char in value
    ):
        raise DomainError(
            ErrorCode.UNSUPPORTED_FORMAT, "An export field cannot be represented in XML."
        )
    return value


def encode_records(
    namespace: Namespace,
    nodes: tuple[NodeView, ...],
    edges: tuple[EdgeView, ...],
    format: ExportFormat,
) -> tuple[bytes, str]:
    if len(nodes) + len(edges) > 20000:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Export exceeds its record budget.")
    identifiers = {node.id for node in nodes}
    endpoint_ids = {str(value) for value in identifiers}
    if len(identifiers) != len(nodes) or len({edge.support_id for edge in edges}) != len(edges):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Export identities are duplicated.")
    records: list[tuple[dict[str, Any], str]] = []
    namespace_fields = {"org_id": str(namespace.org_id), "kg_id": str(namespace.kg_id)}
    size = 0
    for kind, values in (("node", nodes), ("edge", edges)):
        for value in values:
            body = value.model_dump(mode="json")
            _scoped(body, namespace)
            record: dict[str, Any] = namespace_fields | {"kind": kind, kind: body}
            encoded = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
            size += len(encoded.encode())
            if size > MAX_BYTES:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Export exceeds its byte budget.")
            records.append((record, encoded))
    if format == "jsonl":
        result = ("\n".join(encoded for _, encoded in records) + ("\n" if records else "")).encode()
        media = "application/x-ndjson"
    elif format == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(
            (
                "org_id",
                "kg_id",
                "kind",
                "id",
                "name",
                "subject_id",
                "target_id",
                "predicate",
                "body_json",
            )
        )
        for record, encoded in records:
            body = record[record["kind"]]
            cells = (
                record["org_id"],
                record["kg_id"],
                record["kind"],
                body.get("id", body.get("support_id", "")),
                body.get("name", ""),
                body.get("subject_id", ""),
                body.get("target_id", ""),
                body.get("predicate", ""),
                encoded,
            )
            writer.writerow(tuple(_safe_cell(str(cell)) for cell in cells))
        result, media = output.getvalue().encode(), "text/csv; charset=utf-8"
    elif format == "graphml":
        root = ET.Element(f"{{{XMLNS}}}graphml")
        for key in ("org_id", "kg_id", "name", "predicate", "body_json"):
            ET.SubElement(
                root,
                f"{{{XMLNS}}}key",
                {"id": key, "for": "all", "attr.name": key, "attr.type": "string"},
            )
        graph = ET.SubElement(
            root, f"{{{XMLNS}}}graph", {"id": str(namespace.kg_id), "edgedefault": "directed"}
        )
        for key, namespace_value in namespace_fields.items():
            ET.SubElement(graph, f"{{{XMLNS}}}data", {"key": key}).text = namespace_value
        for record, encoded in records:
            body = record[record["kind"]]
            if record["kind"] == "node":
                element = ET.SubElement(graph, f"{{{XMLNS}}}node", {"id": body["id"]})
                key = "name"
            else:
                if not {body["subject_id"], body["target_id"]}.issubset(endpoint_ids):
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT, "Export endpoints are missing."
                    )
                element = ET.SubElement(
                    graph,
                    f"{{{XMLNS}}}edge",
                    {
                        "id": body["support_id"],
                        "source": body["subject_id"],
                        "target": body["target_id"],
                    },
                )
                key = "predicate"
            ET.SubElement(element, f"{{{XMLNS}}}data", {"key": key}).text = _xml_text(body[key])
            ET.SubElement(element, f"{{{XMLNS}}}data", {"key": "body_json"}).text = encoded
        result, media = (
            ET.tostring(root, encoding="utf-8", xml_declaration=True),
            "application/graphml+xml",
        )
    else:
        raise DomainError(ErrorCode.UNSUPPORTED_FORMAT, "The export format is unsupported.")
    if len(result) > MAX_BYTES:
        raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Export exceeds its byte budget.")
    return result, media
