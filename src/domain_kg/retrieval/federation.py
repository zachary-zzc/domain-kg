"""Bounded fan-out waits for every requested KG before rank selection."""

import asyncio
from collections.abc import Awaitable, Callable

from domain_kg.revisions.models import ReadScope


async def fan_out[T](
    scopes: list[ReadScope], operation: Callable[[ReadScope], Awaitable[T]], *, concurrency: int = 2
) -> list[T]:
    semaphore = asyncio.Semaphore(concurrency)

    async def run(scope: ReadScope) -> T:
        async with semaphore:
            return await operation(scope)

    results = await asyncio.gather(*(run(scope) for scope in scopes), return_exceptions=True)
    values = []
    for result in results:
        if isinstance(result, BaseException):
            raise result
        values.append(result)
    return values
