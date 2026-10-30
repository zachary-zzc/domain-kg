import json

import pytest
from pydantic import ValidationError
from test_extraction_validation import proposal, source
from test_provider_extraction import configured

from domain_kg.providers.extraction import extraction_payload
from domain_kg.providers.profiles import ProviderProfile, embedding_space_id
from domain_kg.providers.semantics import profile_cache_identity


@pytest.mark.parametrize("format", ["full", "source_refs_v1", "source_names_v1"])
def test_explicit_json_object_keeps_schema_in_prompt_and_disables_thinking(format):
    chunk = source()
    _, schema = proposal(chunk)
    profile, _ = configured(chunk)
    profile = ProviderProfile.model_validate(
        {
            **profile.model_dump(),
            "extraction_format": format,
            "extraction_response_format": "json_object",
            "extraction_enable_thinking": False,
        }
    )
    request = extraction_payload(chunk, schema, profile)
    assert request["response_format"] == {"type": "json_object"}
    assert request["enable_thinking"] is False
    system = request["messages"][0]["content"]
    assert '"additionalProperties":false' in system
    assert '"assertions"' in system and '"entities"' in system
    assert '"required"' in system
    assert request["max_completion_tokens"] == profile.max_completion_tokens
    assert "tools" not in request


def test_default_wire_and_cache_stay_identical_but_explicit_modes_rotate_extraction_only():
    chunk = source()
    _, schema = proposal(chunk)
    profile, connection = configured(chunk)
    legacy = profile_cache_identity(profile)
    assert "extraction_response_format" not in legacy
    assert "extraction_enable_thinking" not in legacy
    request = extraction_payload(chunk, schema, profile)
    assert request["response_format"]["type"] == "json_schema"
    assert "enable_thinking" not in request
    changed = ProviderProfile.model_validate(
        {
            **profile.model_dump(),
            "extraction_response_format": "json_object",
            "extraction_enable_thinking": False,
        }
    )
    assert profile_cache_identity(changed) != legacy
    assert json.loads(profile_cache_identity(changed))["extraction_enable_thinking"] is False
    assert embedding_space_id(profile, connection) == embedding_space_id(changed, connection)
    for fields in ({"extraction_response_format": "text"}, {"extraction_enable_thinking": "false"}):
        with pytest.raises(ValidationError):
            ProviderProfile.model_validate({**profile.model_dump(), **fields})


def test_json_object_prompt_schema_cannot_bypass_context_preflight():
    from domain_kg.providers.budget import extraction_fits

    chunk = source()
    _, schema = proposal(chunk)
    profile, _ = configured(chunk)
    profile = ProviderProfile.model_validate(
        {
            **profile.model_dump(),
            "schema_in_context": False,
            "max_completion_tokens": 512,
            "extraction_context_tokens": 4096,
        }
    )
    assert extraction_fits(chunk, schema, profile)
    enabled = profile.model_copy(update={"extraction_response_format": "json_object"})
    assert not extraction_fits(chunk, schema, enabled)
    assert extraction_fits(
        chunk, schema, enabled.model_copy(update={"extraction_context_tokens": 16384})
    )
