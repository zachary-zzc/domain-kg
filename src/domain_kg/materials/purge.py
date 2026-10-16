"""Exact-source retained cleanup. Mongo acceptance and physical deletion are separate."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession
from qdrant_client import models as qmodels

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.models import SupportRecord
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobArtifact, JobCommand, LeasedJob
from domain_kg.jobs.queue import TERMINAL, JobQueue
from domain_kg.materials.models import BlobRef, BlobScope, MaterialImpact
from domain_kg.materials.purge_models import PurgeMaterial, PurgeReceipt
from domain_kg.materials.service import digest
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.revisions.journal import PublicationJournal
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.router import RoutedStores


def now() -> datetime:
    return datetime.now(UTC)


def inconsistent() -> DomainError:
    return DomainError(
        ErrorCode.PROJECTION_INCONSISTENT, "Retained cleanup ownership is inconsistent."
    )


def eligible(row: dict[str, Any], revision: int) -> None:
    if (
        row.get("revision") != revision
        or not row.get("tombstoned")
        or row.get("purged")
        or not row.get("purge_after")
        or row["purge_after"].replace(tzinfo=UTC) > now()
    ):
        raise DomainError(
            ErrorCode.VERSION_CONFLICT, "This material is not eligible for retained cleanup."
        )


async def authorize_purge_job(
    queue: JobQueue,
    access: AccessContext,
    command: JobCommand,
    session: AsyncClientSession | None = None,
) -> dict[str, Any]:
    current = await queue.policy.revalidate(access, action=Action.PURGE, session=session)
    if (
        command.kind != "purge_material"
        or command.org_id != current.org_id
        or command.kg_id not in current.kg_ids
    ):
        raise inaccessible_reference()
    ns = {"org_id": str(command.org_id), "kg_id": str(command.kg_id)}
    raw = await queue.database.jobs.find_one(
        ns | {"id": str(command.id), "kind": "purge_material"}, session=session
    )
    intent = await queue.database.material_commands.find_one(
        ns
        | {
            "operation": "purge",
            "state": "pending",
            "job_id": str(command.id),
            "material_id": str(command.target_id),
        },
        session=session,
    )
    row = await queue.policy._material_acl_record(
        current, command.kg_id, command.target_id, session=session
    )
    if (
        raw is None
        or intent is None
        or row is None
        or raw["material_id"] != str(command.target_id)
        or raw.get("command_id") != intent["id"]
        or row.get("purge_job_id") != str(command.id)
        or any(
            intent["actor"].get(key) != getattr(current.principal, key)
            for key in ("issuer", "subject", "kind")
        )
        or raw["actor"] != intent["actor"]
    ):
        raise inaccessible_reference()
    eligible(row, intent["expected_revision"])
    return dict(intent)


@dataclass(frozen=True)
class PurgePlan:
    versions: tuple[UUID, ...]
    blobs: tuple[BlobRef, ...]
    attempts: tuple[dict[str, Any], ...]
    committed_bytes: int
    reserved_bytes: int


class PurgeService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.policy, self.database = queue, queue.policy, queue.database
        self.transactions = queue.transactions

    async def admit(self, access: AccessContext, command: PurgeMaterial) -> PurgeReceipt:
        try:
            return await self._admit(access, command)
        except (ValidationError, KeyError, TypeError, ValueError):
            raise inconsistent() from None

    async def _admit(self, access: AccessContext, command: PurgeMaterial) -> PurgeReceipt:
        command = PurgeMaterial.model_validate(command)
        current = await self.policy.revalidate(access, action=Action.PURGE)
        if command.kg_id not in current.kg_ids:
            raise inaccessible_reference()
        stores = await self.queue.router.for_sources(current, command.kg_id)
        ns: dict[str, Any] = {"org_id": str(current.org_id), "kg_id": str(command.kg_id)}
        key = self.transactions.key(current, command.kg_id, command.idempotency_key)
        fingerprint = digest({"operation": "purge", "command": command.model_dump(mode="json")})

        async def accept(session: AsyncClientSession) -> PurgeReceipt:
            if command.dry_run:
                await self.policy.revalidate(current, action=Action.PURGE, session=session)
            else:
                await self.transactions.fence(current, Action.PURGE, session)
            row = await self.policy._material_acl_record(
                current, command.kg_id, command.material_id, session=session
            )
            if row is None:
                raise inaccessible_reference()
            previous = await self.database.material_commands.find_one(key, session=session)
            if previous and not command.dry_run:
                if previous.get("operation") != "purge" or previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Material key has different input."
                    )
                receipt = PurgeReceipt.model_validate(previous["receipt"])
                if (receipt.org_id, receipt.kg_id, receipt.material_id, str(receipt.job_id)) != (
                    current.org_id,
                    command.kg_id,
                    command.material_id,
                    previous["job_id"],
                ):
                    raise inconsistent()
                if (
                    receipt.state != ("purged" if previous["state"] == "accepted" else "pending")
                    or receipt.revision != command.expected_revision + (receipt.state == "purged")
                    or previous["material_id"] != str(command.material_id)
                    or previous["expected_revision"] != command.expected_revision
                ):
                    raise inconsistent()
                registered_job = await self.database.jobs.find_one(
                    ns
                    | {
                        "id": str(receipt.job_id),
                        "kind": "purge_material",
                        "target_id": str(command.material_id),
                        "material_id": str(command.material_id),
                        "command_id": previous["id"],
                        "actor": previous["actor"],
                    },
                    session=session,
                )
                if registered_job is None:
                    raise inconsistent()
                return receipt
            eligible(row, command.expected_revision)
            await self._idle(current, command.kg_id, command.material_id, session=session)
            versions = [
                item
                async for item in stores.authority._database.version_blobs.find(
                    ns | {"material_id": str(command.material_id)}, session=session
                ).limit(1001)
            ]
            if len(versions) > 1000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Retained cleanup exceeds its bounded version limit."
                )
            size = 0
            for item in versions:
                blob = BlobRef.model_validate(item["blob"])
                if (blob.org_id, blob.kg_id, blob.material_id, str(blob.version_id)) != (
                    current.org_id,
                    command.kg_id,
                    command.material_id,
                    item["id"],
                ):
                    raise inconsistent()
                await stores.authority.require_version(
                    ScopedRef(org_id=current.org_id, kg_id=command.kg_id, id=blob.version_id),
                    session=session,
                )
                canonical_version = await stores.authority._require(
                    "material_versions",
                    ScopedRef(org_id=current.org_id, kg_id=command.kg_id, id=blob.version_id),
                    session=session,
                )
                if canonical_version["material"]["id"] != str(command.material_id):
                    raise inconsistent()
                size += blob.size_bytes
            impact = MaterialImpact(versions=len(versions), original_bytes=size, pending_jobs=0)
            if command.dry_run:
                return PurgeReceipt(
                    **ns,
                    material_id=command.material_id,
                    revision=command.expected_revision,
                    state="preview",
                    impact=impact,
                )
            intent_id, job_id = uuid4(), uuid4()
            receipt = PurgeReceipt(
                **ns,
                material_id=command.material_id,
                revision=command.expected_revision,
                state="pending",
                job_id=job_id,
                impact=impact,
            )
            job = JobCommand.model_validate(
                ns
                | {
                    "id": job_id,
                    "kind": "purge_material",
                    "target_id": command.material_id,
                    "idempotency_key": "purge-" + str(intent_id),
                }
            )
            actor = current.principal.model_dump(mode="json")
            await self.database.material_commands.insert_one(
                key
                | {
                    "id": str(intent_id),
                    "operation": "purge",
                    "digest": fingerprint,
                    "state": "pending",
                    "phase": "pending",
                    "material_id": str(command.material_id),
                    "job_id": str(job_id),
                    "expected_revision": command.expected_revision,
                    "actor": actor,
                    "reason": command.reason,
                    "receipt": receipt.model_dump(mode="json"),
                    "created_at": now(),
                },
                session=session,
            )
            await self.database.jobs.insert_one(
                job.model_dump(mode="json")
                | {
                    "command_id": str(intent_id),
                    "material_id": str(command.material_id),
                    "actor": actor,
                    "stage": "queued",
                    "fence": 0,
                    "attempts": 0,
                    "created_at": now(),
                },
                session=session,
            )
            await self.database.material_access.update_one(
                ns | {"id": str(command.material_id)},
                {"$set": {"purge_job_id": str(job_id), "purge_state": "pending"}},
                session=session,
            )
            await self.database.kgs.update_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]},
                {"$inc": {"visibility_revision": 1}},
                session=session,
            )
            await self.database.outbox.insert_one(
                ns
                | {
                    "id": str(job_id),
                    "kind": "job_available",
                    "delivered": False,
                    "created_at": now(),
                },
                session=session,
            )
            await self.database.audit.insert_one(
                ns
                | {
                    "id": str(uuid4()),
                    "kind": "material_purge_requested",
                    "actor": actor,
                    "material_id": str(command.material_id),
                    "job_id": str(job_id),
                    "reason": command.reason,
                    "created_at": now(),
                },
                session=session,
            )
            return receipt

        receipt = await self.transactions.transaction(accept)
        await self.policy.revalidate(current, action=Action.PURGE)
        return receipt

    async def _idle(
        self,
        access: AccessContext,
        kg: UUID,
        material: UUID,
        *,
        own_job: UUID | None = None,
        session: AsyncClientSession | None = None,
    ) -> None:
        ns = {"org_id": str(access.org_id), "kg_id": str(kg)}
        active = await self.database.jobs.find_one(
            ns
            | {
                "material_id": str(material),
                "id": {"$ne": str(own_job)},
                "stage": {"$nin": list(TERMINAL)},
                "$or": [{"kind": "purge_material"}, {"lease_until": {"$gt": now()}}],
            },
            session=session,
        )
        pin = await self.database.read_pins.find_one(
            ns | {"material_ids": str(material), "expires_at": {"$gt": now()}}, session=session
        )
        upload = await self.database.uploads.find_one(
            ns
            | {"material_id": str(material), "state": "uploading", "upload_until": {"$gt": now()}},
            session=session,
        )
        if active or pin or upload:
            raise DomainError(
                ErrorCode.REVISION_NOT_READY,
                "Retained source work must settle before cleanup.",
                retryable=True,
            )

    async def _references(self, stores: RoutedStores, blob: BlobRef) -> None:
        query = {
            "blob." + key: str(getattr(blob, key))
            for key in ("org_id", "kg_id", "material_id", "version_id", "object_id")
        }
        for name, collection in (
            ("version", stores.authority._database.version_blobs),
            ("artifact", self.database.job_artifacts),
            ("upload", self.database.uploads),
        ):
            count = 0
            async for row in collection.find(query).limit(20001):
                count += 1
                if count > 20000:
                    raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Object reference limit exceeded.")
                if (row.get("org_id"), row.get("kg_id")) != (str(blob.org_id), str(blob.kg_id)):
                    raise inconsistent()
                if BlobRef.model_validate(row["blob"]) != blob:
                    raise inconsistent()
                if name == "artifact":
                    origin = await self.database.jobs.find_one(
                        {
                            "org_id": row["org_id"],
                            "kg_id": row["kg_id"],
                            "id": row["job_id"],
                            "target_id": str(blob.version_id),
                            "material_id": str(blob.material_id),
                        }
                    )
                    if origin is None or row["source_version_id"] != str(blob.version_id):
                        raise inconsistent()
                elif (
                    row.get("material_id"),
                    row.get("id") if name == "version" else row.get("version_id"),
                ) != (str(blob.material_id), str(blob.version_id)):
                    raise inconsistent()

    async def _plan(self, access: AccessContext, job: LeasedJob, stores: RoutedStores) -> PurgePlan:
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        authority = stores.authority
        versions = (
            await authority._database.version_blobs.find(ns | {"material_id": str(job.target_id)})
            .limit(1001)
            .to_list()
        )
        if not versions or len(versions) > 1000:
            raise inconsistent()
        blobs: dict[str, BlobRef] = {}
        identifiers: list[UUID] = []
        committed, reserved = 0, 0

        def add(blob: BlobRef) -> None:
            if (blob.org_id, blob.kg_id, blob.material_id) != (
                job.org_id,
                job.kg_id,
                job.target_id,
            ):
                raise inconsistent()
            key = BlobStore(stores.blobs)._key(blob, blob.object_id)
            if key in blobs and blobs[key] != blob:
                raise inconsistent()
            blobs[key] = blob
            if len(blobs) > 20000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Retained cleanup object limit exceeded."
                )

        for row in versions:
            source = SourceVersion(
                org_id=job.org_id, kg_id=job.kg_id, material_id=job.target_id, version_id=row["id"]
            )
            reference = ScopedRef(org_id=job.org_id, kg_id=job.kg_id, id=source.version_id)
            await authority.require_version(reference)
            version = await authority._require("material_versions", reference)
            if version["material"]["id"] != str(job.target_id) or row["state"] not in (
                "accepted",
                "retained",
                "staged",
            ):
                raise inconsistent()
            blob = BlobRef.model_validate(row["blob"])
            if blob.version_id != source.version_id:
                raise inconsistent()
            add(blob)
            identifiers.append(source.version_id)
            committed += blob.size_bytes
        version_values = [str(value) for value in identifiers]
        support_count = 0
        async for raw_support in authority._database.supports.find(
            ns | {"version.id": {"$in": version_values}}
        ).limit(20001):
            support_count += 1
            if support_count > 20000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Source support cleanup limit exceeded."
                )
            support = SupportRecord.model_validate(
                {key: raw_support[key] for key in SupportRecord.model_fields}
            )
            if support.version is None or (support.version.org_id, support.version.kg_id) != (
                job.org_id,
                job.kg_id,
            ):
                raise inconsistent()
            await authority._require("assertions", support.assertion)
            for span in support.spans:
                block = await authority._database.source_blocks.find_one(
                    ns
                    | {
                        "id": str(span.location.block_id),
                        "material_id": str(job.target_id),
                        "version_id": str(support.version.id),
                    }
                )
                if block is None:
                    raise inconsistent()
        artifact_count = 0
        async for row in self.database.job_artifacts.find(
            ns | {"source_version_id": {"$in": version_values}}
        ).limit(20001):
            artifact_count += 1
            if artifact_count > 20000:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Source artifact limit exceeded.")
            artifact = JobArtifact.model_validate(
                {key: row[key] for key in JobArtifact.model_fields}
            )
            if artifact.blob.version_id != artifact.source_version_id:
                raise inconsistent()
            add(artifact.blob)
        upload_count = 0
        async for row in self.database.uploads.find(ns | {"material_id": str(job.target_id)}).limit(
            20001
        ):
            upload_count += 1
            if upload_count > 20000:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Source upload limit exceeded.")
            if row.get("blob"):
                blob = BlobRef.model_validate(row["blob"])
                if str(blob.version_id) != row["version_id"]:
                    raise inconsistent()
                add(blob)
            if row["state"] != "claimed" and row.get("quota_reserved", False):
                reserved += row["size_bytes"]
        await self._discover(job, stores, blobs)
        for blob in blobs.values():
            await self._references(stores, blob)
        attempts = []
        journal = PublicationJournal(self.queue)
        kg = await self.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
        async for row in self.database.projection_attempts.find(
            ns | {"source.material_id": str(job.target_id)}
        ).limit(20001):
            attempt = await journal.attempt(access, UUID(row["id"]))
            source = SourceVersion.model_validate(attempt["source"])
            if source.version_id not in identifiers or (
                source.org_id,
                source.kg_id,
                source.material_id,
            ) != (job.org_id, job.kg_id, job.target_id):
                raise inconsistent()
            origin = LeasedJob.model_validate(attempt["job"])
            owned_job = await self.database.jobs.find_one(
                ns
                | {
                    "id": str(origin.id),
                    "target_id": str(source.version_id),
                    "material_id": str(job.target_id),
                    "kind": origin.kind,
                }
            )
            if (
                owned_job is None
                or origin.target_id != source.version_id
                or (origin.org_id, origin.kg_id) != (job.org_id, job.kg_id)
                or str(origin.attempt_id) != attempt["batch"]["attempt_id"]
                or origin.kind
                not in ("ingest", "replace", "repair", "restore_material", "reprocess_material")
            ):
                raise inconsistent()
            if kg is None or attempt["batch"]["generation"] != kg["generation"]:
                raise DomainError(
                    ErrorCode.REVISION_NOT_READY,
                    "An older projection generation needs explicit cleanup routing.",
                )
            # A differently owned binding may never borrow this physical attempt.
            async for binding in self.database.publication_bindings.find(
                ns | {"binding.manifest_id": attempt["id"]}
            ):
                if binding.get("owner_key") != f"source:{job.target_id}" or binding["binding"].get(
                    "source"
                ) != source.model_dump(mode="json"):
                    raise inconsistent()
            attempts.append(attempt)
            if len(attempts) > 20000:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Projection attempt limit exceeded.")
        return PurgePlan(
            tuple(identifiers),
            tuple(blobs[key] for key in sorted(blobs)),
            tuple(attempts),
            committed,
            reserved,
        )

    async def _discover(
        self, job: LeasedJob, stores: RoutedStores, blobs: dict[str, BlobRef]
    ) -> None:
        """Include source-owned orphan uploads without accepting arbitrary object keys."""
        prefix = f"{job.org_id}/{job.kg_id}/{job.target_id}/"
        token: str | None = None
        seen: set[str] = set()
        count = 0
        while True:
            await self.queue.authorize(job)
            options: dict[str, Any] = {
                "Bucket": stores.blobs.bucket,
                "Prefix": prefix,
                "MaxKeys": 1000,
            }
            if token:
                options["ContinuationToken"] = token
            page = await asyncio.to_thread(stores.blobs.client.list_objects_v2, **options)
            for item in page.get("Contents", []):
                count += 1
                if count > 20000:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "Owned object listing limit exceeded."
                    )
                key = item["Key"]
                if key in blobs:
                    continue
                parts = key.split("/")
                if len(parts) != 5:
                    raise inconsistent()
                org, kg, material, version, identifier = (UUID(part) for part in parts)
                scope = BlobScope(org_id=org, kg_id=kg, material_id=material, version_id=version)
                if (org, kg, material) != (job.org_id, job.kg_id, job.target_id) or (
                    BlobStore(stores.blobs)._key(scope, identifier) != key
                ):
                    raise inconsistent()
                head = await asyncio.to_thread(
                    stores.blobs.client.head_object, Bucket=stores.blobs.bucket, Key=key
                )
                blobs[key] = BlobRef(
                    **scope.model_dump(),
                    object_id=identifier,
                    sha256=head.get("Metadata", {}).get("sha256"),
                    size_bytes=head["ContentLength"],
                    media_type=head["ContentType"],
                )
                if len(blobs) > 20000:
                    raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Owned object limit exceeded.")
            if not page.get("IsTruncated"):
                break
            token = page.get("NextContinuationToken")
            if not token or token in seen:
                raise inconsistent()
            seen.add(token)

    async def _detach(self, job: LeasedJob) -> None:
        access = await self.queue.authorize(job)
        intent = await authorize_purge_job(self.queue, access, job)
        if intent["phase"] == "detached":
            return
        lease = await self.queue.acquire_publication(job)
        manifest_id = uuid4()
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}

        async def commit(session: AsyncClientSession) -> None:
            current = await self.queue.authorize(job, session)
            await self.transactions.fence(current, Action.PURGE, session)
            await self.queue.validate_publication(job, lease, session)
            saved = await authorize_purge_job(self.queue, current, job, session)
            if saved["phase"] == "detached":
                return
            kg_key = {"org_id": ns["org_id"], "id": ns["kg_id"]}
            kg = await self.database.kgs.find_one(kg_key, session=session)
            if kg is None:
                raise inaccessible_reference()
            bindings = (
                await self.database.publication_bindings.find(
                    ns | {"view_manifest_id": kg.get("published_manifest_id")}, session=session
                )
                .limit(1001)
                .to_list()
            )
            if len(bindings) > 1000:
                raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Accepted binding limit exceeded.")
            retained = [row for row in bindings if row["owner_key"] != f"source:{job.target_id}"]
            for row in retained:
                await self.database.publication_bindings.insert_one(
                    {key: value for key, value in row.items() if key != "_id"}
                    | {"view_manifest_id": str(manifest_id)},
                    session=session,
                )
            await self.database.publication_manifests.insert_one(
                ns
                | {
                    "id": str(manifest_id),
                    "revision": kg["published_revision"] + 1,
                    "generation": kg["generation"],
                    "state": "accepted",
                    "binding_count": len(retained),
                    "job_id": str(job.id),
                    "attempt_id": str(job.attempt_id),
                    "kind": "purge_material",
                    "created_at": now(),
                },
                session=session,
            )
            changed = await self.database.kgs.update_one(
                kg_key
                | {
                    "published_revision": kg["published_revision"],
                    "publication_fence": lease.fence,
                    "publication_lease.lease_token": str(lease.lease_token),
                },
                {
                    "$set": {
                        "published_manifest_id": str(manifest_id),
                        "published_revision": kg["published_revision"] + 1,
                        "publication_lease.lease_until": now(),
                    },
                    "$inc": {"visibility_revision": 1},
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The cleanup publication changed.")
            await self.database.material_commands.update_one(
                ns | {"id": saved["id"], "state": "pending"},
                {"$set": {"phase": "detached", "detached_manifest_id": str(manifest_id)}},
                session=session,
            )
            await self.database.material_access.update_one(
                ns | {"id": str(job.target_id)},
                {"$set": {"purge_state": "detached"}},
                session=session,
            )

        await self.transactions.transaction(commit)

    async def _delete_attempt(
        self, job: LeasedJob, stores: RoutedStores, attempt: dict[str, Any]
    ) -> None:
        await self.queue.authorize(job)
        batch = attempt["batch"]
        conditions = {
            "org_id": str(job.org_id),
            "kg_id": str(job.kg_id),
            "attempt_id": batch["attempt_id"],
            "manifest_id": attempt["id"],
            "generation": batch["generation"],
            "manifest_hash": batch["content_hash"],
        }
        await stores.graph.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org_id,kg_id:$kg_id,attempt_id:$attempt_id,"
            "manifest_id:$manifest_id,generation:$generation,manifest_hash:$manifest_hash}) "
            "DETACH DELETE n",
            **conditions,
        )
        await self.queue.authorize(job)
        query = qmodels.Filter(
            must=[
                qmodels.FieldCondition(key=key, match=qmodels.MatchValue(value=value))
                for key, value in conditions.items()
            ]
        )
        await stores.vectors.client.delete(
            stores.vectors.collection,
            points_selector=qmodels.FilterSelector(filter=query),
            wait=True,
        )
        rows, _, _ = await stores.graph.driver.execute_query(
            "MATCH (n:ScopedProjection {org_id:$org_id,kg_id:$kg_id,attempt_id:$attempt_id,"
            "manifest_id:$manifest_id,generation:$generation,manifest_hash:$manifest_hash}) "
            "RETURN count(n) AS total",
            **conditions,
        )
        count = await stores.vectors.client.count(
            stores.vectors.collection, count_filter=query, exact=True
        )
        if rows[0]["total"] or count.count:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "Owned projection cleanup did not finish.",
                retryable=True,
            )

    async def run(self, job: LeasedJob) -> None:
        try:
            access = await self.queue.authorize(job)
            stores = await self.queue.router.for_kg(access, job.kg_id)
            await self._idle(access, job.kg_id, job.target_id, own_job=job.id)
            plan = await self._plan(access, job, stores)
            await self._detach(job)
            for attempt in plan.attempts:
                await self._delete_attempt(job, stores, attempt)
            for blob in plan.blobs:
                await self.queue.authorize(job)
                await self._references(stores, blob)
                await BlobStore(stores.blobs).remove_unclaimed(blob)
            await self.queue.authorize(job)
            remaining = await asyncio.to_thread(
                stores.blobs.client.list_objects_v2,
                Bucket=stores.blobs.bucket,
                Prefix=f"{job.org_id}/{job.kg_id}/{job.target_id}/",
                MaxKeys=1,
            )
            if remaining.get("KeyCount", 0):
                raise DomainError(
                    ErrorCode.BACKEND_UNAVAILABLE,
                    "Owned object cleanup is pending.",
                    retryable=True,
                )
            await self._finish(job, stores, plan)
        except (ValidationError, KeyError, TypeError, ValueError):
            raise inconsistent() from None
        except (ClientError, BotoCoreError) as exc:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "Owned object cleanup did not finish.",
                retryable=True,
            ) from exc

    async def _finish(self, job: LeasedJob, stores: RoutedStores, plan: PurgePlan) -> None:
        ns = {"org_id": str(job.org_id), "kg_id": str(job.kg_id)}
        versions = [str(value) for value in plan.versions]

        async def commit(session: AsyncClientSession) -> None:
            current = await self.queue.authorize(job, session)
            await self.transactions.fence(current, Action.PURGE, session)
            intent = await authorize_purge_job(self.queue, current, job, session)
            await self._idle(current, job.kg_id, job.target_id, own_job=job.id, session=session)
            authority = stores.authority._database
            source_key = ns | {"material_id": str(job.target_id), "version_id": {"$in": versions}}
            for name in (
                "source_blocks",
                "source_chunks",
                "observations",
                "import_id_mappings",
                "import_assertion_mappings",
            ):
                await authority[name].delete_many(source_key, session=session)
            await self.database.import_plans.update_many(
                ns
                | {
                    "source.material_id": str(job.target_id),
                    "source.version_id": {"$in": versions},
                    "state": "admitted",
                },
                {"$unset": {"graph": ""}, "$set": {"state": "purged", "purged_at": now()}},
                session=session,
            )
            await authority.engine_names.delete_many(
                ns | {"version_id": {"$in": versions}}, session=session
            )
            supports = (
                await authority.supports.find(
                    ns | {"version.id": {"$in": versions}}, session=session
                )
                .limit(20001)
                .to_list()
            )
            if len(supports) > 20000:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Source support cleanup limit exceeded."
                )
            await authority.supports.delete_many(
                ns | {"version.id": {"$in": versions}}, session=session
            )
            for identifier in {row["assertion"]["id"] for row in supports}:
                remaining = await authority.supports.find_one(
                    ns | {"assertion.id": identifier}, session=session
                )
                history = await self.database.curation_revisions.find_one(
                    ns | {"target_id": identifier}, session=session
                )
                if remaining is None and history is None:
                    await authority.assertions.delete_one(ns | {"id": identifier}, session=session)
            await authority.materials.update_one(
                ns | {"id": str(job.target_id)},
                {"$set": {"title": "Purged material"}},
                session=session,
            )
            await authority.version_blobs.update_many(
                ns | {"material_id": str(job.target_id)},
                {"$set": {"state": "purged", "proposed": {}, "purged_at": now()}},
                session=session,
            )
            await self.database.uploads.update_many(
                ns | {"material_id": str(job.target_id)},
                {"$set": {"state": "purged", "quota_reserved": False}},
                session=session,
            )
            await self.database.job_artifacts.update_many(
                ns | {"source_version_id": {"$in": versions}},
                {"$set": {"purged_at": now()}},
                session=session,
            )
            revision = intent["expected_revision"] + 1
            changed = await self.database.material_access.update_one(
                ns
                | {
                    "id": str(job.target_id),
                    "revision": intent["expected_revision"],
                    "purge_job_id": str(job.id),
                },
                {
                    "$set": {
                        "purged": True,
                        "purged_at": now(),
                        "purge_state": "purged",
                        "active_version_id": None,
                        "member_ids": [],
                        "group_ids": [],
                    },
                    "$unset": {"metadata": ""},
                    "$inc": {"revision": 1},
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The cleanup target changed.")
            receipt = PurgeReceipt(
                org_id=job.org_id,
                kg_id=job.kg_id,
                material_id=job.target_id,
                revision=revision,
                state="purged",
                job_id=job.id,
                impact=PurgeReceipt.model_validate(intent["receipt"]).impact,
            )
            await self.transactions.quota(
                current, job.kg_id, -plan.reserved_bytes, -plan.committed_bytes, session
            )
            await self.database.material_commands.update_one(
                ns | {"id": intent["id"], "state": "pending"},
                {
                    "$set": {
                        "state": "accepted",
                        "phase": "purged",
                        "receipt": receipt.model_dump(mode="json"),
                        "accepted_at": now(),
                    }
                },
                session=session,
            )
            changed = await self.database.jobs.update_one(
                self.queue._active(job),
                {
                    "$set": {
                        "stage": "ready",
                        "progress": 1,
                        "lease_until": now(),
                        "error_code": None,
                    }
                },
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The cleanup worker lease changed.")
            await self.database.kgs.update_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]},
                {"$inc": {"visibility_revision": 1}},
                session=session,
            )
            await self.database.audit.insert_one(
                ns
                | {
                    "id": str(uuid4()),
                    "kind": "material_purged",
                    "actor": current.principal.model_dump(mode="json"),
                    "material_id": str(job.target_id),
                    "job_id": str(job.id),
                    "created_at": now(),
                },
                session=session,
            )

        await self.transactions.transaction(commit)


async def visible_purge_job(
    queue: JobQueue,
    access: AccessContext,
    raw: dict[str, Any],
    session: AsyncClientSession | None = None,
) -> dict[str, Any]:
    current = await queue.policy.revalidate(access, action=Action.PURGE, session=session)
    command = JobCommand.model_validate(
        {key: raw[key] for key in JobCommand.model_fields if key in raw}
    )
    if (
        command.kind != "purge_material"
        or command.org_id != current.org_id
        or command.kg_id not in current.kg_ids
        or str(command.target_id) != raw["material_id"]
    ):
        raise inaccessible_reference()
    ns = {"org_id": str(command.org_id), "kg_id": str(command.kg_id)}
    intent = await queue.database.material_commands.find_one(
        ns
        | {
            "id": raw["command_id"],
            "job_id": str(command.id),
            "operation": "purge",
            "material_id": str(command.target_id),
            "state": {"$in": ["pending", "accepted"]},
        },
        session=session,
    )
    if intent is None or intent["actor"] != raw["actor"]:
        raise inconsistent()
    receipt = PurgeReceipt.model_validate(intent["receipt"])
    if (receipt.org_id, receipt.kg_id, receipt.material_id, receipt.job_id, receipt.state) != (
        command.org_id,
        command.kg_id,
        command.target_id,
        command.id,
        "purged" if intent["state"] == "accepted" else "pending",
    ):
        raise inconsistent()
    row = await queue.policy._material_acl_record(
        current, command.kg_id, command.target_id, session=session
    )
    if row is None:
        raise inaccessible_reference()
    stores = await queue.router.for_sources(current, command.kg_id)
    await stores.authority._require(
        "materials",
        ScopedRef(org_id=command.org_id, kg_id=command.kg_id, id=command.target_id),
        session=session,
    )
    return dict(intent)


async def verify_purge_acceptance(
    queue: JobQueue, access: AccessContext, raw: dict[str, Any]
) -> None:
    intent = await visible_purge_job(queue, access, raw)
    receipt = PurgeReceipt.model_validate(intent["receipt"])
    row = await queue.database.material_access.find_one(
        {
            "org_id": raw["org_id"],
            "kg_id": raw["kg_id"],
            "id": raw["target_id"],
            "purged": True,
            "purge_job_id": raw["id"],
            "revision": {"$gte": receipt.revision},
        }
    )
    if (
        intent["state"] != "accepted"
        or intent["phase"] != "purged"
        or raw["stage"] != "ready"
        or row is None
    ):
        raise inconsistent()
