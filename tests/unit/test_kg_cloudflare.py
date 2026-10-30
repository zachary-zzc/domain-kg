import pytest

from eval.kg_cloudflare import MODELS, QuotaPause, Runner, endpoint, wire_request
from eval.source_role_eval import digest
from eval.source_role_run import write_new


def test_cloudflare_transport_preserves_frozen_constructor_content():
    alias = next(iter(MODELS))
    original = {
        "model": alias,
        "messages": [{"role": "user", "content": "source"}],
        "max_tokens": 1536,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "enable_thinking": False,
        "store": False,
    }
    value = wire_request(original)
    assert value["model"] == MODELS[alias]
    assert value["messages"] == original["messages"]
    assert value["max_tokens"] == 1536
    assert "enable_thinking" not in value and "store" not in value
    assert original["model"] == alias


def test_provider_destination_cannot_be_supplied_as_a_url():
    with pytest.raises(ValueError):
        endpoint("https://elsewhere.example")
    assert endpoint("a" * 32).startswith("https://api.cloudflare.com/client/v4/accounts/")


@pytest.mark.asyncio
async def test_ambiguous_cloudflare_dispatch_is_not_repeated(tmp_path):
    runner = Runner(tmp_path, "secret", "a" * 32)
    alias = next(iter(MODELS))
    payload = {"model": MODELS[alias], "messages": [], "max_tokens": 1536}
    label = "construct/" + digest(payload)
    write_new(
        tmp_path / (digest(label) + ".launch.json"),
        {
            "label": label,
            "request_sha256": digest(payload),
            "payload": payload,
            "endpoint": runner.url,
            "utc_day": "2026-10-21",
            "reserved_neurons": 60,
        },
    )

    class NoNetwork:
        def stream(self, *args, **kwargs):
            pytest.fail("ambiguous dispatch retried")

    row = await runner.call(NoNetwork(), label, payload)
    assert row["error"] == "ambiguous_launch"
    with pytest.raises(ValueError, match="identity"):
        await runner.call(NoNetwork(), label, {**payload, "max_tokens": 1000})


def test_unknown_usage_reserves_daily_capacity_instead_of_claiming_free(tmp_path):
    runner = Runner(tmp_path, "secret", "a" * 32)
    label = "prior"
    write_new(
        tmp_path / (digest(label) + ".launch.json"),
        {"utc_day": "2026-10-21", "reserved_neurons": 60},
    )
    assert runner.day_usage("2026-10-21") == 60
    write_new(tmp_path / (digest(label) + ".result.json"), {"usage": {"neurons": 17.5}})
    assert runner.day_usage("2026-10-21") == 17.5


@pytest.mark.asyncio
async def test_daily_budget_stops_before_dispatch_without_creating_empty_graph(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("eval.kg_cloudflare.DAILY_ALLOWANCE", 1)
    runner = Runner(tmp_path, "secret", "a" * 32)
    payload = {"model": next(iter(MODELS.values())), "messages": [], "max_tokens": 1536}

    class NoNetwork:
        def stream(self, *args, **kwargs):
            pytest.fail("quota exhausted but sent a request")

    with pytest.raises(QuotaPause):
        await runner.call(NoNetwork(), "construct/test", payload)
    assert not list(tmp_path.iterdir())
