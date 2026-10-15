"""Compatibility characterization with tiny deterministic inputs, not a model benchmark."""

import asyncio
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from neo4j import AsyncDriver
from qdrant_client import AsyncQdrantClient, models


async def fixture_llm(prompt: str, **kwargs: object) -> str:
    await asyncio.sleep(0)
    return json.dumps(
        {
            "entities": [{"name": "Alpha", "type": "Organization", "description": "Alpha project"}],
            "relationships": [],
        }
    )


async def test_real_lightrag_extraction_keeps_original_chunk_ids() -> None:
    from domain_kg.engine.lightrag import LightRAGBoundary, SourceChunk

    boundary = LightRAGBoundary()
    result = await boundary.extract(
        [
            SourceChunk(chunk_id="chunk-source-a", text="Alpha project.", source_id="version-a"),
            SourceChunk(chunk_id="chunk-source-b", text="Alpha project.", source_id="version-b"),
        ],
        llm=fixture_llm,
    )
    assert {(x.chunk_id, x.source_id, x.name) for x in result} == {
        ("chunk-source-a", "version-a", "Alpha"),
        ("chunk-source-b", "version-b", "Alpha"),
    }


async def test_engine_identity_mapping_is_scoped_and_survives_aliases() -> None:
    from domain_kg.engine.lightrag import EngineIdentityMap

    mapping = EngineIdentityMap()
    org, kg, other_kg, stable = uuid4(), uuid4(), uuid4(), uuid4()
    mapping.bind(org, kg, "Alpha", stable)
    mapping.bind(org, kg, "Renamed Alpha", stable)
    assert mapping.resolve(org, kg, "Alpha") == stable
    assert mapping.resolve(org, kg, "Renamed Alpha") == stable
    with pytest.raises(KeyError):
        mapping.resolve(org, other_kg, "Alpha")
    with pytest.raises(ValueError, match="already bound"):
        mapping.bind(org, kg, "Alpha", uuid4())


@pytest.mark.storage
async def test_concurrent_scopes_filter_real_graph_and_vectors(
    graph: AsyncDriver,
    vectors: AsyncQdrantClient,
) -> None:
    from domain_kg.engine.lightrag import ProbeReadScope, ScopedProjectionProbe

    org, kg = uuid4(), uuid4()
    collection = f"scope_{uuid4().hex}"
    scope_a = ProbeReadScope(org, kg, ("a",), "generation-1")
    scope_b = ProbeReadScope(org, kg, ("b",), "generation-1")
    with pytest.raises(FrozenInstanceError):
        scope_a.generation = "forged"  # type: ignore[misc]
    try:
        await vectors.create_collection(
            collection, vectors_config=models.VectorParams(size=3, distance=models.Distance.COSINE)
        )
        await vectors.upsert(
            collection,
            wait=True,
            points=[
                models.PointStruct(
                    id=1,
                    vector=[1.0, 0.0, 0.0],
                    payload={
                        "org": str(org),
                        "kg": str(kg),
                        "version": "a",
                        "generation": "generation-1",
                    },
                ),
                models.PointStruct(
                    id=2,
                    vector=[0.9, 0.1, 0.0],
                    payload={
                        "org": str(org),
                        "kg": str(kg),
                        "version": "b",
                        "generation": "generation-1",
                    },
                ),
                models.PointStruct(
                    id=3,
                    vector=[1.0, 0.0, 0.0],
                    payload={
                        "org": str(org),
                        "kg": str(uuid4()),
                        "version": "a",
                        "generation": "generation-1",
                    },
                ),
            ],
        )
        await graph.execute_query(
            "UNWIND ['a','b'] AS version "
            "CREATE (:ProbeFact {org:$org,kg:$kg,entity:'Alpha',version:version,"
            "generation:'generation-1'})-[:SUPPORTED_BY]->"
            "(:ProbeSource {org:$org,kg:$kg,version:version})",
            org=str(org),
            kg=str(kg),
        )
        probe = ScopedProjectionProbe(graph, vectors, collection)

        async def read(scope: ProbeReadScope) -> tuple[list[str], list[int | str]]:
            facts, points = await asyncio.gather(
                probe.graph_sources(scope, "Alpha"), probe.vector_ids(scope, [1.0, 0.0, 0.0], 1)
            )
            return facts, points

        results = await asyncio.gather(*(read(s) for s in [scope_a, scope_b] * 5))
        assert results == [(["a"], [1]), (["b"], [2])] * 5
        empty = ProbeReadScope(org, kg, (), "generation-1")
        assert await read(empty) == ([], [])
    finally:
        await vectors.delete_collection(collection)
        await graph.execute_query("MATCH (n {org:$org}) DETACH DELETE n", org=str(org))


@pytest.mark.parametrize("backend", ["embedded", pytest.param("server", marks=pytest.mark.storage)])
@pytest.mark.filterwarnings("ignore:Payload indexes have no effect.*:UserWarning")
async def test_real_lightrag_qdrant_upsert_requires_flush(
    monkeypatch: pytest.MonkeyPatch,
    settings,
    tmp_path: Path,
    backend: str,
) -> None:
    # Characterize the real upstream store, entirely in a worker thread. It uses
    # synchronous Qdrant I/O inside async functions, unlike our serving adapter.
    from domain_kg.engine.lightrag import run_blocking_engine

    monkeypatch.setenv("QDRANT_URL", settings.qdrant_url)
    monkeypatch.setenv("QDRANT_API_KEY", settings.qdrant_api_key.get_secret_value())
    monkeypatch.delenv("QDRANT_WORKSPACE", raising=False)

    async def probe() -> tuple[int, int, int]:
        import pipmaster

        def forbid_install(*args: object, **kwargs: object) -> None:
            raise AssertionError("runtime dependency installation is forbidden")

        monkeypatch.setattr(pipmaster, "install", forbid_install)
        from lightrag.kg.qdrant_impl import QdrantVectorDBStorage
        from lightrag.kg.shared_storage import initialize_share_data
        from lightrag.utils import EmbeddingFunc
        from qdrant_client import QdrantClient

        initialize_share_data(workers=1)
        calls = 0

        async def embed(texts: list[str], **kwargs: object) -> np.ndarray:
            nonlocal calls
            calls += len(texts)
            return np.array([[1.0, 0.0, 0.0] for _ in texts])

        storage = QdrantVectorDBStorage(
            namespace=f"smoke_{uuid4().hex}",
            workspace=f"org_kg_{uuid4().hex}",
            global_config={
                "embedding_batch_num": 2,
                "working_dir": str(tmp_path),
                "vector_db_storage_cls_kwargs": {"cosine_better_than_threshold": 0.0},
            },
            embedding_func=EmbeddingFunc(embedding_dim=3, func=embed, model_name="fixture-3d"),
            meta_fields={"source_id", "content"},
        )
        if backend == "embedded":
            client = QdrantClient(":memory:")
        else:
            client = QdrantClient(
                url=settings.qdrant_url, api_key=settings.qdrant_api_key.get_secret_value()
            )
        storage._client = client
        await storage.initialize()
        try:
            await storage.upsert({"chunk-a": {"content": "Alpha project", "source_id": "a"}})
            before = client.count(storage.final_namespace, exact=True).count
            assert calls == 0
            await storage.index_done_callback()
            after = client.count(storage.final_namespace, exact=True).count
            return before, after, calls
        finally:
            client.delete_collection(storage.final_namespace)
            await storage.finalize()
            client.close()

    assert await run_blocking_engine(probe) == (0, 1, 1)
