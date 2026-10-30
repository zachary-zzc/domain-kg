"""Retry absent-row insert races in MongoDB's authoritative transactions."""

from collections.abc import Awaitable, Callable
from typing import Any

from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError


async def transaction[T](
    database: AsyncDatabase[dict[str, Any]], work: Callable[[AsyncClientSession], Awaitable[T]]
) -> T:
    for attempt in range(3):
        try:
            async with database.client.start_session() as session:
                return await session.with_transaction(work)
        except DuplicateKeyError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")
