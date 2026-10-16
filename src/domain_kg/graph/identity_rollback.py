"""Owner compensation replays partitions without restoring historical permissions."""

from uuid import uuid4

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation import authorize_curation
from domain_kg.graph.identity_actions import IdentityControl, IdentityService, scope_key
from domain_kg.graph.identity_commands import IdentityChange
from domain_kg.graph.identity_models import IdentityState, ManualIdentity
from domain_kg.graph.identity_partition import IdentityContext, IdentityPartition
from domain_kg.graph.identity_records import identity_directive
from domain_kg.graph.schema import schema_for_kg
from domain_kg.graph.service import GraphService
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.queue import JobQueue, now
from domain_kg.materials.service import digest
from domain_kg.models import ManualProvenance, Namespace, ScopedRef
from domain_kg.retrieval.models import SearchFilters, SearchRequest
from domain_kg.revisions.models import RevisionReceipt, RollbackRevision


class IdentityRollbackService:
    def __init__(self, queue: JobQueue) -> None:
        self.queue = queue
        self.identities = IdentityService(queue)

    async def apply(self, access: AccessContext, command: RollbackRevision) -> RevisionReceipt:
        current = await self.identities._actor(access, command.kg_id, True)
        fingerprint = digest(
            {"operation": "identity_rollback", "command": command.model_dump(mode="json")}
        )
        replay = await self.identities._replay(
            current, command.kg_id, command.idempotency_key, fingerprint
        )
        if replay:
            return replay
        selected = await authorize_curation(
            self.queue, current, command.revision_id, kg_id=command.kg_id
        )
        if selected["kind"] != "identity" or selected["state"] != "accepted":
            raise DomainError(ErrorCode.REVISION_NOT_READY, "Select an accepted identity revision.")
        original = selected
        if selected.get("retracts") is not None:
            reference = ScopedRef.model_validate(selected["retracts"])
            original = await authorize_curation(
                self.queue, current, reference.id, kg_id=command.kg_id
            )
        if original["kind"] != "identity" or original.get("retracts") is not None:
            raise inaccessible_reference()
        directive = identity_directive(original)
        sources = tuple(sorted(directive.material_ids, key=str))
        graph = GraphService(self.queue)
        read = await graph.resolver.resolve(
            current,
            SearchRequest(
                kg_ids=(command.kg_id,),
                query=str(directive.target.id),
                mode="graph",
                filters=SearchFilters.for_curation(sources),
            ),
            command.kg_id,
        )
        data = await graph.repository.open(read)
        active = data.identities
        if active is None or directive.curation not in {
            item.curation for item in active.directives
        }:
            raise inaccessible_reference()
        controls = [item for item in active.directives if item.retracts == directive.curation]
        before_enabled = controls[-1].enabled if controls else True
        enabled = not selected["after"]["enabled"] if selected.get("retracts") else False
        if before_enabled == enabled:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "This identity state is already current.")
        proposal = ManualIdentity(
            curation=ScopedRef(org_id=current.org_id, kg_id=command.kg_id, id=uuid4()),
            target=directive.target,
            provenance=ManualProvenance(actor="owner-preview", reason=command.reason),
            revision=command.expected_current_revision + 1,
            created_at=now(),
            sequence=max(item.sequence for item in active.directives) + 1,
            public=directive.public,
            material_ids=sources,
            retracts=directive.curation,
            enabled=enabled,
        )
        simulated = IdentityPartition(
            Namespace(org_id=current.org_id, kg_id=command.kg_id),
            (*active.directives, proposal),
        )
        contexts: tuple[IdentityContext, ...] = (
            (None, *(item.material_id for item in read.visible_sources))
            if directive.public
            else (*sources, sources)
        )
        members = set(directive.members)
        while True:
            previous = set(members)
            for entity in previous:
                for context in contexts:
                    for partition in (active, simulated):
                        members.update(partition.members(entity, context))
            if len(members) > 100:
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED, "Identity compensation exceeds 100 nodes."
                )
            if members == previous:
                break
        scope = scope_key(directive.public, sources)
        counters = await self.identities._counters(
            current, command.kg_id, scope, frozenset(members)
        )
        if dict(counters)[directive.target.id] != command.expected_current_revision:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The identity target revision changed.")
        if len(read.current_view.projections) >= 1000:
            raise DomainError(
                ErrorCode.QUOTA_EXCEEDED, "Accepted identity metadata needs maintenance."
            )
        carrier = IdentityChange(
            kg_id=command.kg_id,
            entity_id=directive.target.id,
            action="merge",
            member_ids=tuple(sorted(members, key=str)),
            expected_revision=command.expected_current_revision,
            member_revisions=tuple(item for item in counters if item[0] != directive.target.id),
            curation_scope="kg_public" if directive.public else "source",
            source_ids=sources,
            reason=command.reason,
            idempotency_key=command.idempotency_key,
        )
        schema = await schema_for_kg(self.queue.policy, current, command.kg_id)
        return await self.identities._admit(
            current,
            carrier,
            fingerprint,
            scope,
            counters,
            IdentityState(),
            read,
            schema,
            IdentityControl(directive.curation, command.revision_id, before_enabled, enabled),
        )
