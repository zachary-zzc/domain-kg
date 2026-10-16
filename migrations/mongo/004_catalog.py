"""Idempotent catalog and approved placement indexes on the authority replica set."""

from domain_kg.catalog.backends import BackendRegistry
from domain_kg.catalog.repository import CatalogRepository


async def upgrade(registry: BackendRegistry) -> None:
    await registry.initialize()
    await CatalogRepository(registry.policy, registry).initialize()
