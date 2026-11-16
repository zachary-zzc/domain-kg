"""General model-only extraction: bounded gleaning/repair and unchanged trust boundaries."""

import copy
import json

import httpx
import pytest
from test_compact_extraction import compact_profile, facts
from test_extraction_validation import source

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.models import ExtractionResult
from domain_kg.models import Namespace
from domain_kg.providers.extraction import ExtractionClient, extraction_payload
from domain_kg.providers.profiles import ProviderProfile


def general(chunk):
    profile, connection = compact_profile(chunk, "source_names_v1")
    profile = ProviderProfile.model_validate(
        profile.model_dump()
        | {"extraction_semantics": "lightrag_general_v1", "extraction_context_tokens": 16384}
    )
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    return profile, connection, schema


def named(target="Beta"):
    value = facts()
    value["entities"][1]["name"] = target
    value["assertions"][0].update(subject="Alpha", target=target)
    return value


async def invoke(
    responses, *, authorize=None, text="Alpha uses Beta and Gamma.", semantics="lightrag_general_v1"
):
    chunk = source(text)
    profile, connection, schema = general(chunk)
    profile = ProviderProfile.model_validate(
        profile.model_dump() | {"extraction_semantics": semantics}
    )
    requests, usage, authorizations = [], [], []

    async def auth(item):
        authorizations.append(item)
        if authorize:
            await authorize(len(authorizations))

    async def record(item):
        usage.append(item)

    def reply(request):
        requests.append(json.loads(request.content))
        value = responses[len(requests) - 1]
        if isinstance(value, Exception):
            raise value
        content = value if isinstance(value, str) else json.dumps(value)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            },
        )

    client = ExtractionClient(
        profile,
        connection,
        authorize=auth,
        record_usage=record,
        transport=httpx.MockTransport(reply),
    )
    try:
        result = ExtractionResult.model_validate(await client.complete(chunk, schema))
        return result, requests, usage, authorizations
    finally:
        await client.close()


@pytest.mark.parametrize(
    "semantics", ["lightrag_general_v1", "lightrag_general_v2"]
)
async def test_general_gleaning_adds_missing_facts_with_same_model_and_accounts_each_call(
    semantics,
):
    result, requests, usage, auth = await invoke([named(), named("Gamma")], semantics=semantics)
    assert len(result.assertions) == 2
    assert len(requests) == len(usage) == 2 and len(auth) == 4
    assert requests[0]["model"] == requests[1]["model"]
    assert "previously accepted" in requests[1]["messages"][-1]["content"]
    assert len({(a.subject, a.target, a.predicate) for a in result.assertions}) == 2


async def test_gleaning_duplicate_claim_does_not_create_additional_support():
    result, requests, _, _ = await invoke([named(), named()])
    assert len(requests) == 2
    assert len(result.entities) == 2 and len(result.assertions) == 1


@pytest.mark.parametrize("bad", ["not JSON", {"entities": [], "assertions": [], "grant": "owner"}])
async def test_model_repairs_general_format_failure_once_without_local_json_repair(bad):
    result, requests, usage, _ = await invoke([bad, named()])
    assert len(requests) == len(usage) == 2 and len(result.assertions) == 1
    assert "format" in requests[1]["messages"][-1]["content"]
    with pytest.raises(DomainError):
        await invoke([bad, bad])


async def test_format_correction_retains_independent_valid_facts_when_one_claim_is_invalid():
    corrected = named()
    corrected["assertions"].append({**corrected["assertions"][0], "target": "Invented"})
    result, requests, usage, _ = await invoke(["invalid JSON", corrected])
    assert len(requests) == len(usage) == 2
    assert len(result.assertions) == 1
    assert result.warnings == ("extraction_rejected_assertions:1",)


async def test_arbitrary_source_qualifiers_survive_and_unsupported_values_do_not():
    initial = named()
    initial["assertions"][0]["qualifiers"] = [
        {"name": "operating_mode", "value": "Gamma", "refs": [0]}
    ]
    result, _, _, _ = await invoke([initial, {"entities": [], "assertions": []}])
    assert result.assertions[0].qualifiers[0].name == "operating_mode"
    bad = copy.deepcopy(initial)
    bad["assertions"][0]["qualifiers"][0]["value"] = "invented"
    result, requests, _, _ = await invoke([bad, {"entities": [], "assertions": []}])
    assert not result.assertions and len(requests) == 2


