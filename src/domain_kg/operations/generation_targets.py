"""Bounded immutable rebuild inputs; no backend configuration is supplied by a caller."""

from typing import Annotated, Any, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.catalog.models import StorageProfile
from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.schema import schema_for_kg
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import SHA256, NonNegative, ScopedRef
from domain_kg.operations.models import GenerationCommand, OwnerOperation
from domain_kg.revisions.curation_publication import directive
from domain_kg.revisions.projections import AcceptedProjection, digest

MAX_BINDINGS = 1000


def invalid() -> DomainError:
    return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Generation input ownership differs.")


def material_hash(row: dict[str, Any]) -> str:
    return digest(
        {
            key: row.get(key)
            for key in (
                "org_id",
                "kg_id",
                "id",
                "revision",
                "metadata_epoch",
                "active_version_id",
                "acl_mode",
                "member_ids",
                "group_ids",
                "excluded",
                "tombstoned",
                "purged",
                "purge_state",
            )
        }
    )


class GenerationDependency(ScopedRef):
    access_hash: SHA256


class GenerationInput(ScopedRef):
    operation_id: UUID
    owner_key: str
    parent: AcceptedProjection
    base: AcceptedProjection
    source_access_hash: SHA256 | None = None
    curation_hash: SHA256 | None = None
    dependencies: Annotated[tuple[GenerationDependency, ...], Field(max_length=100)] = ()

    @model_validator(mode="after")
    def exact_parent(self) -> Self:
        if (
            any(
                (value.org_id, value.kg_id) != (self.org_id, self.kg_id)
                for value in (self.parent, self.base)
            )
            or (
                (self.parent.source is None) != (self.source_access_hash is None)
                or (self.parent.curation is None) != (self.curation_hash is None)
                or any(
                    getattr(self.parent, key) != getattr(self.base, key)
                    for key in (
                        "source",
                        "curation",
                        "content_hash",
                        "chunk_artifact_id",
                        "import_origin",
                    )
                )
            )
            or (self.dependencies and self.parent.curation is None)
            or len({value.id for value in self.dependencies}) != len(self.dependencies)
            or any(
                (value.org_id, value.kg_id) != (self.org_id, self.kg_id)
                for value in self.dependencies
            )
        ):
            raise ValueError("generation input requires its same-KG owner and material fence")
        return self


def serialized_input(item: GenerationInput) -> dict[str, Any]:
    value = item.model_dump(mode="json")
    # Preserve hashes of previously accepted rebuilds without dependency fences.
    if not item.dependencies:
        value.pop("dependencies")
    return value


class GenerationTarget(ScopedRef):
    job: ScopedRef
    generation: UUID
    parent_generation: UUID
    parent_manifest_id: UUID | None
    binding_count: NonNegative
    inputs_hash: SHA256
    configuration_hash: SHA256
    profile: StorageProfile

    @model_validator(mode="after")
    def scoped_target(self) -> Self:
        if (self.job.org_id, self.job.kg_id) != (self.org_id, self.kg_id) or (
            self.generation == self.parent_generation or self.binding_count > MAX_BINDINGS
        ):
            raise ValueError("rebuild must use a fresh bounded generation in its exact KG")
        return self


