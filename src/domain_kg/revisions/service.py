"""Admit human commands under current policy; immutable workers publish their fields."""

from typing import Any
from uuid import UUID, uuid4, uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation import authorize_curation
from domain_kg.graph.curation_models import NodeFields
from domain_kg.graph.identity_commands import IdentityChange, IdentityImpact
from domain_kg.graph.manual_relations import RelationFields
from domain_kg.graph.models import AssertionRecord, DomainSchema
from domain_kg.graph.schema import schema_for_kg
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobCommand
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.models import ManualProvenance, ScopedRef
from domain_kg.providers.service import ProviderService
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import (
    CurationCommand,
    CurationImpact,
    CurationReceipt,
    EditCommand,
    HistoryPage,
    HistoryRequest,
    NodeUpdate,
    RelationUpdate,
    RevisionReceipt,
    RollbackRevision,
)
from domain_kg.storage.models import CurationRecord


class _NodeRevision(EditCommand):
    """Server-only state compensation; public NodeUpdate cannot replace history."""

    entity_id: UUID
    changes: NodeFields
    replace_state: bool = False
    rollback_of: UUID | None = None

    @property
    def target_id(self) -> UUID:
        return self.entity_id

    @property
    def kind(self) -> str:
        return "node"


class _RelationRevision(EditCommand):
    assertion_id: UUID
    changes: RelationFields
    replace_state: bool = False
    rollback_of: UUID | None = None

    @property
    def target_id(self) -> UUID:
        return self.assertion_id

    @property
    def kind(self) -> str:
        return "relation"


