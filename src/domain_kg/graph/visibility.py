"""Pure scoped masks for source evidence and independently owned human fields."""

from dataclasses import dataclass
from functools import cached_property
from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.identity_models import ManualIdentity
from domain_kg.graph.identity_partition import IdentityPartition
from domain_kg.graph.manual_relations import ManualRelation
from domain_kg.graph.models import AssertionRecord
from domain_kg.graph.visibility_models import IdentityFenceGroup, NodeFence
from domain_kg.models import Namespace, SourceVersion
from domain_kg.revisions.projections import ProjectionBody

ChunkKey = tuple[SourceVersion, UUID]


@dataclass(frozen=True)
class NodeMasks:
    namespace: Namespace
    fences: tuple[NodeFence, ...]

    def __post_init__(self) -> None:
        for fence in self.fences:
            self._require_namespace(fence)

    def _require_namespace(self, value: Namespace) -> None:
        if (value.org_id, value.kg_id) != (self.namespace.org_id, self.namespace.kg_id):
            raise inaccessible_reference()

    @cached_property
    def public_entities(self) -> frozenset[UUID]:
        return frozenset(
            entity
            for item in self.fences
            if item.public
            for entity in (
                item.entity_id,
                *(
                    entity
                    for group in item.identity_groups
                    if group.material_id is None
                    for entity in group.member_ids
                ),
            )
        )

    @cached_property
    def source_entities(self) -> dict[UUID, frozenset[UUID]]:
        grouped: dict[UUID, set[UUID]] = {}
        for item in self.fences:
            if not item.public:
                grouped.setdefault(item.entity_id, set()).update(item.material_ids)
            for group in item.identity_groups:
                if group.material_id is not None:
                    for entity in group.member_ids:
                        grouped.setdefault(entity, set()).add(group.material_id)
        return {key: frozenset(value) for key, value in grouped.items()}

    def expanded(
        self, identities: IdentityPartition | None, materials: tuple[UUID, ...]
    ) -> "NodeMasks":
        fences = []
        for fence in self.fences:
            contexts: set[UUID | None] = set(materials if fence.public else fence.material_ids)
            contexts.update(group.material_id for group in fence.identity_groups)
            if fence.public:
                contexts.add(None)
            groups = []
            public_members = {fence.entity_id}
            for context in sorted(contexts, key=lambda value: (value is not None, str(value))):
                members = set(public_members) if fence.public else {fence.entity_id}
                members.update(
                    entity
                    for group in fence.identity_groups
                    if group.material_id == context
                    for entity in group.member_ids
                )
                if identities:
                    while True:
                        previous = set(members)
                        for entity in previous:
                            members.update(identities.members(entity, context))
                        # A single human revision may describe several partitions.
                        # Its proof must not reveal a separately fenced member.
                        for directive in identities.directives:
                            if not directive.members.intersection(members):
                                continue
                            if any(
                                directive.curation in identities.witnesses(entity, context)
                                for entity in directive.members
                            ):
                                members.update(directive.members)
                        if len(members) > 100:
                            raise DomainError(
                                ErrorCode.QUOTA_EXCEEDED, "Identity mask is too large."
                            )
                        if members == previous:
                            break
                if context is None:
                    public_members = set(members)
                    if members == {fence.entity_id}:
                        continue
                elif members.issubset(public_members if fence.public else {fence.entity_id}):
                    # The base fence already covers these IDs in this context.
                    continue
                groups.append(
                    IdentityFenceGroup(
                        material_id=context, member_ids=tuple(sorted(members, key=str))
                    )
                )
            if (
                len(groups) > 101
                or len({member for group in groups for member in group.member_ids}) > 100
            ):
                raise DomainError(
                    ErrorCode.QUOTA_EXCEEDED,
                    "Choose a smaller source scope for this identity fence.",
                )
            fences.append(
                NodeFence.model_validate(fence.model_dump() | {"identity_groups": tuple(groups)})
            )
        return NodeMasks(self.namespace, tuple(fences))

    def blocked(
        self, entity: UUID, *, material_id: UUID | None = None, material_ids: tuple[UUID, ...] = ()
    ) -> bool:
        if entity in self.public_entities:
            return True
        targets = self.source_entities.get(entity, frozenset())
        return material_id in targets or bool(targets.intersection(material_ids))

    def blocked_chunks(self, rows: tuple[ProjectionBody, ...]) -> frozenset[ChunkKey]:
        result = set()
        for row in rows:
            self._require_namespace(row)
            if row.source is None:
                continue
            if row.observation and self.blocked(
                row.observation.entity.id, material_id=row.source.material_id
            ):
                result.add((row.source, row.observation.chunk_id))
            if row.assertion and any(
                self.blocked(endpoint.id, material_id=row.source.material_id)
                for endpoint in (row.assertion.assertion.subject, row.assertion.assertion.target)
            ):
                result.update(
                    (row.source, item.chunk_id)
                    for item in (row.subject_observation, row.target_observation)
                    if item
                )
        return frozenset(result)

    def hides(
        self,
        row: ProjectionBody,
        chunks: frozenset[ChunkKey],
        *,
        relation_context: AssertionRecord | None = None,
    ) -> bool:
        self._require_namespace(row)
        if not self.fences:
            return False
        if row.source:
            identifiers = [
                item.chunk_id
                for item in (row.observation, row.subject_observation, row.target_observation)
                if item
            ]
            if row.chunk:
                identifiers.append(row.chunk.id)
            return any((row.source, identifier) in chunks for identifier in identifiers)
        manual = row.manual_node or row.manual_relation
        if manual:
            return self.hides_directive(manual, relation_context=relation_context)
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Manual mask ownership is unknown.")

    def hides_directive(
        self,
        manual: ManualNode | ManualRelation | ManualIdentity,
        *,
        relation_context: AssertionRecord | None = None,
    ) -> bool:
        self._require_namespace(manual.curation)
        if isinstance(manual, ManualIdentity):
            # Structural definitions are needed to expand node masks; they never add text rows.
            return False
        if not self.fences:
            return False
        if isinstance(manual, ManualNode):
            return self.blocked(manual.entity.id, material_ids=manual.material_ids)
        fact = manual.fact or relation_context
        if fact is None or (manual.fact is None and fact.id != manual.logical_assertion.id):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "Manual relationship mask context is missing."
            )
        for reference in (fact, fact.subject, fact.target):
            self._require_namespace(reference)
        return any(
            self.blocked(endpoint.id, material_ids=manual.material_ids)
            for endpoint in (fact.subject, fact.target)
        )
