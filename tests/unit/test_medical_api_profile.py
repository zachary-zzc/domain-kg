"""An explicit public-PDF API configuration cannot silently fall back or widen scope."""

import json
from uuid import uuid4

import pytest

from domain_kg.models import Namespace
from scripts.medical_extract import ROOT, connections, profile_for, provider_fingerprint


def private_file(tmp_path, monkeypatch, *, approved=True, allowed=()):
    value = json.loads((ROOT / "config/providers.medical-api.example.json").read_bytes())
    value["connections"][0].update(
        endpoint="https://approved.example/v1", api_key="private-test-key"
    )
    value["profiles"][0].update(
        extraction_model="approved-medical-api",
        data_policy_approved=approved,
        allowed_namespaces=[n.model_dump(mode="json") for n in allowed],
    )
    path = tmp_path / "provider.json"
    path.write_text(json.dumps(value))
    path.chmod(0o600)
    monkeypatch.setenv("DOMAIN_KG_MEDICAL_API_CONFIG", str(path))


def test_explicit_api_is_bound_only_to_the_current_public_fixture_namespace(tmp_path, monkeypatch):
    private_file(tmp_path, monkeypatch)
    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    profile = profile_for(ns)
    assert profile.extraction_model == "approved-medical-api" and profile.allowed_namespaces == (
        ns,
    )
    assert len(connections()) == 2 and "private-test-key" not in repr(connections())
    other = profile_for(Namespace(org_id=uuid4(), kg_id=uuid4()))
    assert other.id != profile.id and provider_fingerprint(other) == provider_fingerprint(profile)


def test_disabled_or_other_namespace_api_never_falls_back_to_a_native_model(tmp_path, monkeypatch):
    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    private_file(tmp_path, monkeypatch, approved=False)
    with pytest.raises(RuntimeError, match="Approve"):
        profile_for(ns)
    private_file(tmp_path, monkeypatch, allowed=(Namespace(org_id=uuid4(), kg_id=uuid4()),))
    with pytest.raises(RuntimeError, match="scope"):
        profile_for(ns)
