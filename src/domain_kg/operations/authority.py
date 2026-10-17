"""Bounded authority sections use a server-owned table allowlist and accepted dependencies."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from bson import json_util
from pydantic import JsonValue
from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.schema import schema_for_kg
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.backup_models import AuthoritySection, AuthorityTable
from domain_kg.operations.capture import BackupCapture
from domain_kg.revisions.models import PublishedView


@dataclass(frozen=True)
class AuthoritySelection:
    material_ids: tuple[str, ...]
    version_ids: tuple[str, ...]
    job_ids: tuple[str, ...]
    curation_ids: tuple[str, ...]
    manifest_ids: tuple[str, ...]


class AuthorityCapture:
    def __init__(self, queue: JobQueue, job: LeasedJob, view: PublishedView) -> None:
        self.queue, self.job, self.view = queue, job, view
        self.capture = BackupCapture(queue)
        self.ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}

    async def selection(self) -> AuthoritySelection:
        materials = tuple(sorted({str(source.material_id) for source in self.view.sources}))
        attempts = (
            await self.queue.database.projection_attempts.find(
                self.ns
                | {
                    "state": "accepted",
                    "source.material_id": {"$in": list(materials)},
                    "receipt.published_revision": {"$lte": self.view.revision},
                }
            )
            .limit(10001)
            .to_list()
        )
        curations = (
            await self.queue.database.curation_revisions.find(self.ns | {"state": "accepted"})
            .limit(10001)
            .to_list()
        )
        if len(attempts) > 10000 or len(curations) > 10000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Backup history exceeds its record budget.")
        versions = {str(source.version_id) for source in self.view.sources}
        jobs = set()
        for row in attempts:
            if row.get("generation_origin"):
                from domain_kg.operations.generation_results import generation_artifact_job

                original = await generation_artifact_job(
                    self.queue, await self.capture.guard(self.job), row
                )
                jobs.add(str(original.id))
            else:
                jobs.add(row["job"]["id"])
        for row in attempts:
            if row.get("source"):
                versions.add(row["source"]["version_id"])
        jobs.update(row["job_id"] for row in curations if row.get("job_id"))
        manual_attempts = (
            await self.queue.database.projection_attempts.find(
                self.ns
                | {
                    "state": "accepted",
                    "curation.id": {"$in": [row["id"] for row in curations]},
                    "receipt.published_revision": {"$lte": self.view.revision},
                }
            )
            .limit(10001)
            .to_list()
        )
        if len(manual_attempts) > 10000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Backup history exceeds its record budget.")
        return AuthoritySelection(
            materials,
            tuple(sorted(versions)),
            tuple(sorted(jobs)),
            tuple(sorted(row["id"] for row in curations)),
            tuple(sorted({row["id"] for row in (*attempts, *manual_attempts)})),
        )

    async def sections(self, selected: AuthoritySelection) -> AsyncIterator[AuthoritySection]:
        access = await self.capture.guard(self.job)
        stores = await self.queue.router.for_sources(access, self.job.kg_id)
        if await stores.authority.reconcile():
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Authority references need repair."
            )
        kg = await self.queue.database.kgs.find_one(
            {"org_id": self.ns["org_id"], "id": self.ns["kg_id"]}
        )
        if kg is None:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Backup KG metadata is missing.")
        fields = {
            "id",
            "name",
            "state",
            "revision",
            "domain_profile_id",
            "domain_schema",
            "schema",
            "generation",
            "published_revision",
            "visibility_revision",
            "identity_sequence",
            "published_manifest_id",
            "configuration_epoch",
        }
        metadata = {key: value for key, value in kg.items() if key in fields} | self.ns
        yield self._section("control", "kg_metadata", [metadata])
        schema = await schema_for_kg(self.queue.policy, access, self.job.kg_id)
        yield self._section("control", "kg_schemas", [schema.model_dump(mode="json")])
        yield self._section("control", "published_view", [self.view.model_dump(mode="json")])
        control: tuple[tuple[AuthorityTable, dict[str, Any]], ...] = (
            ("material_access", {"id": {"$in": list(selected.material_ids)}}),
            ("curation_revisions", {"state": "accepted"}),
            ("curation_access", {"id": {"$in": list(selected.curation_ids)}}),
            ("curation_targets", {}),
            ("curation_scopes", {}),
            ("node_visibility", {}),
            ("jobs", {"id": {"$in": list(selected.job_ids)}}),
            ("job_artifacts", {"job_id": {"$in": list(selected.job_ids)}}),
            ("job_checkpoints", {"job_id": {"$in": list(selected.job_ids)}}),
            ("operation_artifacts", {"job_id": {"$in": list(selected.job_ids)}}),
            (
                "projection_attempts",
                {"id": {"$in": list(selected.manifest_ids)}, "state": "accepted"},
            ),
            ("projection_record_refs", {"manifest_id": {"$in": list(selected.manifest_ids)}}),
            (
                "publication_manifests",
                {"state": "accepted", "revision": {"$lte": self.view.revision}},
            ),
            ("publication_bindings", {}),
            ("import_source_origins", {"version_id": {"$in": list(selected.version_ids)}}),
            (
                "import_plans",
                {"state": "admitted", "source.version_id": {"$in": list(selected.version_ids)}},
            ),
        )
        authority: tuple[tuple[AuthorityTable, dict[str, Any]], ...] = (
            ("materials", {"id": {"$in": list(selected.material_ids)}}),
            ("material_versions", {"id": {"$in": list(selected.version_ids)}}),
            ("entities", {}),
            ("assertions", {}),
            (
                "supports",
                {
                    "$or": [
                        {"version.id": {"$in": list(selected.version_ids)}},
                        {"curation.id": {"$in": list(selected.curation_ids)}},
                    ]
                },
            ),
            ("curations", {"id": {"$in": list(selected.curation_ids)}}),
            (
                "version_blobs",
                {
                    "id": {"$in": list(selected.version_ids)},
                    "state": {"$in": ["accepted", "retained"]},
                },
            ),
            *(
                (table, {"version_id": {"$in": list(selected.version_ids)}})
                for table in (
                    "source_blocks",
                    "source_chunks",
                    "observations",
                    "engine_names",
                    "import_id_mappings",
                    "import_assertion_mappings",
                )
            ),
        )
        for area, database, tables in (
            ("control", self.queue.database, control),
            ("authority", stores.authority._database, authority),
        ):
            async for section in self._tables(area, database, tables):
                yield section

    def _section(
        self, area: str, table: AuthorityTable, rows: list[dict[str, Any]]
    ) -> AuthoritySection:
        records = [
            cast(
                dict[str, JsonValue],
                json.loads(
                    json_util.dumps({key: value for key, value in row.items() if key != "_id"})
                ),
            )
            for row in rows
        ]
        return AuthoritySection.model_validate(
            self.ns
            | {"backup_id": self.job.target_id, "area": area, "table": table, "records": records}
        )

    async def _tables(
        self,
        area: str,
        database: AsyncDatabase[dict[str, Any]],
        tables: tuple[tuple[AuthorityTable, dict[str, Any]], ...],
    ) -> AsyncIterator[AuthoritySection]:
        count = 0
        for table, query in tables:
            rows: list[dict[str, Any]] = []
            size = 0
            async for raw in database[table].find(self.ns | query).batch_size(100):
                await self.capture.guard(self.job)
                count += 1
                if count > 100000:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "Backup authority record budget exceeded."
                    )
                row = {key: value for key, value in raw.items() if key != "_id"}
                if table == "jobs":
                    job_fields = {
                        "org_id",
                        "kg_id",
                        "id",
                        "kind",
                        "target_id",
                        "material_id",
                        "material_ids",
                        "actor",
                        "idempotency_key",
                        "created_at",
                        "import_plan_id",
                        "import_plan_hash",
                    }
                    row = {key: value for key, value in row.items() if key in job_fields} | {
                        "stage": "ready"
                    }
                elif table == "projection_attempts":
                    if row.get("generation_origin"):
                        from domain_kg.operations.generation_results import generation_artifact_job

                        original = await generation_artifact_job(
                            self.queue, await self.capture.guard(self.job), row
                        )
                        row["job"] = original.model_dump(mode="json")
                        row["generation_provenance"] = row.pop("generation_origin")
                    elif row.get("maintenance_origin"):
                        from domain_kg.operations.maintenance_results import (
                            maintenance_artifact_job,
                        )

                        original = await maintenance_artifact_job(
                            self.queue, await self.capture.guard(self.job), row
                        )
                        row["job"] = original.model_dump(mode="json")
                        row["maintenance_provenance"] = row.pop("maintenance_origin")
                    # Retain historical identity, never a reusable worker/publication lease.
                    row.pop("lease", None)
                    row["job"] = {
                        key: value
                        for key, value in row["job"].items()
                        if key not in {"lease_token", "lease_until", "fence"}
                    }
                row_size = len(json_util.dumps(row).encode())
                if rows and (len(rows) >= 100 or size + row_size > 900000):
                    yield self._section(area, table, rows)
                    rows, size = [], 0
                rows.append(row)
                size += row_size
            if rows:
                yield self._section(area, table, rows)
