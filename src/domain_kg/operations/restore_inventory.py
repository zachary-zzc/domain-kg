"""Bounded read-only inventory of a completely verified retained package."""

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import AssertionRecord, EntityRecord
from domain_kg.imports.legacy_models import LegacyGraph
from domain_kg.imports.models import ImportPlan
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.ingestion.models import ExtractionBatch
from domain_kg.ingestion.resolved import ResolvedEntity
from domain_kg.jobs.models import Checkpoint, JobArtifact
from domain_kg.models import Namespace, ScopedRef, SourceVersion
from domain_kg.operations.backup_models import AuthoritySection, AuthorityTable, BackupManifest
from domain_kg.operations.package_reader import PackageReader, inconsistent
from domain_kg.operations.packages import PackagePart
from domain_kg.operations.remapping import RestoreRemapper
from domain_kg.operations.restore_documents import RestoreDocuments
from domain_kg.operations.restore_imports import restore_import
from domain_kg.revisions.models import PublishedView
from domain_kg.storage.models import MaterialVersionRecord

MAX_METADATA = 64 * 1024 * 1024


@dataclass(frozen=True)
class RestoreInventory:
    manifest: BackupManifest
    root: PublishedView
    reader: PackageReader
    parts: tuple[PackagePart, ...]
    records: dict[AuthorityTable, list[dict[str, Any]]]

    @classmethod
    async def load(cls, reader: PackageReader, manifest: BackupManifest) -> "RestoreInventory":
        await reader.verify(manifest)
        parts = []
        records: dict[AuthorityTable, list[dict[str, Any]]] = {}
        size = count = 0
        async for page in reader.pages(manifest):
            for part in page.parts:
                parts.append(part)
                if len(parts) > 10000:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "Restore part inventory exceeds its budget."
                    )
                if part.kind == "authority":
                    size += part.object.size_bytes
                    if size > MAX_METADATA:
                        raise DomainError(
                            ErrorCode.QUOTA_EXCEEDED, "Restore metadata exceeds its byte budget."
                        )
                    section = AuthoritySection.model_validate_json(await reader.body(part.object))
                    count += len(section.records)
                    if count > 100000:
                        raise DomainError(
                            ErrorCode.QUOTA_EXCEEDED, "Restore authority exceeds its row budget."
                        )
                    records.setdefault(section.table, []).extend(section.records)
        roots = records.get("published_view", [])
        if len(roots) != 1:
            raise inconsistent()
        return cls(manifest, PublishedView.model_validate(roots[0]), reader, tuple(parts), records)

    def rows(self, table: AuthorityTable) -> list[dict[str, Any]]:
        return self.records.get(table, [])

    def mapping(self, target: Namespace) -> RestoreRemapper:
        versions = tuple(
            MaterialVersionRecord.model_validate(row) for row in self.rows("material_versions")
        )
        sources = tuple(
            SourceVersion(
                org_id=row.org_id, kg_id=row.kg_id, material_id=row.material.id, version_id=row.id
            )
            for row in versions
        )
        graphs = tuple(
            LegacyGraph.model_validate(row["graph"]) for row in self.rows("import_plans")
        )
        curations = tuple(
            ScopedRef(org_id=row["org_id"], kg_id=row["kg_id"], id=row["id"])
            for row in self.rows("curations")
        )
        return RestoreRemapper(
            Namespace(org_id=self.manifest.org_id, kg_id=self.manifest.kg_id),
            target,
            versions=sources,
            curations=curations,
            entities=tuple(EntityRecord.model_validate(row) for row in self.rows("entities")),
            assertions=tuple(
                AssertionRecord.model_validate(row) for row in self.rows("assertions")
            ),
            observations=tuple(
                ResolvedEntity.model_validate(row) for row in self.rows("observations")
            ),
            legacy_graphs=graphs,
        )

    async def documents(self, mapping: RestoreRemapper) -> RestoreDocuments:
        originals = {part.source: part for part in self.parts if part.kind == "original"}
        plans = {str(row["id"]): row for row in self.rows("import_plans")}
        imports = []
        for row in self.rows("import_source_origins"):
            origin = ImportOrigin.model_validate(row)
            source = SourceVersion(
                **{key: getattr(origin, key) for key in SourceVersion.model_fields}
            )
            plan = plans.get(str(origin.plan_id))
            original = originals.get(source)
            if plan is None or original is None:
                raise inconsistent()
            imports.append(
                restore_import(
                    mapping,
                    ImportPlan.model_validate(plan["plan"]),
                    LegacyGraph.model_validate(plan["graph"]),
                    origin,
                    await self.reader.body(original.object, maximum=2 * 1024 * 1024),
                )
            )
        return RestoreDocuments(
            mapping,
            originals=tuple(
                part.original for part in originals.values() if part.original is not None
            ),
            imports=tuple(imports),
        )

    async def artifacts(self) -> tuple[tuple[JobArtifact, Checkpoint, PackagePart], ...]:
        parts = {part.artifact_blob: part for part in self.parts if part.kind == "source_artifact"}
        checkpoints = {
            str(row["artifact_id"]): Checkpoint.model_validate(
                {key: row[key] for key in Checkpoint.model_fields if key in row}
            )
            for row in self.rows("job_checkpoints")
        }
        results = []
        inspected_bytes = 0
        for row in self.rows("job_artifacts"):
            artifact = JobArtifact.model_validate(
                {key: row[key] for key in JobArtifact.model_fields}
            )
            if artifact.stage not in (
                "validating",
                "parsing",
                "chunking",
                "extracting",
                "resolving",
            ):
                continue
            checkpoint, part = checkpoints.get(str(artifact.id)), parts.get(artifact.blob)
            if checkpoint is None or part is None:
                raise inconsistent()
            if artifact.stage == "extracting":
                inspected_bytes += part.object.size_bytes
                if inspected_bytes > MAX_METADATA:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "Restore extraction inventory exceeds its budget."
                    )
                body = await self.reader.body(part.object, maximum=MAX_METADATA)
                try:
                    cached = ExtractionBatch.model_validate_json(body)
                except ValidationError:
                    pass  # The ledger must validate the full canonical stage artifact.
                else:
                    if (cached.org_id, cached.kg_id, cached.material_id, cached.version_id) != (
                        artifact.blob.org_id,
                        artifact.blob.kg_id,
                        artifact.blob.material_id,
                        artifact.source_version_id,
                    ):
                        raise inconsistent()
                    # Per-chunk caches never satisfy a complete-source stage.
                    continue
            results.append((artifact, checkpoint, part))
        order = ("validating", "parsing", "chunking", "extracting", "resolving")
        return tuple(
            sorted(results, key=lambda item: (order.index(item[0].stage), str(item[0].id)))
        )
