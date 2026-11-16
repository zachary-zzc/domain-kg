"""A single service composition shared by HTTP, MCP and management screens."""

from uuid import UUID

from domain_kg.catalog.service import CatalogService
from domain_kg.citations.service import CitationService
from domain_kg.graph.service import GraphService
from domain_kg.identity.grants import GrantManagement
from domain_kg.identity.material_grants import MaterialGrantManagement
from domain_kg.identity.models import AccessContext, Action, Principal
from domain_kg.identity.routes import IdentityRuntime
from domain_kg.imports.service import Importer
from domain_kg.ingestion.configuration import ParserSetup
from domain_kg.jobs.inspection import JobInspection
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.import_runs import ImportRuns
from domain_kg.materials.inspection import MaterialInspection
from domain_kg.materials.lifecycle import MaterialLifecycle
from domain_kg.materials.management import MaterialManagement
from domain_kg.materials.service import MaterialService
from domain_kg.operations.batch import BatchOperations
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.metrics import UsageMetrics
from domain_kg.operations.projections import ProjectionManagement
from domain_kg.operations.restore_cleanup_policy import RestoreCleanupPolicy
from domain_kg.operations.restore_management import RestoreManagement
from domain_kg.operations.retention_policy import RetentionPolicy
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.service import RetrievalService
from domain_kg.revisions.service import RevisionService


class ServiceRuntime:
    def __init__(
        self,
        identity: IdentityRuntime,
        catalog: CatalogService,
        materials: MaterialService,
        providers: ProviderService,
        *,
        retention_policy: RetentionPolicy | None = None,
        cleanup_policy: RestoreCleanupPolicy | None = None,
    ) -> None:
        self.identity, self.catalog = identity, catalog
        self.policy, self.materials, self.providers = catalog.policy, materials, providers
        self.queue = JobQueue(self.policy, catalog.router)
        self.parser_setup = ParserSetup(self.policy, identity.settings.parser_config)
        self.retention_policy = retention_policy or RetentionPolicy()
        self.owner_operations = OwnerOperations(self.queue, self.retention_policy, cleanup_policy)
        self.restores = RestoreManagement(self.owner_operations, providers.registry)
        self.job_inspection = JobInspection(self.queue)
        self.inspection = MaterialInspection(self.queue)
        self.import_runs = ImportRuns(materials, self.queue)
        self.management = MaterialManagement(self.queue)
        self.citations = CitationService(self.queue)
        self.lifecycle = MaterialLifecycle(materials, self.queue)
        self.batch = BatchOperations(self.queue, self.lifecycle)
        self.graph = GraphService(self.queue)
        self.search = RetrievalService(self.queue, providers)
        self.revisions = RevisionService(self.queue, providers)
        self.grants = GrantManagement(self.policy)
        self.material_grants = MaterialGrantManagement(self.policy)
        self.projections = ProjectionManagement(self.queue)
        self.usage = UsageMetrics(self.policy, providers.budgets)
        self.imports = Importer(materials, providers)

    async def initialize(self) -> None:
        await self.queue.initialize()
        await self.import_runs.initialize()
        await self.revisions.initialize()
        await self.imports.initialize()

    async def access(
        self,
        principal: Principal,
        kg_ids: tuple[UUID, ...],
        action: Action,
        org_id: UUID | None = None,
    ) -> AccessContext:
        member = await self.identity.repository.require_member(principal, org_id)
        return await self.policy.resolve_access(
            principal, list(kg_ids), action, org_id=member.org_id
        )
