"""Immediate human node fences, distinct from immutable field/vector publications."""

from typing import Any
from uuid import UUID, uuid4, uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.repository import GraphData
from domain_kg.graph.service import GraphService
from domain_kg.graph.visibility_models import (
    CurationCommand,
    CurationImpact,
    CurationReceipt,
    NodeFence,
)
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.models import BlobRef
from domain_kg.materials.service import digest
from domain_kg.models import ManualProvenance, ScopedRef
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import ReadScope
from domain_kg.revisions.projections import CurationEmbeddedDocument, ProjectionBody
from domain_kg.revisions.publication import accepted_document, verify_document
from domain_kg.storage.blobs import BlobStore
from domain_kg.storage.models import CurationRecord


def scope_identity(public: bool, materials: tuple[UUID, ...]) -> str:
    return digest({"public": public, "material_ids": list(map(str, materials))})


class NodeVisibilityService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.database, self.policy = queue, queue.database, queue.policy

    async def initialize(self) -> None:
        await self.database.node_visibility.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )
        await self.database.curation_revisions.create_index(
            [
                ("org_id", 1),
                ("kg_id", 1),
                ("kind", 1),
                ("state", 1),
                ("target_state_id", 1),
                ("expected_revision", -1),
            ]
        )
        await self.database.node_action_commands.create_index(
            [("org_id", 1), ("kg_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)], unique=True
        )

    async def _sources(
        self,
        access: AccessContext,
        kg: UUID,
        materials: tuple[UUID, ...],
        session: AsyncClientSession | None = None,
    ) -> dict[str, str]:
        stores = await self.queue.router.for_sources(access, kg)
        result = {}
        for material in materials:
            if not await self.policy.material_visible(access, kg, material, session=session):
                raise inaccessible_reference()
            row = await self.database.material_access.find_one(
                {
                    "org_id": str(access.org_id),
                    "kg_id": str(kg),
                    "id": str(material),
                },
                session=session,
            )
            if row is None or not row.get("active_version_id"):
                raise inaccessible_reference()
            version = ScopedRef(org_id=access.org_id, kg_id=kg, id=row["active_version_id"])
            await stores.authority.require_version(version, session=session)
            raw = await stores.authority._require("material_versions", version, session=session)
            if raw["material"]["id"] != str(material):
                raise inaccessible_reference()
            result[str(material)] = str(version.id)
        return result

    async def _replay(
        self,
        access: AccessContext,
        command: CurationCommand,
        fingerprint: str,
        *,
        session: AsyncClientSession | None = None,
    ) -> CurationReceipt | None:
        saved = await self.database.node_action_commands.find_one(
            self.queue.transactions.key(access, command.kg_id, command.idempotency_key),
            session=session,
        )
        if saved is None:
            return None
        record = await self.database.curation_revisions.find_one(
            {
                "org_id": str(access.org_id),
                "kg_id": str(command.kg_id),
                "id": saved["revision_id"],
                "kind": "visibility",
                "state": "accepted",
            },
            session=session,
        )
        if record is None:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Node action ownership differs.")
        await self._sources(
            access, command.kg_id, tuple(UUID(x) for x in record["material_ids"]), session
        )
        if record["public"] and not any(
            grant.kg_id == command.kg_id and grant.role == "owner" for grant in access.grants
        ):
            raise inaccessible_reference()
        if any(
            record["actor"].get(key) != getattr(access.principal, key)
            for key in ("issuer", "subject", "kind")
        ):
            raise inaccessible_reference()
        if saved["digest"] != fingerprint:
            raise DomainError(
                ErrorCode.IDEMPOTENCY_CONFLICT, "Node action key has different input."
            )
        stores = await self.queue.router.for_sources(access, command.kg_id)
        reference = ScopedRef(org_id=access.org_id, kg_id=command.kg_id, id=record["id"])
        canonical = await stores.authority._require("curations", reference, session=session)
        if (canonical["actor"], canonical["reason"]) != (
            record["provenance"]["actor"],
            record["provenance"]["reason"],
        ):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Node action provenance differs.")
        receipt = CurationReceipt.model_validate(saved["receipt"])
        expected = CurationReceipt.model_validate(record["receipt"])
        if (
            receipt != expected
            or receipt.job_id is not None
            or (
                receipt.action != ("suppress" if record["after"]["suppressed"] else "restore")
                or receipt.target_revision != record["expected_revision"] + 1
                or receipt.visibility_revision != record["visibility_revision"]
            )
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Node action receipt backlink differs."
            )
        if (receipt.org_id, receipt.kg_id, receipt.revision_id, receipt.target_id) != (
            access.org_id,
            command.kg_id,
            UUID(record["id"]),
            command.entity_id,
        ):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Node action receipt differs.")
        return receipt

    @staticmethod
    def _target_ownership(state: dict[str, Any], entity: UUID, scope_key: str) -> None:
        if (
            state.get("kind") != "node"
            or state.get("target_id") != str(entity)
            or state.get("scope_key") != scope_key
            or type(state.get("revision")) is not int
            or state["revision"] < 0
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Node target state ownership differs."
            )

    async def _authority_data(self, scope: ReadScope) -> GraphData:
        from domain_kg.graph.repository import scoped_graph_data

        rows: list[ProjectionBody] = []
        directives: list[ManualNode | ManualRelation] = []
        for binding in scope.projections:
            if (
                binding.source not in scope.visible_sources
                and binding.curation not in scope.visible_curations
            ):
                continue
            document, _, _ = await accepted_document(self.queue, scope.access, binding)
            rows.extend(
                ProjectionBody.model_validate(row.model_dump(exclude={"vector"}))
                for row in document.rows
            )
            if isinstance(document, CurationEmbeddedDocument):
                directives.append(document.directive)
        return await scoped_graph_data(self.queue, scope, tuple(rows), tuple(directives))

    async def _readiness(self, scope: ReadScope) -> None:
        stores = await self.queue.router.for_kg(scope.access, scope.view.kg_id)
        for binding in scope.projections:
            if (
                binding.source not in scope.visible_sources
                and binding.curation not in scope.visible_curations
            ):
                continue
            document, batch, receipts = await accepted_document(self.queue, scope.access, binding)
            if await verify_document(stores, document, batch) != receipts:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Node restore receipts differ."
                )
            if binding.source:
                source = binding.source
                raw = await stores.authority._database.version_blobs.find_one(
                    {
                        "org_id": str(source.org_id),
                        "kg_id": str(source.kg_id),
                        "id": str(source.version_id),
                        "material_id": str(source.material_id),
                        "state": {"$in": ["accepted", "retained"]},
                    }
                )
                if raw is None:
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT, "Node original is missing."
                    )
                blob = BlobRef.model_validate(raw["blob"])
                if (blob.org_id, blob.kg_id, blob.material_id, blob.version_id) != (
                    source.org_id,
                    source.kg_id,
                    source.material_id,
                    source.version_id,
                ):
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT, "Node original ownership differs."
                    )
                async for _ in BlobStore(stores.blobs).open(blob):
                    pass

    async def apply(
        self,
        access: AccessContext,
        command: CurationCommand,
        *,
        intent_hash: str | None = None,
        rollback_of: UUID | None = None,
    ) -> CurationReceipt | CurationImpact:
        command = CurationCommand.model_validate(command)
        current = await self.policy.revalidate(access, action=Action.CURATE)
        public = command.curation_scope == "kg_public"
        if command.kg_id not in current.kg_ids or current.principal.kind != "human":
            raise inaccessible_reference()
        if public and not any(
            g.kg_id == command.kg_id and g.role == "owner" for g in current.grants
        ):
            raise inaccessible_reference()
        fingerprint = intent_hash or digest(
            {"operation": "node_visibility", "command": command.model_dump(mode="json")}
        )
        if not command.dry_run:
            replay = await self._replay(current, command, fingerprint)
            if replay:
                return replay
        graph = GraphService(self.queue)
        scope = await graph.resolver.resolve(
            current,
            SearchRequest(
                kg_ids=(command.kg_id,),
                query=str(command.entity_id),
                mode="graph",
                filters=SearchFilters(material_ids=command.source_ids),
            ),
            command.kg_id,
        )
        data = await self._authority_data(scope)
        materials = tuple(sorted(command.source_ids, key=str))
        if command.action == "suppress":
            node = graph.repository.node(data, command.entity_id)
            if node is None:
                raise inaccessible_reference()
            if not public and not materials:
                owners = {item.material_id for item in node.observations}
                owners.update(m for directive in node.curations for m in directive.material_ids)
                for row in data.supports:
                    if (
                        row.manual_relation
                        and row.claim
                        and command.entity_id
                        in (
                            row.claim.assertion.subject.id,
                            row.claim.assertion.target.id,
                        )
                    ):
                        owners.update(row.manual_relation.material_ids)
                materials = tuple(sorted(owners, key=str))
        elif not public and not materials:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "Select the original fence's source targets."
            )
        if public:
            materials = ()
        elif not materials or len(materials) > 100:
            raise inaccessible_reference()
        source_versions = await self._sources(current, command.kg_id, materials)
        scope_key = scope_identity(public, materials)
        target_state_id = uuid5(command.entity_id, f"curation-scope/{scope_key}")
        ns: dict[str, Any] = {"org_id": str(current.org_id), "kg_id": str(command.kg_id)}
        target_key = ns | {"id": str(target_state_id)}
        target = await self.database.curation_targets.find_one(target_key)
        if target:
            self._target_ownership(target, command.entity_id, scope_key)
        revision = target["revision"] if target else 0
        if revision != command.expected_revision:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The node revision changed.")
        prior = await self.database.node_visibility.find_one(target_key)
        if command.action == "restore" and (prior is None or not prior["suppressed"]):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The matching node fence is not active.")
        if command.dry_run:
            await graph.resolver.revalidate(scope)
            return CurationImpact(
                material_count=len(materials) if not public else len(scope.visible_sources),
                affected_chunks=len(
                    {
                        (item.material_id, item.chunk_id)
                        for item in data.observations
                        if item.entity.id == command.entity_id
                    }
                ),
                affected_observations=sum(
                    item.entity.id == command.entity_id for item in data.observations
                ),
                affected_supports=sum(
                    bool(
                        row.claim
                        and command.entity_id
                        in (
                            row.claim.assertion.subject.id,
                            row.claim.assertion.target.id,
                        )
                    )
                    for row in data.supports
                ),
                target_revision=revision,
            )
        if command.action == "restore":
            await self._readiness(scope)
        await graph.resolver.revalidate(scope)
        await self.initialize()
        identifier = uuid4()
        stores = await self.queue.router.for_sources(current, command.kg_id)
        entity = ScopedRef(org_id=current.org_id, kg_id=command.kg_id, id=command.entity_id)

        async def commit(session: AsyncClientSession) -> CurationReceipt:
            actor = await self.queue.transactions.fence(current, Action.CURATE, session)
            old = await self._replay(actor, command, fingerprint, session=session)
            if old:
                return old
            if await self._sources(actor, command.kg_id, materials, session) != source_versions:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "A node source version changed.")
            await stores.authority._require("entities", entity, session=session)
            marker = await self.database.kgs.find_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "published_manifest_id": str(scope.current_view.manifest_id),
                    "generation": str(scope.current_view.generation),
                    "visibility_revision": scope.visibility_revision,
                },
                session=session,
            )
            if marker is None:
                raise DomainError(ErrorCode.POLICY_CHANGED, "The node source view changed.")
            state = await self.database.curation_targets.find_one(target_key, session=session)
            if state is not None:
                self._target_ownership(state, command.entity_id, scope_key)
            if state is None:
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
                state = {"revision": 0}
            if state["revision"] != command.expected_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The node revision changed.")
            old_fence = await self.database.node_visibility.find_one(target_key, session=session)
            if old_fence != prior:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The node fence changed.")
            member = await self.database.members.find_one(
                {
                    "org_id": str(actor.org_id),
                    "issuer": actor.principal.issuer,
                    "subject": actor.principal.subject,
                    "suspended": False,
                },
                session=session,
            )
            if member is None:
                raise inaccessible_reference()
            provenance = ManualProvenance(actor=f"member:{member['id']}", reason=command.reason)
            await stores.authority.insert(
                CurationRecord(
                    **ns,
                    id=identifier,
                    actor=provenance.actor,
                    reason=provenance.reason,
                ),
                session=session,
            )
            receipt = CurationReceipt(
                **ns,
                revision_id=identifier,
                target_id=command.entity_id,
                action=command.action,
                target_revision=revision + 1,
                visibility_revision=scope.visibility_revision + 1,
            )
            await self.database.curation_revisions.insert_one(
                ns
                | {
                    "id": str(identifier),
                    "kind": "visibility",
                    "target_id": str(command.entity_id),
                    "target_state_id": str(target_state_id),
                    "scope_key": scope_key,
                    "expected_revision": revision,
                    "visibility_revision": scope.visibility_revision + 1,
                    "public": public,
                    "material_ids": list(map(str, materials)),
                    "source_versions": source_versions,
                    "before": {"suppressed": bool(prior and prior["suppressed"])},
                    "after": {"suppressed": command.action == "suppress"},
                    "state": "accepted",
                    "rollback_of": str(rollback_of) if rollback_of else None,
                    "job_id": None,
                    "actor": actor.principal.model_dump(mode="json"),
                    "provenance": provenance.model_dump(mode="json"),
                    "created_at": now(),
                    "receipt": receipt.model_dump(mode="json"),
                },
                session=session,
            )
            await self.database.node_visibility.update_one(
                target_key,
                {
                    "$set": {
                        "entity_id": str(command.entity_id),
                        "curation": ScopedRef(**ns, id=identifier).model_dump(mode="json"),
                        "scope_key": scope_key,
                        "revision": revision + 1,
                        "public": public,
                        "material_ids": list(map(str, materials)),
                        "suppressed": command.action == "suppress",
                    }
                },
                upsert=True,
                session=session,
            )
            changed = await self.database.curation_targets.update_one(
                target_key | {"revision": revision},
                {"$inc": {"revision": 1}},
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The node target changed.")
            changed = await self.database.kgs.update_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "visibility_revision": scope.visibility_revision,
                },
                {"$inc": {"visibility_revision": 1}},
                session=session,
            )
            if not changed.matched_count:
                raise DomainError(ErrorCode.POLICY_CHANGED, "Node visibility changed.")
            await self.database.node_action_commands.insert_one(
                self.queue.transactions.key(actor, command.kg_id, command.idempotency_key)
                | {
                    "digest": fingerprint,
                    "revision_id": str(identifier),
                    "receipt": receipt.model_dump(mode="json"),
                },
                session=session,
            )
            await self.database.audit.insert_one(
                ns
                | {
                    "id": str(identifier),
                    "kind": "node_" + command.action,
                    "target_id": str(command.entity_id),
                    "revision_id": str(identifier),
                    "actor": actor.principal.model_dump(mode="json"),
                    "created_at": now(),
                },
                session=session,
            )
            await self.database.outbox.insert_one(
                ns
                | {
                    "id": str(uuid5(identifier, "node-visibility-event")),
                    "kind": "node_visibility_changed",
                    "target_id": str(command.entity_id),
                    "revision_id": str(identifier),
                    "visibility_revision": scope.visibility_revision + 1,
                    "created_at": now(),
                    "delivered": False,
                },
                session=session,
            )
            return receipt

        result = await self.queue.transactions.transaction(commit)
        await self.policy.revalidate(current, action=Action.CURATE)
        await self._sources(current, command.kg_id, materials)
        return result

    async def load(
        self, access: AccessContext, kg: UUID, materials: tuple[UUID, ...]
    ) -> tuple[NodeFence, ...]:
        current = await self.policy.revalidate(access, action=Action.READ)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        ns: dict[str, Any] = {"org_id": str(current.org_id), "kg_id": str(kg)}
        cursor = await self.database.curation_revisions.aggregate(
            [
                {"$match": ns | {"kind": "visibility", "state": "accepted"}},
                {"$sort": {"target_state_id": 1, "expected_revision": -1}},
                {"$group": {"_id": "$target_state_id", "record": {"$first": "$$ROOT"}}},
                {"$match": {"record.after.suppressed": True}},
            ]
        )
        records = [row["record"] async for row in cursor]
        if not records:
            return ()
        stores = await self.queue.router.for_sources(current, kg)
        result = []
        for record in records:
            row = await self.database.node_visibility.find_one(
                ns | {"id": record["target_state_id"]}
            )
            if row is None or not row.get("suppressed"):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "An accepted node fence mirror is missing."
                )
            fence = NodeFence.model_validate({key: row[key] for key in NodeFence.model_fields})
            if any(
                (
                    str(fence.curation.id) != record["id"],
                    record["target_id"] != str(fence.entity_id),
                    record["target_state_id"] != row["id"],
                    record["expected_revision"] + 1 != fence.revision,
                    record["public"] != fence.public,
                    record["material_ids"] != list(map(str, fence.material_ids)),
                    record["after"] != {"suppressed": True},
                    record["actor"]["kind"] != "human",
                    record.get("job_id") is not None,
                    record["scope_key"] != scope_identity(fence.public, fence.material_ids),
                )
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Node fence authority differs."
                )
            canonical = await stores.authority._require("curations", fence.curation)
            if (canonical["actor"], canonical["reason"]) != (
                record["provenance"]["actor"],
                record["provenance"]["reason"],
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Node fence provenance differs."
                )
            await stores.authority._require("entities", ScopedRef(**ns, id=fence.entity_id))
            for material in fence.material_ids:
                await stores.authority._require("materials", ScopedRef(**ns, id=material))
            if fence.public or set(fence.material_ids).intersection(materials):
                result.append(fence)
                if len(result) > 1000:
                    raise DomainError(
                        ErrorCode.QUOTA_EXCEEDED, "This read exceeds the node-fence budget."
                    )
        await self.policy.revalidate(current)
        return tuple(result)
