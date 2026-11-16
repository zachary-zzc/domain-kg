"""Each working model builds its own graph through the production extraction path."""

import json
import time
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import SecretStr

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.extraction import Extractor
from domain_kg.ingestion.models import ParsedChunk
from domain_kg.ingestion.output_splitting import extract_with_splitting
from domain_kg.models import Namespace
from domain_kg.providers.budget import extraction_fits
from domain_kg.providers.extraction import ExtractionClient
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile
from eval import kg_quality_bbio as bbio
from eval.kg_quality_api import (
    API_BASE,
    EMBEDDING,
    WORK_MODELS,
    CloudCalls,
    cloud_client,
    save_once,
)
from eval.kg_quality_graph import graph_statistics, load_graph
from eval.scale_guard import pool
from eval.source_role_run import read, write_new

CONSTRUCTION_SEMANTICS = (
    "source_roles_v1",
    "source_facets_v1",
    "source_facets_v2",
    "lightrag_general_v1",
    "lightrag_general_v2",
)


def profile_for(model, namespace, *, semantics="source_roles_v1"):
    if model not in WORK_MODELS:
        raise ValueError("unapproved construction model")
    if semantics not in CONSTRUCTION_SEMANTICS:
        raise ValueError("unapproved construction semantics")
    return ProviderProfile(
        id=uuid5(NAMESPACE_URL, "kg-quality-v4/" + model),
        connection_ref="bbio" if model in bbio.MODELS else "alibaba",
        embedding_connection_ref="alibaba",
        extraction_model=model,
        embedding_model=EMBEDDING,
        embedding_dimensions=1024,
        allowed_namespaces=(namespace,),
        data_policy_approved=True,
        extraction_format="source_names_v1",
        extraction_semantics=semantics,
        extraction_response_format="json_object",
        extraction_enable_thinking=False if model.startswith("qwen3") else None,
        max_completion_tokens=8192,
        extraction_context_tokens=16384,
        timeout_seconds=bbio.DEADLINE if model in bbio.MODELS else 120,
    )


class ExtractionWire(httpx.AsyncBaseTransport):
    def __init__(self, calls, client, model):
        self.calls, self.client, self.model = calls, client, model

    async def handle_async_request(self, request):
        base = bbio.BASE + "/v1" if self.model in bbio.MODELS else API_BASE
        if str(request.url) != base + "/chat/completions":
            raise ValueError("API-only extraction endpoint changed")
        payload = json.loads(request.content)
        if payload["model"] != self.model:
            raise ValueError("construction working model changed")
        row = await self.calls.raw(self.client, payload)
        if row["http_status"] is None or (row["error"] and row["http_status"] == 200):
            raise httpx.TransportError("recorded API attempt failed or has ambiguous state")
        return httpx.Response(row["http_status"], json=row["response"], request=request)


async def construct(root, model, key, *, probe=False, concurrency=4):
    if type(concurrency) is not int or not 1 <= concurrency <= 16:
        raise ValueError("construction concurrency must be between 1 and 16")
    windows = read(root / "windows.json")
    if probe:
        windows = windows[:1]
    if not windows:
        raise ValueError("no construction windows")
    chunks = [ParsedChunk.model_validate(w["chunk"]) for w in windows]
    namespace = Namespace(org_id=chunks[0].org_id, kg_id=chunks[0].kg_id)
    if any((c.org_id, c.kg_id) != (namespace.org_id, namespace.kg_id) for c in chunks):
        raise ValueError("mixed construction namespaces")
    protocol = read(root / "protocol.json") if (root / "protocol.json").exists() else {}
    profile = profile_for(
        model, namespace, semantics=protocol.get("construction_semantics", "source_roles_v1")
    )
    schema = schema_for(namespace, "general")
    if not all(extraction_fits(c, schema, profile) for c in chunks):
        raise ValueError("shared source windows exceed construction model budget")
    directory = (root / "preflight" if probe else root) / "construction" / model
    directory.mkdir(parents=True, exist_ok=True)
    save_once(
        directory / "schedule.json", {"concurrency": 1 if model in bbio.MODELS else concurrency}
    )
    save_once(directory / "corpus.json", read(root / "corpus.json"))
    save_once(directory / "windows.json", windows)
    save_once(directory / "profile.json", profile.model_dump(mode="json"))
    versions = {c.version_id for c in chunks}

    async def authorize(chunk):
        if (chunk.org_id, chunk.kg_id) != (
            namespace.org_id,
            namespace.kg_id,
        ) or chunk.version_id not in versions:
            raise ValueError("extraction source scope changed")

    async def record_usage(_):
        # Every raw API response (including usage) is recorded by CloudCalls before return.
        return None

    calls = CloudCalls(directory / "calls", key)
    async with cloud_client() as network:
        remote = model in bbio.MODELS
        connection = ProviderConnection(
            ref=profile.connection_ref,
            endpoint=bbio.BASE + "/v1" if remote else API_BASE,
            api_key=SecretStr("unused-ssh-tunnel" if remote else key),
        )
        client = ExtractionClient(
            profile,
            connection,
            authorize=authorize,
            record_usage=record_usage,
            transport=ExtractionWire(calls, network, model),
        )
        extractor = Extractor(client)

        async def one(item):
            row, chunk = item
            path = directory / "revised" / (str(chunk.id) + ".json")
            if path.exists():
                if read(path).get("model") != model:
                    raise ValueError("saved construction model mismatch")
                return
            result = {"document": row["document"], "chunk_id": str(chunk.id), "model": model}
            start = time.perf_counter()
            try:
                batch = await extract_with_splitting(
                    chunk, schema, profile.id, lambda c: extractor.extract(c, schema)
                )
                result["batch"] = batch.model_dump(mode="json")
            except DomainError as error:
                result.update(error=str(error.code), message=error.message)
            result["seconds"] = time.perf_counter() - start
            write_new(path, result)

        try:
            await pool(
                list(zip(windows, chunks, strict=True)),
                one,
                concurrency=1 if remote else concurrency,
            )
        finally:
            await client.close()
    if probe:
        row = read(directory / "revised" / (str(chunks[0].id) + ".json"))
        return {
            "passed": "error" not in row,
            "error": row.get("error"),
            "message": row.get("message"),
        }
    graph = load_graph(directory, "revised", expected_model=model)
    save_once(directory / "graph.json", {**graph, "model": model})
    save_once(
        directory / "graph-statistics.json", graph_statistics(read(root / "corpus.json"), graph)
    )
