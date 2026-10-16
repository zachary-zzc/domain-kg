"""Explicit direct transport and version checks for approved Qdrant backends."""

import math
from importlib.metadata import version
from typing import TYPE_CHECKING, Any

import httpx
from qdrant_client import AsyncQdrantClient, models
from qdrant_client.common.version_check import is_compatible

from domain_kg.errors import DomainError, ErrorCode

if TYPE_CHECKING:
    from domain_kg.storage.router import VectorBinding


async def open_qdrant(
    *, url: str, api_key: str, timeout_seconds: int = 5, limits: httpx.Limits | None = None
) -> AsyncQdrantClient:
    # SDK 1.19.1's background checker uses a separate httpx.get that ignores
    # trust_env=False. Perform the same compatibility check on our own client.
    client = AsyncQdrantClient(
        url=url,
        api_key=api_key,
        timeout=timeout_seconds,
        trust_env=False,
        check_compatibility=False,
        limits=limits or httpx.Limits(max_connections=2, max_keepalive_connections=1),
    )
    try:
        info = await client.info()
        if not is_compatible(version("qdrant-client"), info.version):
            raise ValueError("incompatible backend")
    except Exception as exc:
        await client.close()
        raise DomainError(
            ErrorCode.BACKEND_UNAVAILABLE,
            "The approved vector backend failed its version check.",
            retryable=True,
        ) from exc
    return client


class VectorProjection:
    def __init__(self, binding: "VectorBinding") -> None:
        self.binding = binding

    async def write(self, rows: list[dict[str, Any]], vectors: list[list[float]]) -> None:
        from domain_kg.errors import inaccessible_reference

        if len(rows) != len(vectors) or any(
            (row["org_id"], row["kg_id"])
            != (str(self.binding.namespace.org_id), str(self.binding.namespace.kg_id))
            for row in rows
        ):
            raise inaccessible_reference()
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            result = await self.binding.client.upsert(
                self.binding.collection,
                wait=True,
                update_mode=models.UpdateMode.INSERT_ONLY,
                points=[
                    models.PointStruct(id=row["projection_id"], vector=vector, payload=row)
                    for row, vector in zip(batch, vectors[start : start + 100], strict=True)
                ],
            )
            if result.status != models.UpdateStatus.COMPLETED:
                raise DomainError(
                    ErrorCode.REVISION_NOT_READY, "Vector writes are not complete.", retryable=True
                )

    async def verify(self, rows: list[dict[str, Any]], vectors: list[list[float]]) -> bool:
        from domain_kg.errors import inaccessible_reference

        if any(
            (row["org_id"], row["kg_id"])
            != (str(self.binding.namespace.org_id), str(self.binding.namespace.kg_id))
            for row in rows
        ):
            raise inaccessible_reference()
        if len(rows) != len(vectors):
            return False
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            points = await self.binding.client.retrieve(
                self.binding.collection,
                ids=[row["projection_id"] for row in batch],
                with_payload=True,
                with_vectors=True,
            )
            by_id = {str(point.id): point for point in points}
            for row, vector in zip(batch, vectors[start : start + 100], strict=True):
                point = by_id.get(row["projection_id"])
                if point is None or point.payload != row or not isinstance(point.vector, list):
                    return False
                actual = point.vector
                if len(actual) != len(vector) or any(
                    not isinstance(x, (float, int))
                    or not math.isfinite(x)
                    or not math.isclose(x, expected, rel_tol=2e-5, abs_tol=2e-6)
                    for x, expected in zip(actual, vector, strict=True)
                ):
                    return False
        return True
