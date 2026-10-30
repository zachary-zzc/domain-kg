"""Explicit approved embedding profiles, bounded HTTP, and strict positional vectors."""

import asyncio
import math
from collections.abc import Awaitable, Callable
from contextlib import nullcontext
from typing import Annotated
from uuid import UUID

import httpx
from pydantic import Field

from domain_kg.models import SHA256, Namespace, NonNegative
from domain_kg.providers.call_budget import CallBudget
from domain_kg.providers.extraction import Usage, unavailable
from domain_kg.providers.http import WireModel, provider_headers, provider_json
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile, embedding_space_id
from domain_kg.providers.usage_budget import BudgetBounds


class EmbeddingProfile(Namespace):
    id: UUID
    revision: NonNegative
    model: str
    dimensions: Annotated[int, Field(ge=1, le=4096, strict=True)]
    space_id: SHA256 | None = (
        None  # Legacy artifacts remain readable, but cannot serve vector queries.
    )


class EmbeddingItem(WireModel):
    index: Annotated[int, Field(ge=0, strict=True)]
    embedding: list[float]


class EmbeddingUsage(WireModel):
    prompt_tokens: Annotated[int, Field(ge=0, le=128000, strict=True)]


class EmbeddingResponse(WireModel):
    model: str
    data: Annotated[list[EmbeddingItem], Field(max_length=16)]
    usage: EmbeddingUsage


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
        budget: CallBudget | None = None,
    ) -> None:
        self.approved, self.connection = approved, connection
        self.budget = budget
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

    async def embed(
        self, texts: list[str], profile: EmbeddingProfile, *, query: bool = False
    ) -> list[list[float]]:
        from tiktoken import get_encoding

        from domain_kg.providers.budget import split_embedding_input

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
        prefix = self.approved.embedding_query_prefix if query else ""
        windows = [
            split_embedding_input(
                text, context_tokens=self.approved.embedding_context_tokens, prefix=prefix
            )
            for text in texts
        ]
        inputs = [prefix + part for parts in windows for part in parts]
        result: list[list[float]] = []
        size = self.approved.embedding_batch_size
        for start in range(0, len(inputs), size):
            batch = inputs[start : start + size]
            await self.authorize()
            result.extend(await self._batch(batch, profile))
            await self.authorize()
        pooled, offset = [], 0
        for parts in windows:
            vectors = result[offset : offset + len(parts)]
            offset += len(parts)
            weights = [len(part.encode()) for part in parts]
            pooled.append(
                list(
                    normalized_vector(
                        [
                            sum(
                                vector[dimension] * weight
                                for vector, weight in zip(vectors, weights, strict=True)
                            )
                            for dimension in range(profile.dimensions)
                        ],
                        profile.dimensions,
                    )
                )
            )
        return pooled

    async def _batch(self, batch: list[str], profile: EmbeddingProfile) -> list[list[float]]:
        bounds = BudgetBounds(
            input_tokens=min(128000, self.approved.embedding_context_tokens * len(batch))
        )
        context = self.budget.call(bounds) if self.budget is not None else nullcontext(None)
        async with context as charge:
            request: dict[str, object] = {
                "input": batch,
                "model": profile.model,
                "encoding_format": "float",
            }
            if self.approved.embedding_send_dimensions:
                request["dimensions"] = profile.dimensions
            try:
                async with asyncio.timeout(self.approved.timeout_seconds):
                    if charge is not None:
                        charge.attempted = True
                    async with self.client.stream(
                        "POST",
                        self.connection.endpoint + "/embeddings",
                        json=request,
                        headers=provider_headers(self.connection),
                    ) as response:
                        body = await provider_json(response, limit=8 * 1024**2)
                raw = EmbeddingResponse.model_validate(body)
                if raw.model != profile.model or len(raw.data) != len(batch):
                    raise unavailable()
                positions: dict[int, tuple[float, ...]] = {}
                for item in raw.data:
                    index = item.index
                    if type(index) is not int or not 0 <= index < len(batch) or index in positions:
                        raise unavailable()
                    positions[index] = normalized_vector(item.embedding, profile.dimensions)
                tokens = raw.usage.prompt_tokens
                used = Usage(
                    org_id=profile.org_id,
                    kg_id=profile.kg_id,
                    profile_id=profile.id,
                    input_tokens=tokens,
                    output_tokens=0,
                )
                if charge is not None:
                    await charge.report(used)
                await self.record_usage(used)
            except (
                httpx.HTTPError,
                TimeoutError,
                ValueError,
                TypeError,
                KeyError,
                IndexError,
            ) as exc:
                raise unavailable() from exc
            return [list(positions[index]) for index in range(len(batch))]
