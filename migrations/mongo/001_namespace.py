"""Initial canonical collection validators and compound namespace indexes.

Provisioning invokes this with its approved AsyncDatabase handle. No DSN or
arbitrary collection name is accepted from an API or MCP request.
"""

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.storage.integrity import install_mongo_schema


async def upgrade(database: AsyncDatabase[dict[str, Any]]) -> None:
    await install_mongo_schema(database)
