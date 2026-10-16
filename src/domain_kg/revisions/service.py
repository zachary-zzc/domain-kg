"""Admit human commands under current policy; immutable workers publish their fields."""

from typing import Any
from uuid import UUID, uuid4, uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation import authorize_curation
from domain_kg.graph.curation_models import NodeFields
from domain_kg.graph.models import DomainSchema
from domain_kg.graph.schema import schema_for_kg
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobCommand
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.models import ManualProvenance, ScopedRef
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import EditCommand, NodeUpdate, RevisionReceipt, RollbackRevision
from domain_kg.storage.models import CurationRecord


class _NodeRevision(EditCommand):
    """Server-only state compensation; public NodeUpdate cannot replace history."""

    entity_id: UUID
    changes: NodeFields
    replace_state: bool = False
    rollback_of: UUID | None = None


class RevisionService:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers = queue, providers
        self.policy, self.database = queue.policy, queue.database

    async def initialize(self) -> None:
        for name in ("curation_revisions", "curation_targets"):
            await self.database[name].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )
        await self.database.curation_commands.create_index(
            [("org_id", 1), ("kg_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)], unique=True
        )
        await self.database.curation_scopes.create_index(
            [("org_id", 1), ("kg_id", 1), ("target_id", 1), ("scope_key", 1)], unique=True
        )

    async def update_node(self, access: AccessContext, command: NodeUpdate) -> RevisionReceipt:
        command = NodeUpdate.model_validate(command)
        return await self._submit_node(
            access,
            _NodeRevision.model_validate(command.model_dump()),
            intent_hash=digest(
                {"operation": "node_update", "command": command.model_dump(mode="json")}
            ),
        )

    async def _replay(
        self, access: AccessContext, kg: UUID, key: str, fingerprint: str
    ) -> RevisionReceipt | None:
        saved = await self.database.curation_commands.find_one(
            self.queue.transactions.key(access, kg, key)
        )
        if saved is None:
            return None
        record = await authorize_curation(
            self.queue, access, UUID(saved["revision_id"]), kg_id=kg, write=True
        )
        if saved["digest"] != fingerprint:
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Curation key has different input.")
        return self._receipt_for_record(access, kg, saved, record)

    @staticmethod
    def _receipt_for_record(
        access: AccessContext, kg: UUID, saved: dict[str, Any], record: dict[str, Any]
    ) -> RevisionReceipt:
        receipt = RevisionReceipt.model_validate(saved["receipt"])
        if (receipt.org_id, receipt.kg_id, str(receipt.revision_id)) != (
            access.org_id,
            kg,
            saved["revision_id"],
        ):
            raise inaccessible_reference()
        if (record["org_id"], record["kg_id"], record["id"]) != (
            str(access.org_id),
            str(kg),
            saved["revision_id"],
        ) or str(receipt.job_id) != record["job_id"]:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Curation receipt ownership differs."
            )
        return receipt

    async def rollback(self, access: AccessContext, command: RollbackRevision) -> RevisionReceipt:
        command = RollbackRevision.model_validate(command)
        current = await self.policy.revalidate(access, action=Action.CURATE)
        if current.principal.kind != "human" or not any(
            grant.kg_id == command.kg_id and grant.role == "owner" for grant in current.grants
        ):
            raise inaccessible_reference()
        fingerprint = digest({"operation": "rollback", "command": command.model_dump(mode="json")})
        previous = await self._replay(current, command.kg_id, command.idempotency_key, fingerprint)
        if previous is not None:
            return previous
        original = await authorize_curation(
            self.queue, current, command.revision_id, kg_id=command.kg_id
        )
        if original["kg_id"] != str(command.kg_id) or original["kind"] != "node":
            raise inaccessible_reference()
        if original["state"] != "accepted":
            raise DomainError(
                ErrorCode.REVISION_NOT_READY, "Only accepted revisions can be rolled back."
            )
        state = await self.database.curation_scopes.find_one(
            {
                "org_id": str(current.org_id),
                "kg_id": str(command.kg_id),
                "target_id": original["target_id"],
                "scope_key": original["scope_key"],
            }
        )
        if state is None:
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Current curation state is missing."
            )
        restored = dict(state["changes"])
        for field in set(original["before"]) | set(original["after"]):
            if original["before"].get(field) == original["after"].get(field):
                continue
            if field in original["before"]:
                restored[field] = original["before"][field]
            else:
                restored.pop(field, None)
        return await self._submit_node(
            current,
            _NodeRevision(
                kg_id=command.kg_id,
                entity_id=original["target_id"],
                expected_revision=command.expected_current_revision,
                curation_scope="kg_public" if original["public"] else "source",
                source_ids=tuple(UUID(value) for value in original["material_ids"]),
                reason=command.reason,
                idempotency_key=command.idempotency_key,
                changes=NodeFields.model_validate(restored),
                replace_state=True,
                rollback_of=command.revision_id,
            ),
            intent_hash=fingerprint,
        )

    async def _submit_node(
        self, access: AccessContext, command: _NodeRevision, *, intent_hash: str
    ) -> RevisionReceipt:
        command = _NodeRevision.model_validate(command)
        current = await self.policy.revalidate(access, action=Action.CURATE)
        if command.kg_id not in current.kg_ids or current.principal.kind != "human":
            raise inaccessible_reference()
        public = command.curation_scope == "kg_public"
        if public and (
            command.source_ids
            or not any(
                grant.kg_id == command.kg_id and grant.role == "owner" for grant in current.grants
            )
        ):
            raise inaccessible_reference()
        if len(command.source_ids) > 100 or len(set(command.source_ids)) != len(command.source_ids):
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Select at most 100 distinct source IDs.")
        previous = await self._replay(current, command.kg_id, command.idempotency_key, intent_hash)
        if previous is not None:
            return previous
        await self.initialize()
        stores = await self.queue.router.for_sources(current, command.kg_id)
        schema = await schema_for_kg(self.policy, current, command.kg_id)
        self._validate_type(command, schema)
        graph = GraphService(self.queue)
        read_scope = await graph.resolver.resolve(
            current,
            SearchRequest(
                kg_ids=(command.kg_id,),
                query=str(command.entity_id),
                mode="graph",
                filters=SearchFilters(material_ids=command.source_ids),
            ),
            command.kg_id,
        )
        node = graph.repository.node(await graph.repository.open(read_scope), command.entity_id)
        if node is None:
            raise inaccessible_reference()
        if node.truncation and not (command.source_ids or public):
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Choose explicit source targets for this edit."
            )
        source_ids = (
            ()
            if public
            else tuple(
                sorted(
                    set(command.source_ids) or {item.material_id for item in node.observations},
                    key=str,
                )
            )
        )
        if not public and (not source_ids or len(source_ids) > 100):
            raise inaccessible_reference()
        ns = {"org_id": str(current.org_id), "kg_id": str(command.kg_id)}
        key = self.queue.transactions.key(current, command.kg_id, command.idempotency_key)
        fingerprint = intent_hash
        scope_key = digest({"public": public, "material_ids": list(map(str, source_ids))})
        target_state_id = uuid5(command.entity_id, f"curation-scope/{scope_key}")
        identifier, job_id = uuid4(), uuid4()

        async def admit(session: AsyncClientSession) -> RevisionReceipt:
            await self.queue.transactions.fence(current, Action.CURATE, session)
            old_command = await self.database.curation_commands.find_one(key, session=session)
            if old_command:
                if old_command["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Curation key has different input."
                    )
                existing_record = await authorize_curation(
                    self.queue,
                    current,
                    UUID(old_command["revision_id"]),
                    kg_id=command.kg_id,
                    write=True,
                    session=session,
                )
                return self._receipt_for_record(
                    current, command.kg_id, old_command, existing_record
                )
            if await schema_for_kg(self.policy, current, command.kg_id, session=session) != schema:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The domain schema changed.")
            marker = await self.database.kgs.find_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "published_manifest_id": str(read_scope.current_view.manifest_id),
                    "visibility_revision": read_scope.visibility_revision,
                    "generation": str(read_scope.view.generation),
                },
                session=session,
            )
            if marker is None:
                raise DomainError(ErrorCode.POLICY_CHANGED, "The curation source view changed.")
            await stores.authority._require(
                "entities",
                ScopedRef(org_id=current.org_id, kg_id=command.kg_id, id=command.entity_id),
                session=session,
            )
            sources: dict[str, str] = {}
            for material in source_ids:
                if not await self.policy.material_visible(
                    current, command.kg_id, material, session=session
                ):
                    raise inaccessible_reference()
                row = await self.database.material_access.find_one(
                    ns | {"id": str(material)}, session=session
                )
                if row is None or not row.get("active_version_id"):
                    raise inaccessible_reference()
                source_ref = ScopedRef(
                    org_id=current.org_id, kg_id=command.kg_id, id=row["active_version_id"]
                )
                await stores.authority.require_version(source_ref, session=session)
                version = await stores.authority._require(
                    "material_versions", source_ref, session=session
                )
                if version["material"]["id"] != str(material):
                    raise inaccessible_reference()
                sources[str(material)] = row["active_version_id"]
            target_key = ns | {"id": str(target_state_id)}
            target = await self.database.curation_targets.find_one(target_key, session=session)
            if target is None:
                await self.database.curation_targets.insert_one(
                    target_key
                    | {
                        "kind": "node",
                        "target_id": str(command.entity_id),
                        "scope_key": scope_key,
                        "revision": 0,
                    },
                    session=session,
                )
                target = {"revision": 0}
            if target["revision"] != command.expected_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The node revision changed.")
            scope = await self.database.curation_scopes.find_one(
                ns | {"target_id": str(command.entity_id), "scope_key": scope_key}, session=session
            )
            before = scope["changes"] if scope else {}
            changes = command.changes.model_dump(mode="json", exclude_unset=True, exclude_none=True)
            after = changes if command.replace_state else before | changes
            member = await self.database.members.find_one(
                {
                    "org_id": str(current.org_id),
                    "issuer": current.principal.issuer,
                    "subject": current.principal.subject,
                    "suspended": False,
                },
                session=session,
            )
            if member is None:
                raise inaccessible_reference()
            provenance = ManualProvenance(actor=f"member:{member['id']}", reason=command.reason)
            await stores.authority.insert(
                CurationRecord(
                    org_id=current.org_id,
                    kg_id=command.kg_id,
                    id=identifier,
                    actor=provenance.actor,
                    reason=provenance.reason,
                ),
                session=session,
            )
            receipt = RevisionReceipt(
                org_id=current.org_id,
                kg_id=command.kg_id,
                revision_id=identifier,
                state="pending",
                job_id=job_id,
            )
            record: dict[str, Any] = ns | {
                "id": str(identifier),
                "kind": "node",
                "target_id": str(command.entity_id),
                "target_state_id": str(target_state_id),
                "public": public,
                "material_ids": list(map(str, source_ids)),
                "source_versions": sources,
                "scope_key": scope_key,
                "expected_revision": command.expected_revision,
                "state": "pending",
                "before": before,
                "after": after,
                "rollback_of": str(command.rollback_of) if command.rollback_of else None,
                "provenance": provenance.model_dump(mode="json"),
                "actor": current.principal.model_dump(mode="json"),
                "job_id": str(job_id),
                "domain_schema": schema.model_dump(mode="json"),
                "created_at": now(),
            }
            await self.database.curation_revisions.insert_one(record, session=session)
            await self.database.curation_access.insert_one(
                ns
                | {
                    "id": str(identifier),
                    "public": public,
                    "material_ids": list(map(str, source_ids)),
                    "suppressed": False,
                },
                session=session,
            )
            job = JobCommand(
                org_id=current.org_id,
                kg_id=command.kg_id,
                id=job_id,
                kind="curate",
                target_id=identifier,
                idempotency_key=command.idempotency_key,
            )
            await self.database.jobs.insert_one(
                job.model_dump(mode="json")
                | {
                    "actor": current.principal.model_dump(mode="json"),
                    "material_ids": list(map(str, source_ids)),
                    "stage": "queued",
                    "fence": 0,
                    "attempts": 0,
                    "created_at": now(),
                },
                session=session,
            )
            await self.database.curation_commands.insert_one(
                key
                | {
                    "digest": fingerprint,
                    "revision_id": str(identifier),
                    "receipt": receipt.model_dump(mode="json"),
                },
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
            return receipt

        result = await self.queue.transactions.transaction(admit)
        await authorize_curation(
            self.queue, current, result.revision_id, kg_id=command.kg_id, write=True
        )
        return result

    @staticmethod
    def _validate_type(command: _NodeRevision, schema: DomainSchema) -> None:
        if command.changes.entity_type and command.changes.entity_type not in schema.entity_types:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Choose a configured entity type.")
