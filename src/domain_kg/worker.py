"""Native single-job worker; no extra container or local model is started."""

import argparse
import asyncio
import sys
from typing import Any
from uuid import uuid4

from pymongo import AsyncMongoClient

from domain_kg.catalog.backends import BackendRegistry, approved_from_settings
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.policy import Policy
from domain_kg.ingestion.pipeline import configured_pipeline
from domain_kg.jobs.queue import JobQueue
from domain_kg.jobs.runner import JobRunner
from domain_kg.providers.profiles import ProviderRegistry
from domain_kg.providers.service import ProviderService
from domain_kg.settings import Settings
from domain_kg.storage.router import KGStoreRouter


async def serve(
    queue: JobQueue, runner: JobRunner, *, once: bool, worker_id: str, repair_only: bool = False
) -> None:
    runner.pipeline.require_configured()
    while True:
        job = (
            await queue.claim(worker_id, kinds=("repair",))
            if repair_only
            else await queue.claim(worker_id)
        )
        if job is not None:
            await runner.run_job(job)
        if once:
            return
        if job is None:
            await asyncio.sleep(1)


async def main(*, once: bool, repair_only: bool = False) -> None:
    settings = Settings()
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.mongo_uri.get_secret_value(), maxPoolSize=4, serverSelectionTimeoutMS=5000
    )
    policy = Policy(client[settings.mongo_database])
    registry = BackendRegistry(policy, approved_from_settings(settings))
    router = KGStoreRouter(policy, registry, dimensions=1536)
    try:
        queue = JobQueue(policy, router)
        approved_providers = ProviderRegistry.from_file(settings.provider_config)
        if not approved_providers.profiles and not repair_only:
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Configure approved provider profiles before starting a worker.",
            )
        providers = ProviderService(policy, router, approved_providers)
        runner = JobRunner(queue, configured_pipeline(queue, providers))
        runner.pipeline.require_configured()
        await queue.initialize()
        await serve(queue, runner, once=once, worker_id=f"local-{uuid4()}", repair_only=repair_only)
    finally:
        await router.close()
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument(
        "--repair-only", action="store_true", help="repair retained projections without a provider"
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(once=args.once, repair_only=args.repair_only))
    except DomainError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        pass