class RevisionService:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers = queue, providers
        self.policy, self.database = queue.policy, queue.database

    async def initialize(self) -> None:
        for name in ("curation_revisions", "curation_targets"):
            await self.database[name].create_index(
                [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
            )
        await self.database.curation_revisions.create_index(
            [
                ("org_id", 1),
                ("kg_id", 1),
                ("target_id", 1),
                ("kind", 1),
                ("created_at", 1),
                ("id", 1),
            ]
        )
        await self.database.curation_commands.create_index(
            [("org_id", 1), ("kg_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)], unique=True
        )
        await self.database.curation_scopes.create_index(
            [("org_id", 1), ("kg_id", 1), ("target_id", 1), ("scope_key", 1)], unique=True
        )

    async def curate_node(
        self, access: AccessContext, command: CurationCommand
    ) -> CurationReceipt | CurationImpact | IdentityImpact | RevisionReceipt:
        from domain_kg.graph.node_actions import NodeVisibilityService

        await self.initialize()
        if command.action in ("merge", "split"):
            from domain_kg.graph.identity_actions import IdentityService

            return await IdentityService(self.queue).apply(
                access, IdentityChange.model_validate(command.model_dump())
            )
        return await NodeVisibilityService(self.queue).apply(access, command)

    async def get_revisions(self, access: AccessContext, request: HistoryRequest) -> HistoryPage:
        from domain_kg.revisions.history import RevisionHistory

        return await RevisionHistory(self.queue).get(access, request)

    async def update_node(self, access: AccessContext, command: NodeUpdate) -> RevisionReceipt:
        command = NodeUpdate.model_validate(command)
        return await self._submit(
            access,
            _NodeRevision.model_validate(command.model_dump()),
            intent_hash=digest(
                {"operation": "node_update", "command": command.model_dump(mode="json")}
            ),
        )

    async def update_relation(
        self, access: AccessContext, command: RelationUpdate
    ) -> RevisionReceipt:
        command = RelationUpdate.model_validate(command)
        return await self._submit(
            access,
            _RelationRevision.model_validate(command.model_dump()),
            intent_hash=digest(
                {"operation": "relation_update", "command": command.model_dump(mode="json")}
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

    async def rollback(
        self, access: AccessContext, command: RollbackRevision
    ) -> RevisionReceipt | CurationReceipt:
        command = RollbackRevision.model_validate(command)
        current = await self.policy.revalidate(access, action=Action.CURATE)
        if current.principal.kind != "human" or not any(
            grant.kg_id == command.kg_id and grant.role == "owner" for grant in current.grants
        ):
            raise inaccessible_reference()
        visibility = await self.database.curation_revisions.find_one(
            {
                "org_id": str(current.org_id),
                "kg_id": str(command.kg_id),
                "id": str(command.revision_id),
                "kind": "visibility",
                "state": "accepted",
            }
        )
        if visibility is not None:
            return await self._rollback_visibility(current, command)
        identity = await self.database.curation_revisions.find_one(
            {
                "org_id": str(current.org_id),
                "kg_id": str(command.kg_id),
                "id": str(command.revision_id),
                "kind": "identity",
            }
        )
        if identity is not None:
            from domain_kg.graph.identity_rollback import IdentityRollbackService

            return await IdentityRollbackService(self.queue).apply(current, command)
        fingerprint = digest({"operation": "rollback", "command": command.model_dump(mode="json")})
        previous = await self._replay(current, command.kg_id, command.idempotency_key, fingerprint)
        if previous is not None:
            return previous
        original = await authorize_curation(
            self.queue, current, command.revision_id, kg_id=command.kg_id
        )
        if original["kg_id"] != str(command.kg_id) or original["kind"] not in ("node", "relation"):
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
        revision_type = _NodeRevision if original["kind"] == "node" else _RelationRevision
        target_field = "entity_id" if original["kind"] == "node" else "assertion_id"
        return await self._submit(
            current,
            revision_type.model_validate(
                {
                    "kg_id": command.kg_id,
                    target_field: UUID(original["target_id"]),
                    "expected_revision": command.expected_current_revision,
                    "curation_scope": "kg_public" if original["public"] else "source",
                    "source_ids": tuple(UUID(value) for value in original["material_ids"]),
                    "reason": command.reason,
                    "idempotency_key": command.idempotency_key,
                    "changes": restored,
                    "replace_state": True,
                    "rollback_of": command.revision_id,
                }
            ),
            intent_hash=fingerprint,
        )

    async def _submit(
        self, access: AccessContext, command: _NodeRevision | _RelationRevision, *, intent_hash: str
    ) -> RevisionReceipt:
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
        if isinstance(command, _NodeRevision):
            self._validate_type(command, schema)
        graph = GraphService(self.queue)
        read_scope = await graph.resolver.resolve(
            current,
            SearchRequest(
                kg_ids=(command.kg_id,),
                query=str(command.target_id),
                mode="graph",
                filters=SearchFilters.for_curation(command.source_ids),
            ),
            command.kg_id,
        )
        data = await graph.repository.open(read_scope)
        base_fact: AssertionRecord | None = None
        if isinstance(command, _NodeRevision):
            node = graph.repository.node(data, command.target_id)
            if node is None:
                raise inaccessible_reference()
            if node.id != command.target_id:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Use the current canonical identity target."
                )
            if node.truncation and not (command.source_ids or public):
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Choose explicit source targets for this edit."
                )
            owners = {item.material_id for item in node.observations}
        else:
            rows = [
                row
                for row in data.supports
                if row.assertion and row.assertion.assertion.id == command.target_id and row.source
            ]
            if not rows:
                raise inaccessible_reference()
            base_fact = rows[0].assertion.assertion if rows[0].assertion else None
            owners = {row.source.material_id for row in rows if row.source}
            if command.source_ids and not set(command.source_ids).issubset(owners):
                raise inaccessible_reference()
        source_ids = () if public else tuple(sorted(set(command.source_ids) or owners, key=str))
        if not public and (not source_ids or len(source_ids) > 100):
            raise inaccessible_reference()
        ns = {"org_id": str(current.org_id), "kg_id": str(command.kg_id)}
        key = self.queue.transactions.key(current, command.kg_id, command.idempotency_key)
        fingerprint = intent_hash
        scope_identity: dict[str, Any] = {
            "public": public,
            "material_ids": list(map(str, source_ids)),
        }
        if command.kind == "relation":
            scope_identity["kind"] = "relation"
        scope_key = digest(scope_identity)
        target_state_id = uuid5(command.target_id, f"curation-scope/{scope_key}")
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
                "entities" if command.kind == "node" else "assertions",
                ScopedRef(org_id=current.org_id, kg_id=command.kg_id, id=command.target_id),
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
                        "kind": command.kind,
                        "target_id": str(command.target_id),
                        "scope_key": scope_key,
                        "revision": 0,
                    },
                    session=session,
                )
                target = {"revision": 0}
            if target["revision"] != command.expected_revision:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "The curation target revision changed."
                )
            scope = await self.database.curation_scopes.find_one(
                ns | {"target_id": str(command.target_id), "scope_key": scope_key}, session=session
            )
            before = scope["changes"] if scope else {}
            changes = command.changes.model_dump(mode="json", exclude_unset=True, exclude_none=True)
            after = changes if command.replace_state else before | changes
            if isinstance(command, _RelationRevision):
                await self._validate_relation(
                    current, command.kg_id, after, base_fact, public, source_ids, schema
                )
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
                "kind": command.kind,
                "target_id": str(command.target_id),
                "target_state_id": str(target_state_id),
                "public": public,
                "material_ids": list(map(str, source_ids)),
                "source_versions": sources,
                "scope_key": scope_key,
                "expected_revision": command.expected_revision,
                "state": "pending",
                "base_fact": base_fact.model_dump(mode="json")
                if base_fact is not None and not public
                else None,
                "before": before,
                "after": after,
                "rollback_of": str(command.rollback_of) if command.rollback_of else None,
                "provenance": provenance.model_dump(mode="json"),
                "actor": current.principal.model_dump(mode="json"),
                "job_id": str(job_id),
                "domain_schema": schema.model_dump(mode="json"),
                "created_at": now(),
            }
            if command.kind == "relation":
                from domain_kg.graph.manual_relations import ManualRelation
                from domain_kg.revisions.curation_publication import directive

                manual = directive(record)
                if not isinstance(manual, ManualRelation):
                    raise inaccessible_reference()
                if manual.fact is not None and manual.support is not None:
                    existing = await stores.authority._database.assertions.find_one(
                        ns | {"id": str(manual.fact.id)}, session=session
                    )
                    if existing is None:
                        await stores.authority.insert(manual.fact, session=session)
                    elif (
                        AssertionRecord.model_validate(
                            {key: value for key, value in existing.items() if key != "_id"}
                        )
                        != manual.fact
                    ):
                        raise DomainError(
                            ErrorCode.PROJECTION_INCONSISTENT, "Canonical claim identity differs."
                        )
                    await stores.authority.insert(manual.support, session=session)
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

    async def _validate_relation(
        self,
        access: AccessContext,
        kg: UUID,
        after: dict[str, Any],
        base: AssertionRecord | None,
        public: bool,
        source_ids: tuple[UUID, ...],
        schema: DomainSchema,
    ) -> None:
        semantic = {"subject_id", "target_id", "predicate", "polarity", "confidence"}
        if not semantic.intersection(after):
            return
        required = {"subject_id", "target_id", "predicate"}
        if public and not required.issubset(after):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "Public relationship claims require explicit subject, predicate and target.",
            )
        if not public and base is None:
            raise inaccessible_reference()
        subject = UUID(str(after.get("subject_id") or (base.subject.id if base else "")))
        target = UUID(str(after.get("target_id") or (base.target.id if base else "")))
        predicate = after.get("predicate") or (base.predicate if base else None)
        definition = next((item for item in schema.predicates if item.name == predicate), None)
        if definition is None:
            raise DomainError(ErrorCode.VALIDATION_ERROR, "Choose a configured predicate.")
        graph = GraphService(self.queue)
        scope = await graph.resolver.resolve(
            access,
            SearchRequest(
                kg_ids=(kg,),
                query=str(subject),
                mode="graph",
                filters=SearchFilters.for_curation(source_ids),
            ),
            kg,
        )
        data = await graph.repository.open(scope)
        for endpoint, allowed in (
            (subject, definition.subject_types),
            (target, definition.target_types),
        ):
            node = graph.repository.node(data, endpoint)
            if node is None:
                raise inaccessible_reference()
            if not set(node.entity_types).intersection(allowed):
                raise DomainError(ErrorCode.VALIDATION_ERROR, "Predicate endpoint type differs.")

    async def _rollback_visibility(
        self, access: AccessContext, command: RollbackRevision
    ) -> CurationReceipt:
        from domain_kg.graph.node_actions import NodeVisibilityService
        from domain_kg.graph.visibility_history import authorize_visibility_record

        original = await authorize_visibility_record(
            self.queue, access, command.kg_id, command.revision_id
        )
        fingerprint = digest(
            {"operation": "rollback_visibility", "command": command.model_dump(mode="json")}
        )
        action = CurationCommand(
            kg_id=command.kg_id,
            entity_id=original["target_id"],
            action="suppress" if original["before"]["suppressed"] else "restore",
            expected_revision=command.expected_current_revision,
            curation_scope="kg_public" if original["public"] else "source",
            source_ids=tuple(UUID(value) for value in original["material_ids"]),
            reason=command.reason,
            idempotency_key=command.idempotency_key,
        )
        result = await NodeVisibilityService(self.queue).apply(
            access, action, intent_hash=fingerprint, rollback_of=command.revision_id
        )
        if not isinstance(result, CurationReceipt):
            raise inaccessible_reference()
        return result
