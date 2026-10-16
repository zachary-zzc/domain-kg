"""Explicit direct transport and version checks for approved Qdrant backends."""

from importlib.metadata import version

import httpx
from qdrant_client import AsyncQdrantClient
from qdrant_client.common.version_check import is_compatible

from domain_kg.errors import DomainError, ErrorCode


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
