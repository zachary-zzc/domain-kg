"""One Mongo commit activates a freshly verified native target and its explicit owner."""

import hashlib
from uuid import uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.invariants import assert_active_owners
from domain_kg.identity.models import Action, Principal
from domain_kg.jobs.models import JobArtifact, LeasedJob
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.models import SourceVersion
from domain_kg.operations.jobs import OwnerOperations
from domain_kg.operations.models import RestoreCommand
from domain_kg.operations.restore_authority import RestoreAuthority
from domain_kg.operations.restore_fences import material_fences
from domain_kg.operations.restore_preparation import PreparedRestore
from domain_kg.operations.restore_projections import RestoredProjection
from domain_kg.operations.restore_results import RestoreResult, RestoreResults
from domain_kg.providers.service import ProviderService
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.revisions.models import RevisionReceipt
from domain_kg.revisions.projections import (
    CurationEmbeddedDocument,
    EmbeddedDocument,
    ProjectionDocument,
    digest,
)
from domain_kg.revisions.publication import verify_document
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.router import projection_checksum


class RestoreActivation(RestoreAuthority):
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        super().__init__(queue, providers)
        self.results = RestoreResults(queue)

    async def activate(
        self, job: LeasedJob, staged: PreparedRestore, parts: tuple[RestoredProjection, ...]
    ) -> RestoreResult:
        if await self.results.accepted(job):
            access = await self.queue.policy.resolve_access(
                await self._actor(job), [job.kg_id], Action.RESTORE, org_id=job.org_id
            )
            return await self.results.get(access, job.kg_id, job.id)
        if await self.bindings.approved(job) != (staged.binding.target, staged.binding.embedding):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
        if (
            len(parts) != len(staged.sources) + len(staged.curations)
            or (
                {part.binding.source for part in parts if part.binding.source is not None}
                != {
                    SourceVersion(
                        **{key: getattr(item.document, key) for key in SourceVersion.model_fields}
                    )
                    for item in staged.sources
                }
            )
            or (
                {part.binding.curation for part in parts if part.binding.curation is not None}
                != {item.curation for item in staged.curations}
            )
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Restore readiness owner set differs."
            )
        for part in parts:
            document: ProjectionDocument
            if isinstance(part.artifact, JobArtifact):
                body = b"".join(
                    [
                        chunk
                        async for chunk in BlobStore(staged.binding.stores.blobs).open(
                            part.artifact.blob
                        )
                    ]
                )
                document = EmbeddedDocument.model_validate_json(body)
            else:
                body = part.artifact.body.encode()
                document = CurationEmbeddedDocument.model_validate_json(body)
            if (
                hashlib.sha256(body).hexdigest() != part.binding.content_hash
                or (await verify_document(staged.binding.stores, document, part.report.batch))
                != part.report.receipts
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Restored projection readiness changed."
                )
        blobs = (*staged.originals, *(artifact.artifact.blob for artifact in staged.artifacts))
        for reference in blobs:
            async for _ in BlobStore(staged.binding.stores.blobs).open(reference):
                await self.targets.guard(job)
        await PublicationJournal(self.queue).initialize()
        await self.queue.database.restore_results.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        access = await self.queue.authorize(job)
        operation = await OwnerOperations(self.queue).authorize(
            access, await self.queue._current(job)
        )
        if not isinstance(operation.command, RestoreCommand):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore intent differs.")
        command = operation.command
        metadata = staged.inventory.rows("kg_metadata")
        if len(metadata) != 1:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore KG metadata is missing.")
        target = staged.binding.target
        ns = target.target.model_dump(mode="json")
        root = uuid5(job.attempt_id, "restore-root")
        result = RestoreResult(
            org_id=job.org_id,
            kg_id=job.kg_id,
            id=job.target_id,
            job_id=job.id,
            attempt_id=job.attempt_id,
            target=target.target,
            generation=target.generation,
            root_manifest_id=root,
            source_backup_id=staged.inventory.manifest.backup_id,
            source_header_sha=hashlib.sha256(
                staged.inventory.manifest.model_dump_json().encode()
            ).hexdigest(),
            parts_hash=digest([part.origin.model_dump(mode="json") for part in parts]),
            created_at=now(),
        )

        async def commit(session: AsyncClientSession) -> None:
            await self._guard(job, staged, session)
            if await self.bindings.approved(job) != (target, staged.binding.embedding):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore configuration changed.")
            source_ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
            visibility_rows = staged.inventory.rows("node_visibility")
            captured_visibility = {row["id"]: row for row in visibility_rows}
            current_visibility = {
                row["id"]: {key: value for key, value in row.items() if key != "_id"}
                async for row in self.queue.database.node_visibility.find(
                    source_ns, session=session
                )
            }
            if current_visibility != captured_visibility:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT,
                    "Current visibility history changed since this backup.",
                )
            current_history = {
                row["id"]
                async for row in self.queue.database.curation_revisions.find(
                    source_ns | {"state": "accepted"}, session=session
                )
            }
            if current_history != {
                row["id"] for row in staged.inventory.rows("curation_revisions")
            }:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Accepted human history changed since this backup."
                )
            member = await self.queue.database.members.find_one(
                {
                    "org_id": str(job.org_id),
                    "issuer": access.principal.issuer,
                    "subject": access.principal.subject,
                    "kind": "human",
                    "suspended": False,
                },
                session=session,
            )
            if member is None:
                raise DomainError(
                    ErrorCode.RESOURCE_NOT_ACCESSIBLE, "The resource is not accessible."
                )
            if (
                await self.queue.database.domain_profiles.find_one(
                    {"id": metadata[0]["domain_profile_id"]}, session=session
                )
                is None
            ):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "The approved domain profile is unavailable."
                )
            await self.queue.database.registry_state.update_one(
                {"_id": "placement"}, {"$inc": {"revision": 1}}, session=session
            )
            for part in parts:
                source = part.binding.source
                if source is not None:
                    current = await self.queue.database.material_access.find_one(
                        source_ns | {"id": str(source.material_id)}, session=session
                    )
                    material_key = ns | {"id": str(source.material_id)}
                    captured = await self.queue.database.material_access.find_one(
                        material_key, session=session
                    )
                    if captured is None:
                        raise DomainError(
                            ErrorCode.PROJECTION_INCONSISTENT,
                            "Restored material metadata is missing.",
                        )
                    update = material_fences(captured, current)
                    await self.queue.database.material_access.update_one(
                        material_key, {"$set": update}, session=session
                    )
                if isinstance(part.artifact, JobArtifact):
                    await self.queue.database.job_artifacts.insert_one(
                        part.artifact.model_dump(mode="json"), session=session
                    )
                else:
                    await self.queue.database.operation_artifacts.insert_one(
                        part.artifact.model_dump(mode="json"), session=session
                    )
                await self.queue.database.job_checkpoints.insert_one(
                    part.checkpoint.model_dump(mode="json"), session=session
                )
                batch = part.report.batch
                receipt = RevisionReceipt(
                    **target.target.model_dump(),
                    revision_id=batch.revision_id,
                    state="published",
                    published_revision=1,
                )
                await self.queue.database.projection_attempts.insert_one(
                    ns
                    | {
                        "id": str(batch.manifest_id),
                        "batch": batch.model_dump(mode="json", exclude={"records"}),
                        "record_count": len(batch.records),
                        "record_checksum": projection_checksum(
                            [str(ref.id) for ref in batch.records]
                        ),
                        "job": part.job.model_dump(mode="json"),
                        "source": source.model_dump(mode="json") if source else None,
                        "curation": part.binding.curation.model_dump(mode="json")
                        if part.binding.curation
                        else None,
                        "embedded_checkpoint": part.checkpoint.model_dump(mode="json"),
                        "receipts": [r.model_dump(mode="json") for r in part.report.receipts],
                        "state": "accepted",
                        "receipt": receipt.model_dump(mode="json"),
                        "restored_origin": part.origin.model_dump(mode="json"),
                        "import_origin": part.binding.import_origin.model_dump(mode="json")
                        if part.binding.import_origin
                        else None,
                    },
                    session=session,
                )
                if batch.records:
                    await self.queue.database.projection_record_refs.insert_many(
                        [
                            ns
                            | {
                                "manifest_id": str(batch.manifest_id),
                                "position": index,
                                "reference": reference.model_dump(mode="json"),
                            }
                            for index, reference in enumerate(batch.records)
                        ],
                        session=session,
                    )
                if source is not None:
                    owner_key = f"source:{source.material_id}"
                elif part.binding.curation is not None:
                    owner_key = (
                        f"identity:{part.binding.curation.id}"
                        if any(
                            row["id"] == str(part.binding.curation.id) and row["kind"] == "identity"
                            for row in staged.inventory.rows("curation_revisions")
                        )
                        else f"curation:{part.binding.curation.id}"
                    )
                else:
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT, "Restore owner is missing."
                    )
                await self.queue.database.publication_bindings.insert_one(
                    ns
                    | {
                        "view_manifest_id": str(root),
                        "owner_key": owner_key,
                        "binding": part.binding.model_dump(mode="json"),
                    },
                    session=session,
                )
            await self.queue.database.publication_manifests.insert_one(
                ns
                | {
                    "id": str(root),
                    "revision": 1,
                    "generation": str(target.generation),
                    "state": "accepted",
                    "binding_count": len(parts),
                    "restore_operation_id": str(job.target_id),
                    "created_at": now(),
                },
                session=session,
            )
            await self.queue.database.kgs.insert_one(
                {
                    "_id": str(target.target.kg_id),
                    "org_id": str(job.org_id),
                    "id": str(target.target.kg_id),
                    "name": command.name,
                    "storage_profile_id": str(target.placement.id),
                    "domain_profile_id": metadata[0]["domain_profile_id"],
                    "state": "ready",
                    "revision": 0,
                    "visibility_revision": metadata[0]["visibility_revision"],
                    "identity_sequence": metadata[0].get("identity_sequence", 0),
                    "published_revision": 1,
                    "published_manifest_id": str(root),
                    "generation": str(target.generation),
                    "configuration_epoch": 1,
                },
                session=session,
            )
            await self.queue.database.kg_provider_settings.insert_one(
                ns
                | {
                    "profile_id": str(staged.binding.embedding.id),
                    "profile_revision": staged.binding.embedding.revision,
                    "embedding_model": staged.binding.embedding.model,
                    "embedding_dimensions": staged.binding.embedding.dimensions,
                    "revision": 1,
                    "embedding_space_id": staged.binding.embedding.space_id,
                },
                session=session,
            )
            await self.queue.database.kg_grants.insert_one(
                ns | {"subject_kind": "member", "subject_id": member["id"], "role": "owner"},
                session=session,
            )
            await self.queue.database.restore_results.insert_one(
                result.model_dump(mode="json") | {"state": "sealed"}, session=session
            )
            updated = await self.queue.database.jobs.update_one(
                self.queue._active(job),
                {
                    "$set": {
                        "stage": "ready",
                        "progress": 1,
                        "lease_until": now(),
                        "error_code": None,
                        "accepted_operation_id": str(job.target_id),
                        "accepted_operation_hash": digest(result.model_dump(mode="json")),
                    }
                },
                session=session,
            )
            if not updated.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore lease changed.")
            await self.queue.database.restore_targets.update_one(
                source_ns | {"id": str(job.target_id)},
                {"$set": {"state": "activated"}},
                session=session,
            )
            await self.queue.database.organizations.update_one(
                {"_id": str(job.org_id)}, {"$inc": {"policy_revision": 1}}, session=session
            )
            await assert_active_owners(self.queue.database, str(job.org_id), session)
            await OwnerOperations(self.queue).audit.append(
                access, job.kg_id, job.target_id, job.id, "restore", "completed", session
            )

        await self.queue.transactions.transaction(commit)
        return await self.results.get(
            await self.queue.policy.resolve_access(
                access.principal, [job.kg_id], Action.RESTORE, org_id=job.org_id
            ),
            job.kg_id,
            job.id,
        )

    async def _actor(self, job: LeasedJob) -> Principal:
        raw = await self.queue.database.jobs.find_one(self.queue._scope(job))
        if raw is None:
            raise DomainError(ErrorCode.RESOURCE_NOT_ACCESSIBLE, "The resource is not accessible.")
        return Principal.model_validate(raw["actor"])
