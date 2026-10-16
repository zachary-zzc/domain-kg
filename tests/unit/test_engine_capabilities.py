"""An upstream upgrade must satisfy the pinned source and scope adapter contract."""

import pytest

from domain_kg.engine.capabilities import checked_capabilities
from domain_kg.errors import DomainError


def test_locked_engine_capabilities_do_not_advertise_native_acl_or_accepted_mutations():
    capability = checked_capabilities()
    assert capability.version == "1.5.7"
    assert capability.scope_required and capability.source_mapping
    assert not capability.native_acl_filters and not capability.accepted_native_mutations
    assert capability.maintained_patch is None
    assert set(capability.service_modes) == {"fast", "deep", "vector", "lexical", "graph"}


def test_unreviewed_engine_version_and_missing_helper_contract_fail_closed(monkeypatch):
    import domain_kg.engine.capabilities as contract

    monkeypatch.setattr(contract, "version", lambda _: "999.0.0")
    with pytest.raises(DomainError):
        checked_capabilities()
    monkeypatch.setattr(contract, "version", lambda _: "1.5.7")
    import lightrag.operate

    monkeypatch.setattr(lightrag.operate, "_perform_kg_search", lambda query: {})
    with pytest.raises(DomainError):
        checked_capabilities()


def test_unsupported_filter_and_native_mode_are_rejected_before_backend_selection():
    from uuid import uuid4

    from pydantic import ValidationError

    from domain_kg.retrieval.models import SearchFilters, SearchRequest

    with pytest.raises(ValidationError):
        SearchFilters.model_validate({"legacy_workspace_acl": "all"})
    with pytest.raises(ValidationError):
        SearchRequest(kg_ids=(uuid4(),), query="fixture", mode="mix")
