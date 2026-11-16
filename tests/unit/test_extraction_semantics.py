import json

import pytest
from pydantic import ValidationError
from test_extraction_validation import proposal, source
from test_provider_extraction import configured

from domain_kg.providers.extraction import extraction_payload
from domain_kg.providers.profiles import ProviderProfile
from domain_kg.providers.semantics import profile_cache_identity, role_instructions


@pytest.mark.parametrize("format", ["full", "source_refs_v1", "source_names_v1"])
def test_opt_in_roles_reach_all_provider_formats_without_changing_source(format):
    chunk = source()
    _, schema = proposal(chunk)
    profile, _ = configured(chunk)
    profile = ProviderProfile.model_validate({**profile.model_dump(), "extraction_format": format})
    legacy = extraction_payload(chunk, schema, profile)
    enabled = ProviderProfile.model_validate(
        {**profile.model_dump(), "extraction_semantics": "source_roles_v1"}
    )
    request = extraction_payload(chunk, schema, enabled)
    assert request["messages"][1:] == legacy["messages"][1:]
    assert request["response_format"] == legacy["response_format"]
    instructions = request["messages"][0]["content"]
    assert "source_roles_v1" in instructions
    assert "subject -> target" in instructions
    assert "qualifiers" in instructions
    assert "uses:" in instructions
    assert "created_by:" not in instructions  # not part of this schema
    assert "source_roles_v1" not in legacy["messages"][0]["content"]


def test_role_policy_cache_identity_preserves_legacy_and_separates_new_policy():
    profile, _ = configured(source())
    historical = profile.model_dump_json(exclude={"extraction_semantics"})
    assert profile_cache_identity(profile) == historical
    enabled = ProviderProfile.model_validate(
        {**profile.model_dump(), "extraction_semantics": "source_roles_v1"}
    )
    assert profile_cache_identity(enabled) != historical
    assert json.loads(profile_cache_identity(enabled))["extraction_semantics"] == "source_roles_v1"


def test_relation_definitions_are_selected_and_unknown_policy_is_rejected():
    instructions = role_instructions(("created_by", "born_on", "custom"))
    assert "created_by:" in instructions and "born_on:" in instructions
    assert "released_on:" not in instructions
    assert "performer" in instructions and "naturalization" in instructions
    profile, _ = configured(source())
    with pytest.raises(ValidationError):
        ProviderProfile.model_validate({**profile.model_dump(), "extraction_semantics": "bad"})


def test_compact_evidence_numbering_versions_extraction_checkpoints():
    profile, _ = configured(source())
    compact = profile.model_copy(update={"extraction_format": "source_names_v1"})
    identity = json.loads(profile_cache_identity(compact))
    assert identity["evidence_passages"] == "source-spans-v3-open-qualifiers"
    assert "evidence_passages" not in json.loads(profile_cache_identity(profile))


@pytest.mark.parametrize("format", ["full", "source_refs_v1", "source_names_v1"])
@pytest.mark.parametrize("policy", ["source_facets_v1", "source_facets_v2"])
def test_source_facets_policy_preserves_source_schema_and_call_budget(format, policy):
    from domain_kg.graph.schema import schema_for
    from domain_kg.models import Namespace

    chunk = source()
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    profile, _ = configured(chunk)
    profile = ProviderProfile.model_validate(
        {
            **profile.model_dump(),
            "extraction_format": format,
            "extraction_semantics": "source_roles_v1",
        }
    )
    candidate = ProviderProfile.model_validate(
        {**profile.model_dump(), "extraction_semantics": policy}
    )
    old, new = (extraction_payload(chunk, schema, p) for p in (profile, candidate))
    assert old["messages"][1:] == new["messages"][1:]
    assert {k: v for k, v in old.items() if k != "messages"} == {
        k: v for k, v in new.items() if k != "messages"
    }
    assert profile_cache_identity(profile) != profile_cache_identity(candidate)
    assert policy in new["messages"][0]["content"]
    assert policy not in old["messages"][0]["content"]
    assert "entity_types" not in new["messages"][1]["content"]


@pytest.mark.parametrize("policy", ["source_facets_v1", "source_facets_v2"])
def test_facets_policy_keeps_custom_closed_vocabulary_and_roles(policy):
    chunk = source()
    _, schema = proposal(chunk)
    profile, _ = configured(chunk)
    old = profile.model_copy(update={"extraction_semantics": "source_roles_v1"})
    new = ProviderProfile.model_validate(
        {**profile.model_dump(), "extraction_semantics": policy}
    )
    assert extraction_payload(chunk, schema, old) == extraction_payload(chunk, schema, new)


def test_role_instructions_consume_context_and_fail_before_calls_when_too_small():
    from domain_kg.graph.schema import schema_for
    from domain_kg.models import Namespace
    from domain_kg.providers.budget import extraction_fits

    chunk = source()
    schema = schema_for(Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id), "general")
    profile, _ = configured(chunk)
    values = {
        **profile.model_dump(),
        "max_completion_tokens": 512,
        "schema_in_context": False,
        "extraction_context_tokens": 4096,
    }
    assert extraction_fits(chunk, schema, ProviderProfile.model_validate(values))
    values["extraction_semantics"] = "source_roles_v1"
    assert not extraction_fits(chunk, schema, ProviderProfile.model_validate(values))
    values["extraction_context_tokens"] = 8192
    assert extraction_fits(chunk, schema, ProviderProfile.model_validate(values))
