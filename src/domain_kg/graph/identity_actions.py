"""Human identity admission uses node counters and immutable curation jobs."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4, uuid5

from pymongo import ReturnDocument
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation import authorize_curation
from domain_kg.graph.identity_commands import IdentityChange, IdentityImpact
from domain_kg.graph.identity_models import IdentityGroup, IdentityState
from domain_kg.graph.models import DomainSchema
from domain_kg.graph.repository import GraphData
from domain_kg.graph.schema import schema_for_kg
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.models import JobCommand
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.models import ManualProvenance, ScopedRef
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import ReadScope, RevisionReceipt
from domain_kg.storage.models import CurationRecord


@dataclass(frozen=True)
class IdentityControl:
    """Server-only compensation; never accepted in a client identity command."""

    original: ScopedRef
    rollback_of: UUID
    before_enabled: bool
    enabled: bool


def scope_key(public: bool, sources: tuple[UUID, ...]) -> str:
    return digest({"public": public, "material_ids": list(map(str, sorted(sources, key=str)))})


def target_state(entity: UUID, scope: str) -> UUID:
    return uuid5(entity, f"curation-scope/{scope}")


def snapshot(data: GraphData, members: frozenset[UUID], sources: tuple[UUID, ...]) -> IdentityState:
    context = sources or None
    groups: dict[UUID, IdentityGroup] = {}
    for member in sorted(members, key=str):
        canonical = data.entity_id(member, context)
        origins = data.identities.members(member, context) if data.identities else (member,)
        selected = tuple(value for value in origins if value in members)
        # The caller's explicit dependency closure must contain its representative.
        if canonical not in selected:
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Refresh the identity membership preview."
            )
        groups[canonical] = IdentityGroup(canonical_id=canonical, member_ids=selected)
    return IdentityState(groups=tuple(groups[key] for key in sorted(groups, key=str)))


class IdentityService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue, self.policy, self.database = queue, queue.policy, queue.database

    async def _actor(self, access: AccessContext, kg: UUID, public: bool) -> AccessContext:
        current = await self.policy.revalidate(access, action=Action.CURATE)
        if kg not in current.kg_ids or current.principal.kind != "human":
            raise inaccessible_reference()
        if public and not any(
            grant.kg_id == kg and grant.role == "owner" for grant in current.grants
        ):
            raise inaccessible_reference()
        return current

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
        if saved["digest"] != fingerprint or record["kind"] != "identity":
            raise DomainError(ErrorCode.IDEMPOTENCY_CONFLICT, "Curation key has different input.")
        receipt = RevisionReceipt.model_validate(saved["receipt"])
        if (receipt.org_id, receipt.kg_id, str(receipt.revision_id), str(receipt.job_id)) != (
            access.org_id,
            kg,
            record["id"],
            record["job_id"],
        ):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Identity receipt ownership differs."
            )
        return receipt

    async def _counters(
        self,
        access: AccessContext,
        kg: UUID,
        scope: str,
        members: frozenset[UUID],
        session: AsyncClientSession | None = None,
    ) -> tuple[tuple[UUID, int], ...]:
        values = []
        for entity in sorted(members, key=str):
            row = await self.database.curation_targets.find_one(
                {
                    "org_id": str(access.org_id),
                    "kg_id": str(kg),
                    "id": str(target_state(entity, scope)),
                },
                session=session,
            )
            if row and (row["kind"], row["target_id"], row["scope_key"]) != (
                "node",
                str(entity),
                scope,
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Identity target ownership differs."
                )
            values.append((entity, row["revision"] if row else 0))
        return tuple(values)

    async def apply(
        self, access: AccessContext, command: IdentityChange
    ) -> RevisionReceipt | IdentityImpact:
        command = IdentityChange.model_validate(command)
        public = command.curation_scope == "kg_public"
        current = await self._actor(access, command.kg_id, public)
        fingerprint = digest(
            {"operation": "identity_" + command.action, "command": command.model_dump(mode="json")}
        )
        if not command.dry_run:
            replay = await self._replay(
                current, command.kg_id, command.idempotency_key, fingerprint
            )
            if replay:
                return replay
        sources = tuple(sorted(command.source_ids, key=str))
        scope = scope_key(public, sources)
        graph = GraphService(self.queue)
        read = await graph.resolver.resolve(
            current,
            SearchRequest(
                kg_ids=(command.kg_id,),
                query=str(command.entity_id),
                mode="graph",
                filters=SearchFilters.for_curation(sources),
            ),
            command.kg_id,
        )
        data = await graph.repository.open(read)
        required = set(command.members)
        for entity in command.members:
            node = graph.repository.node(data, entity)
            if node is None:
                raise inaccessible_reference()
            if entity == command.entity_id and node.id != entity:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Use the current canonical identity target."
                )
            required.update(node.identity_members or (node.id,))
        if len(required) > 100:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Identity dependency closure exceeds 100 originals."
            )
        members = frozenset(required)
        counters = await self._counters(current, command.kg_id, scope, members)
        affected = tuple(
            row
            for row in data.rows
            if row.observation
            and row.observation.entity.id in members
            or row.claim
            and {row.claim.assertion.subject.id, row.claim.assertion.target.id}.intersection(
                members
            )
        )
        if command.dry_run:
            await graph.resolver.revalidate(read)
            return IdentityImpact(
                action=command.action,
                material_count=len(sources),
                member_ids=tuple(sorted(members, key=str)),
                member_revisions=counters,
                target_revision=dict(counters)[command.entity_id],
                affected_chunks=len(
                    {row.observation.chunk_id for row in affected if row.observation}
                ),
                affected_observations=sum(row.observation is not None for row in affected),
                affected_supports=sum(row.claim is not None for row in affected),
            )
        if members != command.members or dict(counters) != dict(command.expected_targets):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Refresh the identity membership and revision preview."
            )
        if len(read.current_view.projections) >= 1000:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Accepted identity metadata needs maintenance."
            )
        schema = await schema_for_kg(self.policy, current, command.kg_id)
        before = snapshot(data, members, sources)
        return await self._admit(
            current, command, fingerprint, scope, counters, before, read, schema
        )

    async def _admit(
        self,
        access: AccessContext,
        command: IdentityChange,
        fingerprint: str,
        scope: str,
        counters: tuple[tuple[UUID, int], ...],
        before: IdentityState,
        read: "ReadScope",
        schema: "DomainSchema",
        control: IdentityControl | None = None,
    ) -> RevisionReceipt:
        ns = {"org_id": str(access.org_id), "kg_id": str(command.kg_id)}
        public = command.curation_scope == "kg_public"
        sources = tuple(sorted(command.source_ids, key=str))
        stores = await self.queue.router.for_sources(access, command.kg_id)
        identifier, job_id = uuid4(), uuid4()
        key = self.queue.transactions.key(access, command.kg_id, command.idempotency_key)

        async def commit(session: AsyncClientSession) -> RevisionReceipt:
            current = await self.queue.transactions.fence(access, Action.CURATE, session)
            if control is not None:
                if not any(
                    grant.kg_id == command.kg_id and grant.role == "owner"
                    for grant in current.grants
                ):
                    raise inaccessible_reference()
                original = await authorize_curation(
                    self.queue,
                    current,
                    control.original.id,
                    kg_id=command.kg_id,
                    session=session,
                )
                if (
                    original["kind"] != "identity"
                    or original["state"] != "accepted"
                    or original.get("retracts") is not None
                    or original["target_id"] != str(command.entity_id)
                    or original["scope_key"] != scope
                    or not set(original["members"]).issubset(map(str, command.members))
                    or (control.original.org_id, control.original.kg_id)
                    != (current.org_id, command.kg_id)
                ):
                    raise inaccessible_reference()
            previous = await self.database.curation_commands.find_one(key, session=session)
            if previous:
                if previous["digest"] != fingerprint:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Curation key has different input."
                    )
                existing = await authorize_curation(
                    self.queue,
                    current,
                    UUID(previous["revision_id"]),
                    kg_id=command.kg_id,
                    write=True,
                    session=session,
                )
                receipt = RevisionReceipt.model_validate(previous["receipt"])
                if existing["job_id"] != str(receipt.job_id):
                    raise inaccessible_reference()
                return receipt
            kg = await self.database.kgs.find_one(
                {
                    "org_id": ns["org_id"],
                    "id": ns["kg_id"],
                    "published_manifest_id": str(read.current_view.manifest_id),
                    "visibility_revision": read.visibility_revision,
                    "generation": str(read.view.generation),
                },
                session=session,
            )
            if kg is None:
                raise DomainError(ErrorCode.POLICY_CHANGED, "The identity source view changed.")
            ordered = await self.database.kgs.find_one_and_update(
                {"org_id": ns["org_id"], "id": ns["kg_id"]},
                {"$inc": {"identity_sequence": 1}},
                return_document=ReturnDocument.AFTER,
                session=session,
            )
            if ordered is None:
                raise inaccessible_reference()
            if await schema_for_kg(self.policy, current, command.kg_id, session=session) != schema:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The identity schema changed.")
            if dict(
                await self._counters(current, command.kg_id, scope, command.members, session)
            ) != dict(counters):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "An identity target revision changed."
                )
            versions = {}
            selected = {source.material_id: source.version_id for source in read.visible_sources}
            for material in sources:
                if not await self.policy.material_visible(
                    current, command.kg_id, material, session=session
                ):
                    raise inaccessible_reference()
                row = await self.database.material_access.find_one(
                    ns | {"id": str(material)}, session=session
                )
                if row is None or row.get("active_version_id") != str(selected.get(material)):
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "An identity source version changed."
                    )
                reference = ScopedRef(
                    org_id=current.org_id, kg_id=command.kg_id, id=row["active_version_id"]
                )
                await stores.authority.require_version(reference, session=session)
                version = await stores.authority._require(
                    "material_versions", reference, session=session
                )
                if version["material"]["id"] != str(material):
                    raise inaccessible_reference()
                versions[str(material)] = row["active_version_id"]
            targets = []
            for entity, expected in counters:
                reference = ScopedRef(org_id=current.org_id, kg_id=command.kg_id, id=entity)
                await stores.authority._require("entities", reference, session=session)
                state_id = target_state(entity, scope)
                target_key = ns | {"id": str(state_id)}
                if (
                    await self.database.curation_targets.find_one(target_key, session=session)
                    is None
                ):
                    await self.database.curation_targets.insert_one(
                        target_key
                        | {
                            "kind": "node",
                            "target_id": str(entity),
                            "scope_key": scope,
                            "revision": 0,
                        },
                        session=session,
                    )
                targets.append(
                    {
                        "target_id": str(entity),
                        "state_id": str(state_id),
                        "expected_revision": expected,
                    }
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
                "kind": "identity",
                "operation": command.action,
                "target_id": str(command.entity_id),
                "target_state_id": str(target_state(command.entity_id, scope)),
                "scope_key": scope,
                "expected_revision": command.expected_revision,
                "target_revisions": targets,
                "members": list(map(str, sorted(command.members, key=str))),
                "public": public,
                "material_ids": list(map(str, sources)),
                "source_versions": versions,
                "before": before.model_dump(mode="json"),
                "after": command.state.model_dump(mode="json"),
                "retracts": None,
                "rollback_of": None,
                "state": "pending",
                "provenance": provenance.model_dump(mode="json"),
                "actor": current.principal.model_dump(mode="json"),
                "job_id": str(job_id),
                "domain_schema": schema.model_dump(mode="json"),
                "created_at": now(),
                "identity_sequence": ordered["identity_sequence"],
            }
            if control is not None:
                record.update(
                    operation="identity_control",
                    before={"enabled": control.before_enabled},
                    after={"enabled": control.enabled},
                    retracts=control.original.model_dump(mode="json"),
                    rollback_of=str(control.rollback_of),
                )
            await self.database.curation_revisions.insert_one(record, session=session)
            await self.database.curation_access.insert_one(
                ns
                | {
                    "id": str(identifier),
                    "kind": "identity",
                    "public": public,
                    "material_ids": list(map(str, sources)),
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
                    "material_ids": list(map(str, sources)),
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

        receipt = await self.queue.transactions.transaction(commit)
        await authorize_curation(
            self.queue, access, receipt.revision_id, kg_id=command.kg_id, write=True
        )
        return receipt
