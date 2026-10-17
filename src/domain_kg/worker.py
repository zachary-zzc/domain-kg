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
from domain_kg.operations.backup import BackupService
from domain_kg.operations.executor import OwnerJobExecutor
from domain_kg.operations.export import ExportService
from domain_kg.operations.maintenance import MaintenanceService
from domain_kg.operations.restore import RestoreService
from domain_kg.operations.restore_cleanup import RestoreCleanupService
from domain_kg.operations.retention import RetentionService
from domain_kg.providers.profiles import ProviderRegistry
from domain_kg.providers.service import ProviderService
from domain_kg.settings import Settings
from domain_kg.storage.router import KGStoreRouter


async def serve(
    queue: JobQueue,
    runner: JobRunner,
    *,
    once: bool,
    worker_id: str,
    repair_only: bool = False,
    restore_only: bool = False,
    curation_only: bool = False,
    purge_only: bool = False,
    import_only: bool = False,
    backup_only: bool = False,
    export_only: bool = False,
    operations_only: bool = False,
    kg_restore_only: bool = False,
    maintenance_only: bool = False,
    retention_only: bool = False,
    restore_cleanup_only: bool = False,
) -> None:
    runner.pipeline.require_configured()
    while True:
        kinds = (
            ("restore_cleanup",)
            if restore_cleanup_only
            else ("retention",)
            if retention_only
            else ("maintenance",)
            if maintenance_only
            else ("restore",)
            if kg_restore_only
            else ("backup", "export")
            if operations_only
            else ("export",)
            if export_only
            else ("backup",)
            if backup_only
            else ("import",)
            if import_only
            else ("purge_material",)
            if purge_only
            else ("curate",)
            if curation_only
            else ("restore_material",)
            if restore_only
            else ("repair",)
            if repair_only
            else None
        )
        job = await queue.claim(worker_id, kinds=kinds)
        if job is not None:
            await runner.run_job(job)
        if once:
            return
        if job is None:
            await asyncio.sleep(1)


async def main(
    *,
    once: bool,
    repair_only: bool = False,
    restore_only: bool = False,
    curation_only: bool = False,
    purge_only: bool = False,
    import_only: bool = False,
    backup_only: bool = False,
    export_only: bool = False,
    operations_only: bool = False,
    kg_restore_only: bool = False,
    maintenance_only: bool = False,
    retention_only: bool = False,
    restore_cleanup_only: bool = False,
) -> None:
    settings = Settings()
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.mongo_uri.get_secret_value(), maxPoolSize=4, serverSelectionTimeoutMS=5000
    )
    policy = Policy(client[settings.mongo_database])
    registry = BackendRegistry(policy, approved_from_settings(settings))
    router = KGStoreRouter(policy, registry, dimensions=settings.embedding_dimensions)
    try:
        queue = JobQueue(policy, router)
        approved_providers = (
            ProviderRegistry()
            if backup_only
            or export_only
            or operations_only
            or maintenance_only
            or retention_only
            or restore_cleanup_only
            else ProviderRegistry.from_file(settings.provider_config)
        )
        if not approved_providers.profiles and not (
            restore_cleanup_only
            or retention_only
            or maintenance_only
            or repair_only
            or restore_only
            or curation_only
            or purge_only
            or backup_only
            or export_only
            or operations_only
        ):
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Configure approved provider profiles before starting a worker.",
            )
        providers = ProviderService(policy, router, approved_providers)
        runner = JobRunner(
            queue,
            configured_pipeline(queue, providers),
            operations=RestoreCleanupService(queue)
            if restore_cleanup_only
            else RetentionService(queue)
            if retention_only
            else MaintenanceService(queue)
            if maintenance_only
            else RestoreService(queue, providers)
            if kg_restore_only
            else OwnerJobExecutor(queue)
            if operations_only
            else ExportService(queue)
            if export_only
            else BackupService(queue)
            if backup_only
            else None,
        )
        runner.pipeline.require_configured()
        await queue.initialize()
        await serve(
            queue,
            runner,
            once=once,
            worker_id=f"local-{uuid4()}",
            repair_only=repair_only,
            restore_only=restore_only,
            curation_only=curation_only,
            purge_only=purge_only,
            import_only=import_only,
            backup_only=backup_only,
            export_only=export_only,
            operations_only=operations_only,
            kg_restore_only=kg_restore_only,
            maintenance_only=maintenance_only,
            retention_only=retention_only,
            restore_cleanup_only=restore_cleanup_only,
        )
    finally:
        await router.close()
        await client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--restore-cleanup-only",
        action="store_true",
        help="clean permanently fenced inactive restore targets serially without models",
    )
    mode.add_argument(
        "--retention-only",
        action="store_true",
        help="erase explicitly selected expired backups and exports serially without models",
    )
    mode.add_argument(
        "--maintenance-only",
        action="store_true",
        help="repair accepted human projections serially without models",
    )
    mode.add_argument(
        "--kg-restore-only",
        action="store_true",
        help="restore owner-approved backups to fresh KGs with approved target embeddings",
    )
    mode.add_argument(
        "--operations-only",
        action="store_true",
        help="process backups and exports serially without models",
    )
    mode.add_argument(
        "--export-only",
        action="store_true",
        help="produce owner-scoped exports without model calls",
    )
    mode.add_argument(
        "--backup-only", action="store_true", help="seal owner backups without model calls"
    )
    mode.add_argument(
        "--repair-only", action="store_true", help="repair retained projections without a provider"
    )
    mode.add_argument(
        "--restore-only",
        action="store_true",
        help="restore retained material projections without a provider",
    )
    mode.add_argument(
        "--purge-only",
        action="store_true",
        help="clean expired, owner-authorized material data without a provider",
    )
    mode.add_argument(
        "--import-only",
        action="store_true",
        help="process unverified legacy snapshots with approved embeddings only",
    )
    mode.add_argument(
        "--curation-only",
        action="store_true",
        help="process human curations; unchanged embedded text needs no provider",
    )
    args = parser.parse_args()
    try:
        asyncio.run(
            main(
                once=args.once,
                repair_only=args.repair_only,
                restore_only=args.restore_only,
                curation_only=args.curation_only,
                purge_only=args.purge_only,
                import_only=args.import_only,
                backup_only=args.backup_only,
                export_only=args.export_only,
                operations_only=args.operations_only,
                kg_restore_only=args.kg_restore_only,
                maintenance_only=args.maintenance_only,
                retention_only=args.retention_only,
                restore_cleanup_only=args.restore_cleanup_only,
            )
        )
    except DomainError as exc:
        print(f"{exc.code}: {exc.message}", file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        pass
