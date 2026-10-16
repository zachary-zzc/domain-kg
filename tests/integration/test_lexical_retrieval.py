"""CJK/identifier recall and eligible-only lexical statistics."""

import pytest
from test_retrieval_corpus import retrieval_corpus  # noqa: F401

from domain_kg.retrieval.lexical import LexicalRetrievalBranch
from domain_kg.retrieval.models import SearchFilters, SearchRequest

pytestmark = pytest.mark.storage


async def test_visible_fulltext_cjk_identifiers_and_text_only_chunks(retrieval_corpus, monkeypatch):  # noqa: F811
    publishing, reader, receipts = retrieval_corpus
    branch = LexicalRetrievalBranch(publishing[2])

    async def forbidden(*args, **kwargs):
        raise AssertionError("lexical-only read opened a vector/full backend")

    monkeypatch.setattr(publishing[0].router, "for_kg", forbidden)
    monkeypatch.setattr(publishing[0].router, "for_vectors", forbidden)
    for query, filters, expected in (
        (
            "阿尔法",
            SearchFilters(),
            {receipts["public"].material_id, receipts["plain"].material_id},
        ),
        ("urn:fixture:identifier-A7", SearchFilters(), {receipts["plain"].material_id}),
        ("PRIVATE_NEAREST", SearchFilters(), set()),
        ("Plain", SearchFilters(), {receipts["plain"].material_id}),
        ("Plain", SearchFilters(predicates=("uses",)), set()),
        ("Alpha", SearchFilters(tags_all=("plain",)), set()),
    ):
        request = SearchRequest(kg_ids=reader.kg_ids, query=query, mode="lexical", filters=filters)
        scope = await branch.resolver.resolve(reader, request, reader.kg_ids[0])
        result = await branch.retrieve(scope, request)
        assert {item.material_id for item in result} == expected
        assert all(item.chunk_id and item.citation_ids for item in result)
    request = SearchRequest(kg_ids=reader.kg_ids, query="Alpha", mode="lexical")
    scope = await branch.resolver.resolve(reader, request, reader.kg_ids[0])
    before = await branch.retrieve(scope, request)
    graph = await publishing[2].router.for_graph(reader, reader.kg_ids[0])
    await graph.driver.execute_query(
        "MATCH (n:ScopedProjection {org_id:$org,kg_id:$kg,material_id:$material}) SET n.text=$text",
        org=str(reader.org_id),
        kg=str(reader.kg_ids[0]),
        material=str(receipts["hidden"].material_id),
        text="Alpha " * 100,
    )
    after = await branch.retrieve(scope, request)
    assert [(item.chunk_id, item.score) for item in before] == [
        (item.chunk_id, item.score) for item in after
    ]
