"""Mongo acceptance metadata; projection presence can never establish acceptance."""

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.models import Checkpoint, JobArtifact
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.revisions.models import ProjectionReceipt, PublishedView
from domain_kg.revisions.projections import AcceptedProjection
from domain_kg.storage.blobs import BlobStore


async def read_artifact(
    queue: JobQueue,
    access: AccessContext,
    checkpoint: Checkpoint,
    source: SourceVersion,
) -> bytes:
    """Read immutable artifacts under current permission, independent of the original actor."""
    await queue.policy.revalidate(access)
    if (checkpoint.org_id, checkpoint.kg_id) != (
        source.org_id,
        source.kg_id,
    ) or source.org_id != access.org_id:
        raise inaccessible_reference()
    if not await queue.policy.material_visible(access, source.kg_id, source.material_id):
        raise inaccessible_reference()
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
    if not await queue.policy.material_visible(access, source.kg_id, source.material_id):
        raise inaccessible_reference()
    return bytes(data)


class PublicationJournal:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database = queue, queue.database

    async def initialize(self) -> None:
        for name in ("projection_attempts", "publication_manifests"):
            await self.database[name].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
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
            attempt = await self.attempt(access, binding.manifest_id)
            if attempt["state"] != "accepted" or attempt["batch"]["attempt_id"] != str(
                binding.attempt_id
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Accepted projection ownership differs."
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
