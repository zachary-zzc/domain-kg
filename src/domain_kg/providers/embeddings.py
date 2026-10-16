"""Explicit approved embedding profiles, bounded HTTP, and strict positional vectors."""

import asyncio
import json
import math
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import UUID

import httpx
from pydantic import Field

from domain_kg.models import SHA256, Namespace, NonNegative
from domain_kg.providers.extraction import Usage, unavailable
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile, embedding_space_id


class EmbeddingProfile(Namespace):
    id: UUID
    revision: NonNegative
    model: str
    dimensions: Annotated[int, Field(ge=1, le=4096, strict=True)]
    space_id: SHA256 | None = (
        None  # Legacy artifacts remain readable, but cannot serve vector queries.
    )


def normalized_vector(values: list[float], dimensions: int) -> tuple[float, ...]:
    if len(values) != dimensions or any(
        type(value) not in (float, int) or not math.isfinite(value) for value in values
    ):
        raise unavailable()
    magnitude = math.hypot(*values)
    if not math.isfinite(magnitude) or magnitude <= 0:
        raise unavailable()
    return tuple(float(value / magnitude) for value in values)


class EmbeddingClient:
    def __init__(
        self,
        approved: ProviderProfile,
        connection: ProviderConnection,
        *,
        authorize: Callable[[], Awaitable[None]],
        record_usage: Callable[[Usage], Awaitable[None]],
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.approved, self.connection = approved, connection
        self.authorize, self.record_usage = authorize, record_usage
        self.client = httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(approved.timeout_seconds, connect=5),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def embed(self, texts: list[str], profile: EmbeddingProfile) -> list[list[float]]:
        from tiktoken import get_encoding

        if (
            (profile.id, profile.revision, profile.model, profile.dimensions)
            != (
                self.approved.id,
                self.approved.revision,
                self.approved.embedding_model,
                self.approved.embedding_dimensions,
            )
            or Namespace(org_id=profile.org_id, kg_id=profile.kg_id)
            not in self.approved.allowed_namespaces
            or not self.approved.data_policy_approved
            or (
                profile.space_id is not None
                and profile.space_id != embedding_space_id(self.approved, self.connection)
            )
            or any(not text or len(text) > 32000 for text in texts)
        ):
            raise unavailable()
        tokenizer = get_encoding("cl100k_base")
        if any(len(tokenizer.encode(text, disallowed_special=())) > 8000 for text in texts):
            raise unavailable()
        result: list[list[float]] = []
        for start in range(0, len(texts), 16):
            batch = texts[start : start + 16]
            await self.authorize()
            request = {"input": batch, "model": profile.model, "encoding_format": "float"}
            try:
                async with asyncio.timeout(self.approved.timeout_seconds):
                    async with self.client.stream(
                        "POST",
                        self.connection.endpoint + "/embeddings",
                        json=request,
                        headers={
                            "Authorization": "Bearer " + self.connection.api_key.get_secret_value()
                        },
                    ) as response:
                        if response.status_code != 200:
                            raise unavailable()
                        body = bytearray()
                        async for part in response.aiter_bytes():
                            body.extend(part)
                            if len(body) > 8 * 1024**2:
                                raise unavailable()
                raw = json.loads(body)
                if raw["model"] != profile.model or len(raw["data"]) != len(batch):
                    raise unavailable()
                positions: dict[int, tuple[float, ...]] = {}
                for item in raw["data"]:
                    index = item["index"]
                    if type(index) is not int or not 0 <= index < len(batch) or index in positions:
                        raise unavailable()
                    positions[index] = normalized_vector(item["embedding"], profile.dimensions)
                tokens = raw["usage"]["prompt_tokens"]
                if type(tokens) is not int or not 0 <= tokens <= 128000:
                    raise unavailable()
                await self.record_usage(
                    Usage(
                        org_id=profile.org_id,
                        kg_id=profile.kg_id,
                        profile_id=profile.id,
                        input_tokens=tokens,
                        output_tokens=0,
                    )
                )
            except (
                httpx.HTTPError,
                TimeoutError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
            ) as exc:
                raise unavailable() from exc
            await self.authorize()
            result.extend(list(positions[index]) for index in range(len(batch)))
        return result
