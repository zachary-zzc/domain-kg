"""Mongo acceptance metadata; projection presence can never establish acceptance."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.visibility_models import NodeFence
from domain_kg.identity.models import AccessContext
from domain_kg.imports.provenance import check_import_origin
from domain_kg.jobs.models import Checkpoint, JobArtifact
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.models import BlobRef, MaterialMetadata, MaterialReadPermit
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.revisions.models import ProjectionReceipt, PublishedView, RetainedSelection
from domain_kg.revisions.projections import AcceptedProjection
from domain_kg.storage.blobs import BlobStore

if TYPE_CHECKING:
    from domain_kg.operations.capture import BackupReadPermit


async def read_artifact(
    queue: JobQueue,
    access: AccessContext,
    checkpoint: Checkpoint,
    source: SourceVersion,
    *,
    restoration: MaterialReadPermit | None = None,
    backup: "BackupReadPermit | None" = None,
) -> bytes:
    """Read immutable artifacts under current permission, independent of the original actor."""
    await queue.policy.revalidate(access)
    if (checkpoint.org_id, checkpoint.kg_id) != (
        source.org_id,
        source.kg_id,
    ) or source.org_id != access.org_id:
        raise inaccessible_reference()

    async def authorize() -> None:
        if backup is not None:
            if restoration is not None:
                raise inaccessible_reference()
            from domain_kg.operations.capture import authorize_backup_read

            await authorize_backup_read(queue, access, source, backup)
        elif restoration is not None:
            from domain_kg.materials.restoration import authorize_restore_read

            await authorize_restore_read(queue, access, source, restoration)
        elif not await queue.policy.material_visible(access, source.kg_id, source.material_id):
            raise inaccessible_reference()

    await authorize()
    raw = await queue.database.job_artifacts.find_one(
        {
            "org_id": str(source.org_id),
            "kg_id": str(source.kg_id),
            "id": str(checkpoint.artifact_id),
            "job_id": str(checkpoint.job_id),
            "source_version_id": str(source.version_id),
            "stage": checkpoint.stage,
            "input_hash": checkpoint.input_hash,
        }
    )
    if raw is None:
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "An accepted artifact is missing.")
    try:
        artifact = JobArtifact.model_validate({key: raw[key] for key in JobArtifact.model_fields})
    except (ValidationError, KeyError, TypeError):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "An accepted artifact ledger is invalid."
        ) from None
    if (
        artifact.blob.material_id != source.material_id
        or artifact.blob.sha256 != checkpoint.output_hash
    ):
        raise inaccessible_reference()
    origin = await queue.database.jobs.find_one(
        {
            "org_id": str(source.org_id),
            "kg_id": str(source.kg_id),
            "id": str(artifact.job_id),
            "target_id": str(source.version_id),
            "material_id": str(source.material_id),
        }
    )
    if origin is None:
        raise inaccessible_reference()
    stores = await queue.router.for_sources(access, source.kg_id)
    version = await stores.authority.get_material_version(
        ScopedRef(org_id=source.org_id, kg_id=source.kg_id, id=source.version_id)
    )
    if version is None or version.material.id != source.material_id:
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "The artifact's canonical source owner differs."
        )
    data = bytearray()
    async for part in BlobStore(stores.blobs).open(artifact.blob):
        data.extend(part)
    await queue.policy.revalidate(access)
    await authorize()
    return bytes(data)


class PublicationJournal:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def node_fences(
        self, access: AccessContext, kg_id: UUID, materials: tuple[UUID, ...]
    ) -> tuple[NodeFence, ...]:
        from domain_kg.graph.node_actions import NodeVisibilityService

        return await NodeVisibilityService(self.queue).load(access, kg_id, materials)

    async def initialize(self) -> None:
        for name in ("projection_attempts", "publication_manifests"):
            await self.database[name].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )
        await self.database.projection_attempts.create_index(
            [
                ("org_id", 1),
                ("kg_id", 1),
                ("source.material_id", 1),
                ("source.version_id", 1),
                ("state", 1),
                ("receipt.published_revision", -1),
            ]
        )
        await self.database.publication_bindings.create_index(
            [("org_id", 1), ("kg_id", 1), ("view_manifest_id", 1), ("owner_key", 1)], unique=True
        )
        await self.database.repair_requests.create_index(
            [("org_id", 1), ("kg_id", 1), ("manifest_id", 1)], unique=True
        )
        await self.database.projection_record_refs.create_index(
            [("org_id", 1), ("kg_id", 1), ("manifest_id", 1), ("position", 1)], unique=True
        )
        await self.database.publication_commands.create_index(
            [("org_id", 1), ("kg_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)], unique=True
        )

    async def attempt(self, access: AccessContext, manifest_id: UUID) -> dict[str, Any]:
        try:
            return await self._attempt(access, manifest_id)
        except (ValidationError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The projection manifest is invalid."
            ) from None

    async def _attempt(self, access: AccessContext, manifest_id: UUID) -> dict[str, Any]:
        await self.queue.policy.revalidate(access)
        raw = await self.database.projection_attempts.find_one(
            {
                "org_id": str(access.org_id),
                "kg_id": {"$in": [str(kg) for kg in access.kg_ids]},
                "id": str(manifest_id),
            }
        )
        if raw is None:
            raise inaccessible_reference()
        from domain_kg.storage.router import projection_checksum

        references = [
            ScopedRef.model_validate(row["reference"])
            async for row in self.database.projection_record_refs.find(
                {
                    "org_id": raw["org_id"],
                    "kg_id": raw["kg_id"],
                    "manifest_id": raw["id"],
                }
            ).sort("position", 1)
        ]
        if (
            len(references) != raw["record_count"]
            or projection_checksum([str(reference.id) for reference in references])
            != raw["record_checksum"]
            or any(
                (str(ref.org_id), str(ref.kg_id)) != (raw["org_id"], raw["kg_id"])
                for ref in references
            )
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Projection record mappings differ."
            )
        raw["batch"]["records"] = [reference.model_dump(mode="json") for reference in references]
        return dict(raw)

    async def load_metadata(self, access: AccessContext, kg_id: UUID) -> PublishedView:
        try:
            return await self._load_metadata(access, kg_id)
        except (ValidationError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The accepted source bindings are invalid."
            ) from None

    async def _load_metadata(self, access: AccessContext, kg_id: UUID) -> PublishedView:
        await self.queue.policy.revalidate(access)
        if kg_id not in access.kg_ids:
            raise inaccessible_reference()
        ns = {"org_id": str(access.org_id), "kg_id": str(kg_id)}
        kg = await self.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
        if kg is None:
            raise inaccessible_reference()
        if kg["published_revision"] == 0:
            return PublishedView(
                org_id=access.org_id, kg_id=kg_id, revision=0, generation=kg["generation"]
            )
        header = await self.database.publication_manifests.find_one(
            ns
            | {
                "id": kg.get("published_manifest_id"),
                "revision": kg["published_revision"],
                "generation": kg["generation"],
                "state": "accepted",
            }
        )
        if header is None:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The accepted view marker is inconsistent."
            )
        return await self._from_header(access, kg_id, header)

    async def _from_header(
        self, access: AccessContext, kg_id: UUID, header: dict[str, Any]
    ) -> PublishedView:
        ns = {"org_id": str(access.org_id), "kg_id": str(kg_id)}
        if any(header.get(field) != value for field, value in ns.items()) or (
            header["state"] != "accepted"
        ):
            raise inaccessible_reference()
        bindings = [
            AcceptedProjection.model_validate(row["binding"])
            async for row in self.database.publication_bindings.find(
                ns | {"view_manifest_id": header["id"]}
            )
        ]
        if len(bindings) != header["binding_count"]:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Accepted source mappings are missing."
            )
        receipts: list[ProjectionReceipt] = []
        for binding in bindings:
            if binding.source is not None:
                await check_import_origin(self.database, binding.source, binding.import_origin)
            attempt = await self.attempt(access, binding.manifest_id)
            if attempt["state"] != "accepted" or attempt["batch"]["attempt_id"] != str(
                binding.attempt_id
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Accepted projection ownership differs."
                )
            expected_origin = (
                binding.import_origin.model_dump(mode="json") if binding.import_origin else None
            )
            if attempt.get("import_origin") != expected_origin:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Accepted import provenance differs."
                )
            receipts.extend(
                ProjectionReceipt.model_validate(value) for value in attempt["receipts"]
            )
        view = PublishedView(
            org_id=access.org_id,
            kg_id=kg_id,
            revision=header["revision"],
            generation=header["generation"],
            manifest_id=header["id"],
            projections=tuple(bindings),
            receipts=tuple(receipts),
            sources=tuple(binding.source for binding in bindings if binding.source),
            curations=tuple(binding.curation for binding in bindings if binding.curation),
        )
        await self.queue.policy.revalidate(access)
        return view

    async def retained(
        self, access: AccessContext, kg_id: UUID, material_id: UUID, version_id: UUID
    ) -> RetainedSelection:
        try:
            return await self._retained(access, kg_id, material_id, version_id)
        except (ValidationError, KeyError, TypeError):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Retained source mappings are invalid."
            ) from None

    async def _retained(
        self, access: AccessContext, kg_id: UUID, material_id: UUID, version_id: UUID
    ) -> RetainedSelection:
        await self.queue.policy.revalidate(access)
        if not await self.queue.policy.material_visible(access, kg_id, material_id):
            raise inaccessible_reference()
        ns = {"org_id": str(access.org_id), "kg_id": str(kg_id)}
        source = SourceVersion(
            org_id=access.org_id, kg_id=kg_id, material_id=material_id, version_id=version_id
        )
        stores = await self.queue.router.for_sources(access, kg_id)
        version = await stores.authority.get_material_version(
            ScopedRef(org_id=access.org_id, kg_id=kg_id, id=version_id)
        )
        if version is None or version.material.id != material_id:
            raise inaccessible_reference()
        original = await stores.authority._database.version_blobs.find_one(
            ns | {"id": str(version_id), "material_id": str(material_id)}
        )
        if original is None:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained original is missing.")
        blob = BlobRef.model_validate(original["blob"])
        if any(
            getattr(blob, field) != getattr(source, field) for field in SourceVersion.model_fields
        ):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained original owner differs.")
        if original["state"] == "staged":
            raise DomainError(ErrorCode.REVISION_NOT_READY, "This version has not been accepted.")
        if original["state"] not in ("accepted", "retained"):
            raise inaccessible_reference()
        attempt = await self.database.projection_attempts.find_one(
            ns | {"source": source.model_dump(mode="json"), "state": "accepted"},
            sort=[("receipt.published_revision", -1)],
        )
        header = (
            await self.database.publication_manifests.find_one(
                ns | {"id": attempt["id"], "state": "accepted"}
            )
            if attempt
            else None
        )
        kg = await self.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
        if header is None or kg is None:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained acceptance is missing.")
        if header["revision"] > kg["published_revision"]:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained acceptance is invalid.")
        if header["generation"] != kg["generation"]:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "This retained index generation needs a rebuild."
            )
        view = await self._from_header(access, kg_id, header)
        if source not in view.sources:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Retained binding is missing.")
        row = await self.queue.policy.database.material_access.find_one(
            ns | {"id": str(material_id)}
        )
        if row is None or not await self.queue.policy.material_visible(access, kg_id, material_id):
            raise inaccessible_reference()
        metadata = MaterialMetadata.model_validate(row["metadata"])
        if (metadata.material_id, str(metadata.version_id)) != (
            material_id,
            row["active_version_id"],
        ):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Material metadata owner differs.")
        return RetainedSelection(
            view=view,
            source=source,
            metadata=metadata.model_copy(
                update={"version_id": version_id, "media_type": blob.media_type}
            ),
        )

    async def load(self, access: AccessContext, kg_id: UUID) -> PublishedView:
        from domain_kg.revisions.publication import verify_accepted

        view = await self.load_metadata(access, kg_id)
        for binding in view.projections:
            if binding.source and not await self.queue.policy.material_visible(
                access, kg_id, binding.source.material_id
            ):
                continue
            try:
                await verify_accepted(self.queue, access, binding)
            except DomainError as error:
                if error.code in (ErrorCode.PROJECTION_INCONSISTENT, ErrorCode.BACKEND_UNAVAILABLE):
                    await self.database.repair_requests.update_one(
                        {
                            "org_id": str(view.org_id),
                            "kg_id": str(kg_id),
                            "manifest_id": str(binding.manifest_id),
                        },
                        {
                            "$setOnInsert": {
                                "state": "pending_authorized_repair",
                                "binding": binding.model_dump(mode="json"),
                            }
                        },
                        upsert=True,
                    )
                raise
        await self.queue.policy.revalidate(access)
        latest = await self.database.kgs.find_one({"org_id": str(access.org_id), "id": str(kg_id)})
        if (
            latest is None
            or latest["published_revision"] != view.revision
            or latest["generation"] != str(view.generation)
        ):
            raise DomainError(ErrorCode.POLICY_CHANGED, "Publication changed during the read.")
        return view
