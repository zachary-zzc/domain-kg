import importlib
import importlib.util

import pytest

from eval.kg_comparison import construction_request


def schema():
    assert importlib.util.find_spec("eval.kg_cloudflare_schema"), "Schema adapter is missing"
    return importlib.import_module("eval.kg_cloudflare_schema")


def test_schema_adapter_keeps_source_prompt_and_token_budget():
    adapter = schema()
    doc = {"id": "doc", "title": "Alpha", "text": "Alpha uses Beta."}
    alias = "cf-llama-3.2-3b-schema"
    payload = adapter.construction_payload(alias, doc, "direct")
    original = construction_request("cf-llama-3.2-3b", doc, "direct")
    assert payload["messages"] == original["messages"]
    assert payload["max_tokens"] == 1536
    assert payload["model"] == "@cf/meta/llama-3.2-3b-instruct"
    shape = payload["response_format"]["json_schema"]
    assert shape["properties"]["facts"]["maxItems"] == 8
    assert shape["$defs"]["Fact"]["additionalProperties"] is False


def test_schema_adapter_preserves_all_declared_relation_values():
    adapter = schema()
    from eval.dual_path_data import PREDICATES

    payload = adapter.construction_payload(
        "cf-llama-3.1-8b-fp8-schema",
        {"id": "doc", "title": "Alpha", "text": "Alpha uses Beta."},
        "direct",
    )
    values = payload["response_format"]["json_schema"]["$defs"]["Fact"]["properties"]["p"]["enum"]
    assert values == list(PREDICATES)


def test_unsupported_small_model_is_not_silently_replaced_with_a_larger_one():
    adapter = schema()
    with pytest.raises(ValueError, match="unapproved"):
        adapter.construction_payload(
            "cf-llama-3.2-1b-schema",
            {"id": "doc", "title": "Alpha", "text": "Alpha uses Beta."},
            "direct",
        )
