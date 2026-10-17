"""Stage typed source ledgers and private material metadata in an inactive target."""

import hashlib
import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid5

from bson import json_util
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.engine.source_mapping import engine_name
from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import DomainSchema
from domain_kg.identity.models import Principal
from domain_kg.ingestion.models import ParsedChunk, ParsedDocument
from domain_kg.ingestion.resolved import ResolvedDocument
from domain_kg.jobs.models import JobCommand, LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import FrozenModel, ScopedRef, SourceVersion
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_materials import RestoreMaterials
from domain_kg.operations.restore_preparation import PreparedRestore
from domain_kg.providers.service import ProviderService

Row = tuple[str, str, dict[str, Any]]


def decoded(row: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json_util.loads(json.dumps(row))
    return result


def mongo_record(value: FrozenModel) -> dict[str, Any]:
    result = value.model_dump(mode="json", exclude_none=True)
    for name in type(value).model_fields:
        item = getattr(value, name)
        if isinstance(item, datetime):
            result[name] = item
    return result


class RestoreControl(RestoreAuthority):
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        super().__init__(queue, providers)

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restored source ledger differs.")

    async def rows(self, staged: PreparedRestore) -> tuple[Row, ...]:
        inventory = staged.inventory
        mapping = inventory.mapping(staged.binding.target.target)
        documents = await inventory.documents(mapping)
        if documents.imports:
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT,
                "Import admission history requires its dedicated restore stage.",
            )
        materials = RestoreMaterials(documents)
        rows: list[Row] = []
        target = mapping.target.model_dump(mode="json")
        current = {str(source.material_id): source for source in inventory.root.sources}
        for raw in inventory.rows("material_access"):
            source = current.get(str(raw["id"]))
            if source is None:
                raise self.invalid()
            rows.append(
                ("control", "material_access", mongo_record(materials.access(decoded(raw), source)))
            )
        for raw in inventory.rows("version_blobs"):
            rows.append(
                ("authority", "version_blobs", mongo_record(materials.version(decoded(raw))))
            )
        for raw in inventory.rows("kg_schemas"):
            schema = DomainSchema.model_validate(raw)
            rows.append(("control", "kg_schemas", documents.schema(schema).model_dump(mode="json")))
        for raw in inventory.rows("jobs"):
            command = JobCommand.model_validate(
                {key: raw[key] for key in JobCommand.model_fields if key in raw}
            )
            if (command.org_id, command.kg_id) != (mapping.origin.org_id, mapping.origin.kg_id):
                raise self.invalid()
            actor = Principal.model_validate(raw["actor"])
            if command.kind == "curate":
                mapping.curation_ref(ScopedRef(**mapping.origin.model_dump(), id=command.target_id))
            else:
                mapping.source(
                    SourceVersion(
                        **mapping.origin.model_dump(),
                        material_id=raw["material_id"],
                        version_id=command.target_id,
                    )
                )
            value = (
                command.model_dump(mode="json")
                | target
                | {
                    "actor": {
                        key: raw["actor"].get(key, actor.model_dump(mode="json")[key])
                        for key in Principal.model_fields
                    },
                    "stage": "ready",
                    "created_at": decoded(raw)["created_at"],
                }
            )
            for name in ("material_id", "material_ids"):
                if name in raw:
                    value[name] = raw[name]
            rows.append(("control", "jobs", value))
        for rewritten in staged.artifacts:
            rows.append(("control", "job_artifacts", rewritten.artifact.model_dump(mode="json")))
            rows.append(
                ("control", "job_checkpoints", rewritten.checkpoint.model_dump(mode="json"))
            )
        chunks: dict[UUID, ParsedChunk] = {}
        expected: dict[str, dict[str, dict[str, Any]]] = {"source_blocks": {}, "source_chunks": {}}
        for rewritten in staged.artifacts:
            if rewritten.checkpoint.stage not in ("parsing", "chunking"):
                continue
            document = ParsedDocument.model_validate_json(rewritten.body)
            table = "source_blocks" if rewritten.checkpoint.stage == "parsing" else "source_chunks"
            for item in document.blocks if table == "source_blocks" else document.chunks:
                value = item.model_dump(mode="json")
                if table == "source_blocks":
                    value.pop("text")
                elif isinstance(item, ParsedChunk):
                    chunks[item.id] = item
                else:
                    raise self.invalid()
                value.update(
                    text_sha256=hashlib.sha256(item.text.encode()).hexdigest(),
                    text_size_bytes=len(item.text.encode()),
                    parser_version=document.parser_version,
                    artifact_id=str(rewritten.artifact.id),
                )
                expected[table][str(item.id)] = value
        tables: tuple[Literal["source_blocks", "source_chunks"], ...] = (
            "source_blocks",
            "source_chunks",
        )
        for table in tables:
            original = {str(row["id"]): row for row in inventory.rows(table)}
            if set(original) != set(expected[table]):
                raise self.invalid()
            for identifier, value in expected[table].items():
                old = value | {
                    "org_id": str(mapping.origin.org_id),
                    "kg_id": str(mapping.origin.kg_id),
                }
                if any(original[identifier].get(key) != data for key, data in old.items()):
                    raise self.invalid()
                rows.append(("authority", table, value))
        for raw in inventory.rows("observations"):
            from domain_kg.ingestion.resolved import ResolvedEntity

            rows.append(
                (
                    "authority",
                    "observations",
                    mapping.observation(ResolvedEntity.model_validate(raw)).model_dump(mode="json"),
                )
            )
        for rewritten in staged.artifacts:
            if rewritten.checkpoint.stage != "resolving":
                continue
            resolved = ResolvedDocument.model_validate_json(rewritten.body)
            by_chunk = {batch.chunk_id: batch for batch in resolved.extraction.batches}
            for batch in resolved.batches:
                chunk = chunks.get(batch.chunk_id)
                extraction = by_chunk.get(batch.chunk_id)
                if chunk is None or extraction is None:
                    raise self.invalid()
                for proposal, entity in zip(extraction.entities, batch.entities, strict=True):
                    rows.append(
                        (
                            "authority",
                            "engine_names",
                            target
                            | {
                                "id": str(uuid5(batch.extraction_id, proposal.local_id)),
                                "extraction_id": str(batch.extraction_id),
                                "engine_key": engine_name(chunk, proposal.local_id),
                                "entity": entity.entity.model_dump(mode="json"),
                                "version_id": str(batch.version_id),
                                "chunk_id": str(batch.chunk_id),
                            },
                        )
                    )
        return tuple(rows)

    async def stage(self, job: LeasedJob, staged: PreparedRestore) -> None:
        if await self.bindings.approved(job) != (staged.binding.target, staged.binding.embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
        records = await self.rows(staged)
        for start in range(0, len(records), 100):
            batch = records[start : start + 100]

            async def write(session: AsyncClientSession, batch: tuple[Row, ...] = batch) -> None:
                await self._guard(job, staged, session)
                for area, table, value in batch:
                    ns = {
                        "org_id": str(staged.binding.target.target.org_id),
                        "kg_id": str(staged.binding.target.target.kg_id),
                    }
                    if any(value.get(key) != item for key, item in ns.items()):
                        raise self.invalid()
                    query = ns | (
                        {"id": value["id"]}
                        if "id" in value
                        else {key: value[key] for key in ("job_id", "stage", "input_hash")}
                    )
                    database = (
                        self.queue.database
                        if area == "control"
                        else staged.binding.stores.authority._database
                    )
                    previous = await database[table].find_one(query, session=session)
                    if previous is None:
                        await database[table].insert_one(dict(value), session=session)
                    elif json_util.dumps(
                        {k: v for k, v in previous.items() if k != "_id"}, sort_keys=True
                    ) != json_util.dumps(value, sort_keys=True):
                        raise DomainError(
                            ErrorCode.VERSION_CONFLICT, "Restored source metadata changed."
                        )

            await self.queue.transactions.transaction(write)
        if await self.bindings.approved(job) != (staged.binding.target, staged.binding.embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
