"""Connection checks retain real authority, scoped results and durable usage budgets."""

import json

import httpx
import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action
from domain_kg.providers.configuration import ModelConfiguration, ModelSetup
from domain_kg.providers.health import ProviderChecks
from domain_kg.providers.profiles import ProviderConfig, ProviderRegistry
from domain_kg.providers.service import ProviderService

pytestmark = pytest.mark.storage


@pytest.fixture
async def checked_provider(material_service, team, tmp_path):
    materials, kg = material_service
    config_path = tmp_path / "providers.json"
    config_path.write_text(ProviderConfig().model_dump_json())
    config_path.chmod(0o600)
    state = {"billing": False}

    def respond(request):
        if state["billing"]:
            return httpx.Response(
                402, json={"error": {"type": "billing_error", "message": "private balance detail"}}
            )
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "chat-test"}, {"id": "embed-test"}]})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(
                200,
                json={
                    "model": "embed-test",
                    "data": [{"index": 0, "embedding": [1, 0, 0]}],
                    "usage": {"prompt_tokens": 1},
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "stop", "message": {"content": '{"hello":"ok"}'}}],
                "usage": {"prompt_tokens": 8, "completion_tokens": 6},
            },
        )

    providers = ProviderService(
        team[1],
        materials.router,
        ProviderRegistry.from_file(config_path),
        transport=httpx.MockTransport(respond),
    )
    owner = await team[1].resolve_access(team[2]["owner"], [kg], Action.CONFIGURE)
    command = ModelConfiguration(
        name="Probe fixture",
        endpoint="https://probe.example/v1",
        api_key="private-probe-key",
        extraction_model="chat-test",
        embedding_model="embed-test",
        data_approval="approved",
        expected_revision=0,
        idempotency_key="checked-provider",
    )
    await ModelSetup(providers).save(owner, kg, command)
    return providers, owner, kg, state, config_path


async def test_hello_and_embedding_checks_are_budgeted_cached_and_invalidated_by_key_changes(
    checked_provider,
):
    providers, owner, kg, _, path = checked_provider
    checks = ProviderChecks(providers)
    report = await checks.selected(owner, kg)
    assert report.ready and report.chat.state == report.embedding.state == "passed"
    assert report.embedding.dimensions == 3
    assert "private-probe-key" not in report.model_dump_json()
    assert (await checks.selected(owner, kg)).fingerprint == report.fingerprint
    usage = await providers.database.provider_usage.find_one(
        {"org_id": str(owner.org_id), "kg_id": str(kg)}
    )
    assert usage["probe_calls"] == 2 and usage["calls"] == 2
    assert (await checks.last(owner, kg)).ready
    config = json.loads(path.read_text())
    config["connections"][0]["api_key"] = "rotated-private-key"
    path.write_text(json.dumps(config))
    assert await checks.last(owner, kg) is None


async def test_billing_is_actionable_without_returning_raw_provider_details(checked_provider):
    providers, owner, kg, state, _ = checked_provider
    state["billing"] = True
    report = await ProviderChecks(providers).selected(owner, kg)
    assert not report.ready
    assert str(report.chat.code) == "PROVIDER_BILLING_REQUIRED"
    assert report.embedding.state == "skipped"
    assert "private" not in report.model_dump_json()
    assert (await providers.selected(owner, kg))["revision"] == 1


async def test_model_discovery_and_checks_require_current_owner_operator_access(
    checked_provider, team
):
    providers, owner, kg, _, _ = checked_provider
    checks = ProviderChecks(providers)
    editor = await team[1].resolve_access(team[2]["editor"], [kg], Action.INGEST)
    with pytest.raises(DomainError):
        await checks.selected(editor, kg)
    assert (
        await providers.database.provider_usage.count_documents(
            {"org_id": str(owner.org_id), "kg_id": str(kg)}
        )
        == 0
    )
    assert await checks.models(owner, kg, component="chat") == ("chat-test", "embed-test")
