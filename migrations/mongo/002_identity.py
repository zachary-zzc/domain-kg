"""Identity authority validators and issuer/subject/organization compound indexes."""

from typing import Any

from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.identity.repository import IdentityRepository
from domain_kg.identity.sessions import SessionStore


async def upgrade(database: AsyncDatabase[dict[str, Any]]) -> None:
    identities = IdentityRepository(database)
    await identities.initialize()
    await SessionStore(database, identities).initialize()
