import pytest

from eval.kg_graph_export import graph_payload, html_document


def test_source_scoped_nodes_keep_homonyms_separate_and_provenance_intact():
    docs = [
        {"id": "a", "dataset": "x", "title": "Alpha", "text": "Alpha does not use Beta."},
        {"id": "b", "dataset": "x", "title": "Alpha", "text": "Alpha uses Gamma."},
    ]
    facts = [
        {
            "d": "a",
            "s": "Alpha",
            "p": "uses",
            "o": "Beta",
            "n": "negative",
            "q": docs[0]["text"],
            "qualifiers": [],
        },
        {"d": "b", "s": "Alpha", "p": "uses", "o": "Gamma", "n": "positive", "q": docs[1]["text"]},
    ]
    data = graph_payload("example", docs, facts)
    alphas = [n for n in data["nodes"] if n["label"] == "Alpha"]
    assert len(alphas) == 2 and alphas[0]["id"] != alphas[1]["id"]
    assert alphas[0]["hint_id"] == alphas[1]["hint_id"]
    assert data["edges"][0]["polarity"] == "negative"
    assert data["edges"][0]["quote"] == docs[0]["text"]
    assert len(data["edges"]) == len(facts)


def test_cross_source_or_fabricated_quote_is_rejected():
    docs = [{"id": "a", "dataset": "x", "title": "A", "text": "A uses B."}]
    for source, quote in [("unknown", "A uses B."), ("a", "A uses C.")]:
        with pytest.raises(ValueError):
            graph_payload(
                "test",
                docs,
                [{"d": source, "s": "A", "p": "uses", "o": "B", "n": "positive", "q": quote}],
            )


def test_html_cannot_execute_material_text_and_is_self_contained():
    bad = "</script><script>alert(1)</script>"
    data = graph_payload(bad, [{"id": "a", "dataset": "x", "title": bad, "text": bad}], [])
    html = html_document(data)
    assert "<script>alert(1)</script>" not in html
    assert "\\u003c/script\\u003e" in html
    assert "<script src=" not in html and "http://" not in html and "https://" not in html


def test_dotted_model_names_do_not_overwrite_other_graphs(tmp_path):
    from eval.kg_graph_export import export_graph

    export_graph(tmp_path / "qwen3.8-27b--initial", "first", [], [])
    export_graph(tmp_path / "qwen3.5-397b--corrected", "second", [], [])
    assert (tmp_path / "qwen3.8-27b--initial.html").exists()
    assert (tmp_path / "qwen3.5-397b--corrected.html").exists()
    assert len(list(tmp_path.glob("*.html"))) == 2