class GenerationTargets:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def initialize(self) -> None:
        for name in ("generation_targets", "generation_inputs", "generation_results"):
            await self.database[name].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )
        await self.database.generation_inputs.create_index(
            [("org_id", 1), ("kg_id", 1), ("operation_id", 1), ("owner_key", 1)], unique=True
        )

    async def configuration(
        self,
        access: AccessContext,
        operation: OwnerOperation,
        session: AsyncClientSession | None = None,
    ) -> tuple[StorageProfile, str]:
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        kg = await self.database.kgs.find_one(
            {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
        )
        if kg is None:
            raise inaccessible_reference()
        profile = await self.queue.router.registry.profile(
            UUID(kg["storage_profile_id"]), operation.org_id
        )
        selected = await self.database.kg_provider_settings.find_one(ns, session=session)
        schema = await schema_for_kg(self.queue.policy, access, operation.kg_id, session=session)
        return profile, digest(
            {
                "placement": profile.model_dump(mode="json"),
                "selection": {
                    key: value for key, value in (selected or {}).items() if key != "_id"
                },
                "schema": schema.model_dump(mode="json"),
            }
        )

    async def allocate(
        self,
        access: AccessContext,
        operation: OwnerOperation,
        session: AsyncClientSession,
    ) -> GenerationTarget:
        if not isinstance(operation.command, GenerationCommand):
            raise inaccessible_reference()
        ns = {"org_id": str(operation.org_id), "kg_id": str(operation.kg_id)}
        profile, config_hash = await self.configuration(access, operation, session)
        rows = (
            await self.database.publication_bindings.find(
                ns | {"view_manifest_id": str(operation.snapshot.manifest_id)}, session=session
            )
            .sort("owner_key", 1)
            .limit(MAX_BINDINGS + 1)
            .to_list()
        )
        if len(rows) > MAX_BINDINGS:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Generation exceeds its binding budget.")
        if operation.snapshot.manifest_id is not None:
            root = await self.database.publication_manifests.find_one(
                ns | {"id": str(operation.snapshot.manifest_id), "state": "accepted"},
                session=session,
            )
            if root is None or root.get("binding_count") != len(rows):
                raise invalid()
        inputs = []
        for row in rows:
            parent = AcceptedProjection.model_validate(row["binding"])
            if (parent.org_id, parent.kg_id, parent.generation) != (
                operation.org_id,
                operation.kg_id,
                operation.snapshot.generation,
            ):
                raise invalid()
            fence = None
            curation_hash = None
            dependencies = []
            if parent.source:
                material = await self.database.material_access.find_one(
                    ns | {"id": str(parent.source.material_id)}, session=session
                )
                if material is None:
                    raise invalid()
                if material.get("purged") or material.get("purge_state") in ("pending", "purging"):
                    raise DomainError(
                        ErrorCode.REVISION_NOT_READY,
                        "Purged source retirement must be verified before this rebuild.",
                    )
                fence = material_hash(material)
            else:
                if parent.curation is None:
                    raise invalid()
                record = await self.database.curation_revisions.find_one(
                    ns | {"id": str(parent.curation.id), "state": "accepted"},
                    session=session,
                )
                if record is None:
                    raise invalid()
                for identifier in sorted(record["material_ids"]):
                    material = await self.database.material_access.find_one(
                        ns | {"id": identifier}, session=session
                    )
                    if material is None:
                        raise invalid()
                    if material.get("purge_state") in ("pending", "purging", "detached"):
                        raise DomainError(
                            ErrorCode.REVISION_NOT_READY, "Curation source retirement is pending."
                        )
                    if material.get("purged") and not (
                        material.get("tombstoned") is True
                        and material.get("purge_state") == "purged"
                        and material.get("active_version_id") is None
                    ):
                        raise DomainError(
                            ErrorCode.REVISION_NOT_READY,
                            "Curation source retirement is incomplete.",
                        )
                    dependencies.append(
                        GenerationDependency(
                            org_id=operation.org_id,
                            kg_id=operation.kg_id,
                            id=identifier,
                            access_hash=material_hash(material),
                        )
                    )
                curation_hash = digest(directive(record).model_dump(mode="json"))
            from domain_kg.operations.generation_results import artifact_base_binding

            base = await artifact_base_binding(self.queue, access, parent)
            inputs.append(
                GenerationInput(
                    org_id=operation.org_id,
                    kg_id=operation.kg_id,
                    id=uuid4(),
                    operation_id=operation.id,
                    owner_key=row["owner_key"],
                    parent=parent,
                    base=base,
                    source_access_hash=fence,
                    curation_hash=curation_hash,
                    dependencies=tuple(dependencies),
                )
            )
        serialized = [serialized_input(item) for item in inputs]
        target = GenerationTarget(
            org_id=operation.org_id,
            kg_id=operation.kg_id,
            id=operation.id,
            job=operation.job,
            generation=uuid4(),
            parent_generation=operation.snapshot.generation,
            parent_manifest_id=operation.snapshot.manifest_id,
            binding_count=len(inputs),
            inputs_hash=digest(serialized),
            configuration_hash=config_hash,
            profile=profile,
        )
        if (
            len(target.model_dump_json().encode())
            + sum(len(item.model_dump_json().encode()) for item in inputs)
            > 8 * 1024 * 1024
        ):
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Generation input exceeds its byte budget.")
        await self.database.generation_targets.insert_one(
            target.model_dump(mode="json"), session=session
        )
        if inputs:
            await self.database.generation_inputs.insert_many(serialized, session=session)
        return target

    async def record(
        self, operation: OwnerOperation, session: AsyncClientSession | None = None
    ) -> GenerationTarget:
        if not isinstance(operation.command, GenerationCommand):
            raise inaccessible_reference()
        raw = await self.database.generation_targets.find_one(
            {
                "org_id": str(operation.org_id),
                "kg_id": str(operation.kg_id),
                "id": str(operation.id),
            },
            session=session,
        )
        if raw is None:
            raise invalid()
        target = GenerationTarget.model_validate(
            {key: raw[key] for key in GenerationTarget.model_fields}
        )
        if target.job != operation.job or (target.parent_generation, target.parent_manifest_id) != (
            operation.snapshot.generation,
            operation.snapshot.manifest_id,
        ):
            raise invalid()
        return target

    async def inputs(
        self, target: GenerationTarget, session: AsyncClientSession | None = None
    ) -> tuple[GenerationInput, ...]:
        rows = (
            await self.database.generation_inputs.find(
                {
                    "org_id": str(target.org_id),
                    "kg_id": str(target.kg_id),
                    "operation_id": str(target.id),
                },
                session=session,
            )
            .sort("owner_key", 1)
            .limit(MAX_BINDINGS + 1)
            .to_list()
        )
        values = tuple(
            GenerationInput.model_validate(
                {key: row[key] for key in GenerationInput.model_fields if key in row}
            )
            for row in rows
        )
        if (
            len(values) != target.binding_count
            or digest([serialized_input(item) for item in values]) != target.inputs_hash
        ):
            raise invalid()
        return values
