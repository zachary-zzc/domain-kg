"""Export uses the same source-qualified identity and filtering as inspection."""

import pytest
from test_identity_graph_views import graph_fixture

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.repository import GraphRepository
from domain_kg.retrieval.models import SearchFilters


def test_export_keeps_source_qualified_merge_and_original_supports():
    from domain_kg.operations.export_views import export_views

    data, alpha, beta, gamma, *_ = graph_fixture()
    nodes, edges = export_views(data, GraphRepository(None, None))
    assert {node.id for node in nodes} == {alpha, beta, gamma}
    assert {edge.subject_id for edge in edges} == {alpha, beta}
    assert all(edge.target_id == gamma and edge.spans for edge in edges)
    assert len({edge.support_id for edge in edges}) == len(edges)


def test_export_honors_excluded_endpoints_and_never_truncates_silently():
    from domain_kg.operations.export_views import export_views

    data, *rest = graph_fixture()
    gamma = rest[2]
    filtered = data.__class__(
        data.scope.model_copy(update={"filters": SearchFilters(exclude_entity_ids=(gamma,))}),
        data.rows,
        data.curations,
    )
    nodes, edges = export_views(filtered, GraphRepository(None, None))
    assert gamma not in {node.id for node in nodes} and edges == ()
    with pytest.raises(DomainError) as bounded:
        export_views(data, GraphRepository(None, None), max_nodes=2)
    assert bounded.value.code == ErrorCode.QUOTA_EXCEEDED
