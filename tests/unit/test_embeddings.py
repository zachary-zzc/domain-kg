"""Embedding count, position, dimension and numeric bounds are provider contracts."""

import httpx
import pytest

from domain_kg.errors import DomainError


@pytest.mark.parametrize("fault", [None, "dimensions", "nan", "duplicate", "missing", "zero"])
async def test_embedding_response_must_match_every_input(fault):
    from test_extraction_validation import source
    from test_provider_extraction import configured

    from domain_kg.providers.embeddings import EmbeddingClient, EmbeddingProfile

    chunk = source()
    approved, connection = configured(chunk)
    profile = EmbeddingProfile(
        org_id=chunk.org_id,
        kg_id=chunk.kg_id,
        id=approved.id,
        revision=approved.revision,
        model=approved.embedding_model,
        dimensions=3,
    )

    def respond(_):
        data = [
            {"index": 1, "embedding": [0.0, 1.0, 0.0]},
            {"index": 0, "embedding": [1.0, 0.0, 0.0]},
        ]
        if fault == "dimensions":
            data[0]["embedding"] = [1.0, 0.0]
        if fault == "nan":
            return httpx.Response(200, content=b'{"data":[{"index":0,"embedding":[NaN,1,0]}]}')
        if fault == "duplicate":
            data[0]["index"] = 0
        if fault == "missing":
            data.pop()
        if fault == "zero":
            data[0]["embedding"] = [0.0, 0.0, 0.0]
        return httpx.Response(
            200,
            json={
                "model": approved.embedding_model,
                "data": data,
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    async def authorize():
        pass

    async def usage(_):
        pass

    client = EmbeddingClient(
        approved,
        connection,
        authorize=authorize,
        record_usage=usage,
        transport=httpx.MockTransport(respond),
    )
    try:
        if fault:
            with pytest.raises(DomainError):
                await client.embed(["first", "second"], profile)
        else:
            assert await client.embed(["first", "second"], profile) == [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
            ]
    finally:
        await client.close()
