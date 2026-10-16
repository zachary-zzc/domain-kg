"""Install scoped grant, visibility and policy-command compound indexes."""

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.identity.policy import Policy


async def upgrade(database: AsyncDatabase[dict[str, Any]]) -> None:
    await Policy(database).initialize()
