"""Public domain contracts. Transport and storage implementations depend on these."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from domain_kg.catalog.models import BackendCapabilities as BackendCapabilities
from domain_kg.catalog.models import CreateKG as CreateKG
from domain_kg.catalog.models import KGReceipt as KGReceipt
from domain_kg.catalog.models import StorageProfile as StorageProfile
from domain_kg.graph.models import DomainSchema as DomainSchema
from domain_kg.identity.models import AccessContext as AccessContext
from domain_kg.identity.models import Action as Action
from domain_kg.identity.models import Principal as Principal
from domain_kg.imports.models import ImportPlan as ImportPlan
from domain_kg.imports.models import ImportSource as ImportSource
from domain_kg.ingestion.models import ChunkConfig as ChunkConfig
from domain_kg.ingestion.models import ExtractionBatch as ExtractionBatch
from domain_kg.ingestion.models import ParsedBlock as ParsedBlock
from domain_kg.ingestion.models import ParsedChunk as ParsedChunk
from domain_kg.ingestion.models import ParsedDocument as ParsedDocument
from domain_kg.ingestion.models import ParseOptions as ParseOptions
from domain_kg.jobs.models import JobCommand as JobCommand
from domain_kg.jobs.models import JobReceipt as JobReceipt
from domain_kg.jobs.models import LeasedJob as LeasedJob
from domain_kg.materials.models import AddMaterial as AddMaterial
from domain_kg.materials.models import BlobRef as BlobRef
from domain_kg.materials.models import BlobScope as BlobScope
from domain_kg.materials.models import IngestReceipt as IngestReceipt
from domain_kg.materials.models import MaterialMetadata as MaterialMetadata
from domain_kg.materials.models import RemovalReceipt as RemovalReceipt
from domain_kg.materials.models import RemoveMaterial as RemoveMaterial
from domain_kg.materials.models import ReplaceMaterial as ReplaceMaterial
from domain_kg.materials.models import UploadCommand as UploadCommand
from domain_kg.materials.models import UploadReceipt as UploadReceipt
from domain_kg.materials.purge_models import PurgeMaterial as PurgeMaterial
from domain_kg.materials.purge_models import PurgeReceipt as PurgeReceipt
from domain_kg.models import Namespace, ScopedRef
from domain_kg.models import PageRequest as PageRequest
from domain_kg.models import SourceVersion as SourceVersion
from domain_kg.retrieval.models import EvidenceCandidate as EvidenceCandidate
from domain_kg.retrieval.models import SearchFilters as SearchFilters
from domain_kg.retrieval.models import SearchRequest as SearchRequest
from domain_kg.retrieval.models import SearchResponse as SearchResponse
from domain_kg.retrieval.models import SubgraphRequest as SubgraphRequest
from domain_kg.revisions.models import CurationCommand as CurationCommand
from domain_kg.revisions.models import CurationImpact as CurationImpact
from domain_kg.revisions.models import CurationReceipt as CurationReceipt
from domain_kg.revisions.models import HistoryPage as HistoryPage
from domain_kg.revisions.models import HistoryRequest as HistoryRequest
from domain_kg.revisions.models import NodeUpdate as NodeUpdate
from domain_kg.revisions.models import ProjectionReceipt as ProjectionReceipt
from domain_kg.revisions.models import PublishedView as PublishedView
from domain_kg.revisions.models import PublishRevision as PublishRevision
from domain_kg.revisions.models import ReadinessReport as ReadinessReport
from domain_kg.revisions.models import ReadScope as ReadScope
from domain_kg.revisions.models import RelationUpdate as RelationUpdate
from domain_kg.revisions.models import RevisionBatch as RevisionBatch
from domain_kg.revisions.models import RevisionReceipt as RevisionReceipt
from domain_kg.revisions.models import RollbackRevision as RollbackRevision
from domain_kg.storage.models import MaterialVersionRecord


class ScopedAuthority(Protocol):
    @property
    def namespace(self) -> Namespace: ...

    async def get_material_version(self, reference: ScopedRef) -> MaterialVersionRecord | None: ...


class ScopedProjectionClient(Protocol):
    @property
    def namespace(self) -> Namespace: ...

    async def verify(self, receipt: ProjectionReceipt) -> bool: ...


class ScopedBlobClient(Protocol):
    @property
    def namespace(self) -> Namespace: ...

    async def get(self, reference: BlobRef) -> bytes: ...


@dataclass(frozen=True)
class KGStores:
    namespace: Namespace
    authority: ScopedAuthority
    graph: ScopedProjectionClient
    vectors: ScopedProjectionClient
    blobs: ScopedBlobClient

    def __post_init__(self) -> None:
        if any(
            client.namespace != self.namespace
            for client in (self.authority, self.graph, self.vectors, self.blobs)
        ):
            raise ValueError("all stores must be bound to the same organization and KG")


class Policy(Protocol):
    async def resolve_access(
        self, principal: Principal, kg_ids: list[UUID], action: Action
    ) -> AccessContext: ...


class KGStoreRouter(Protocol):
    async def for_kg(self, access: AccessContext, kg_id: UUID) -> KGStores: ...


class EligibilityResolver(Protocol):
    async def resolve(
        self, access: AccessContext, request: SearchRequest, kg_id: UUID
    ) -> ReadScope: ...


class MaterialService(Protocol):
    async def add(self, access: AccessContext, command: AddMaterial) -> IngestReceipt: ...
    async def replace(self, access: AccessContext, command: ReplaceMaterial) -> IngestReceipt: ...
    async def remove(self, access: AccessContext, command: RemoveMaterial) -> RemovalReceipt: ...
    async def purge(self, access: AccessContext, command: PurgeMaterial) -> PurgeReceipt: ...


class Parser(Protocol):
    async def parse(self, blob: BlobRef, options: ParseOptions) -> ParsedDocument: ...


class Extractor(Protocol):
    async def extract(self, chunk: ParsedChunk, schema: DomainSchema) -> ExtractionBatch: ...


class ProjectionCoordinator(Protocol):
    async def stage(self, access: AccessContext, batch: RevisionBatch) -> ProjectionReceipt: ...
    async def verify(
        self, access: AccessContext, receipt: ProjectionReceipt
    ) -> ReadinessReport: ...
    async def publish(self, access: AccessContext, command: PublishRevision) -> RevisionReceipt: ...


class RevisionService(Protocol):
    async def curate_node(
        self, access: AccessContext, command: CurationCommand
    ) -> CurationReceipt | CurationImpact: ...
    async def get_revisions(
        self, access: AccessContext, request: HistoryRequest
    ) -> HistoryPage: ...
    async def update_node(self, access: AccessContext, command: NodeUpdate) -> RevisionReceipt: ...
    async def update_relation(
        self, access: AccessContext, command: RelationUpdate
    ) -> RevisionReceipt: ...
    async def rollback(
        self, access: AccessContext, command: RollbackRevision
    ) -> RevisionReceipt | CurationReceipt: ...


class RetrievalBranch(Protocol):
    async def retrieve(
        self, scope: ReadScope, request: SearchRequest
    ) -> list[EvidenceCandidate]: ...


class RetrievalService(Protocol):
    async def search(self, access: AccessContext, request: SearchRequest) -> SearchResponse: ...


class Importer(Protocol):
    async def plan(self, access: AccessContext, source: ImportSource) -> ImportPlan: ...
    async def execute(self, access: AccessContext, plan: ImportPlan) -> JobReceipt: ...
