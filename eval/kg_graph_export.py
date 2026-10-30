"""Offline public benchmark graph downloads with source-bound node identity."""

import html
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from eval.source_role_eval import digest, parse_facts


def graph_payload(name, docs, facts):
    sources = {d["id"]: {k: d[k] for k in ("id", "dataset", "title", "text")} for d in docs}
    if len(sources) != len(docs):
        raise ValueError("duplicate source ID")
    nodes, edges = {}, []
    from domain_kg.retrieval.ranking import terms

    namespace = uuid5(NAMESPACE_URL, "full-scale-retrieval-v1")
    for i, fact in enumerate(facts):
        source = sources.get(fact["d"])
        if source is None:
            raise ValueError("unknown graph source")
        parsed = parse_facts(
            json.dumps({"facts": [{k: v for k, v in fact.items() if k != "d"}]}), source, 1
        )[0]
        ids = []
        for field in ("s", "o"):
            label = parsed[field]
            ident = "n-" + digest([name, fact["d"], label])[:24]
            hint = str(uuid5(namespace, source["dataset"] + "/label/" + " ".join(terms(label))))
            nodes.setdefault(
                ident,
                {
                    "id": ident,
                    "label": label,
                    "source": fact["d"],
                    "dataset": source["dataset"],
                    "hint_id": hint,
                },
            )
            ids.append(ident)
        edges.append(
            {
                "id": "e-" + str(i),
                "from": ids[0],
                "to": ids[1],
                "predicate": parsed["p"],
                "polarity": parsed["n"],
                "quote": parsed["q"],
                "qualifiers": parsed["qualifiers"],
                "source": fact["d"],
            }
        )
    return {
        "name": name,
        "format": "source-bound-evidence-graph-v1",
        "note": (
            "Source-bound nodes. Equal hint_id values are unverified lexical retrieval links, "
            "not established entity identity. Model proposals are not accepted production facts."
        ),
        "sources": list(sources.values()),
        "nodes": list(nodes.values()),
        "edges": edges,
    }


def html_document(data):
    template = Path(__file__).with_name("kg_graph_viewer.html").read_text()
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for char, replacement in [
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ]:
        encoded = encoded.replace(char, replacement)
    return template.replace("__TITLE__", html.escape(data["name"])).replace(
        "__GRAPH_DATA__", encoded
    )


def export_graph(path, name, docs, facts):
    data = graph_payload(name, docs, facts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_name(path.name + ".json").write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    )
    path.with_name(path.name + ".html").write_text(html_document(data))
    return {
        "name": name,
        "nodes": len(data["nodes"]),
        "edges": len(data["edges"]),
        "sources": len(docs),
        "sources_with_facts": len({e["source"] for e in data["edges"]}),
        "html": str(path.with_name(path.name + ".html")),
        "json": str(path.with_name(path.name + ".json")),
    }