async def test_optional_qualifiers_and_failed_gleaning_preserve_valid_initial_facts():
    initial = named()
    del initial["assertions"][0]["qualifiers"]
    result, requests, _, _ = await invoke([initial, "bad JSON"])
    assert len(requests) == 2 and len(result.assertions) == 1
    assert result.assertions[0].qualifiers == ()
    assert "extraction_gleaning_incomplete" in result.warnings


async def test_general_policy_never_recovers_authority_or_transport_failure():
    async def revoked(count):
        if count == 4:
            raise DomainError("FORBIDDEN", "Revoked")

    with pytest.raises(DomainError, match="Revoked"):
        await invoke([named(), named("Gamma")], authorize=revoked)
    with pytest.raises(DomainError):
        await invoke([httpx.ConnectError("unavailable")])


def test_general_prompt_has_no_domain_vocabulary_or_case_specific_role_dictionary():
    chunk = source()
    profile, _, schema = general(chunk)
    instructions = extraction_payload(chunk, schema, profile)["messages"][0]["content"]
    assert "lightrag_general_v1" in instructions
    assert "no predefined" in instructions
    assert "portrayed" not in instructions and "born_on" not in instructions
    assert "condition, time or location" not in instructions


def test_initial_split_preserves_complete_paragraphs_when_each_fits():
    from test_chunking import document

    from domain_kg.ingestion.chunking import chunk_document
    from domain_kg.ingestion.models import ChunkConfig

    paragraph = "A general application retains each independent paragraph of source evidence.\n\n"
    chunks = chunk_document(
        document(paragraph * 8), ChunkConfig(target_tokens=32, overlap_tokens=0)
    )
    assert all(
        part == paragraph.strip()
        for chunk in chunks
        for span in chunk.spans
        for part in span.quote.strip().split("\n\n")
    )


@pytest.mark.parametrize("kind", ["text", "html"])
@pytest.mark.parametrize(
    "text",
    [
        "مرحبا بالعالم.\n\n" * 40,
        "Ame\u0301lie 👩‍🔬 研究结果。\n" * 30,
        "\n\t " * 100,
        "x" * 2000,
    ],
)
def test_shared_splitter_preserves_multilingual_coverage_and_exact_offsets(kind, text):
    from test_chunking import document

    from domain_kg.ingestion.chunking import chunk_document, tokens
    from domain_kg.ingestion.models import ChunkConfig

    parsed = document(text, kind=kind)
    chunks = chunk_document(parsed, ChunkConfig(target_tokens=32, overlap_tokens=8))
    covered = set()
    for chunk in chunks:
        assert tokens(chunk.text) <= 32
        for span in chunk.spans:
            assert text[span.start : span.end] == span.quote
            covered.update(range(span.start, span.end))
    assert covered == set(range(len(text)))


async def test_gleaning_context_overflow_keeps_initial_result_without_sending_large_request():
    chunk = source("Alpha uses Beta.")
    profile, connection, schema = general(chunk)
    request = extraction_payload(chunk, schema, profile)
    size = len(
        json.dumps(
            {"messages": request["messages"]}, ensure_ascii=False, separators=(",", ":")
        ).encode()
    )
    profile = profile.model_copy(
        update={"extraction_context_tokens": size + profile.max_completion_tokens + 256}
    )
    calls = []

    async def noop(_):
        pass

    def reply(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(named())}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    client = ExtractionClient(
        profile, connection, authorize=noop, record_usage=noop, transport=httpx.MockTransport(reply)
    )
    try:
        result = await client.complete(chunk, schema)
        assert len(calls) == 1 and len(result["assertions"]) == 1
        assert result["warnings"] == ["extraction_gleaning_context_limit"]
    finally:
        await client.close()


@pytest.mark.parametrize("version", ["lightrag_general_v2"])
def test_general_revision_preserves_v1_and_has_distinct_checkpoint_identity(version):
    from domain_kg.providers.semantics import profile_cache_identity

    chunk = source()
    old, _, schema = general(chunk)
    new = ProviderProfile.model_validate(old.model_dump() | {"extraction_semantics": version})
    before = extraction_payload(chunk, schema, old)["messages"][0]["content"]
    after = extraction_payload(chunk, schema, new)["messages"][0]["content"]
    assert "lightrag_general_v1" in before and "nested" not in before
    assert version in after and "nested" in after
    assert "alternate names" in after
    assert profile_cache_identity(old) != profile_cache_identity(new)
    assert "born_on" not in after and "portrayed" not in after
