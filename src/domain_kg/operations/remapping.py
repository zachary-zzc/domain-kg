"""Typed restore identities. This module grants no access and performs no writes."""

from uuid import UUID, uuid5

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.curation_models import ManualNode
from domain_kg.graph.identity_models import IdentityGroup, ManualIdentity
from domain_kg.graph.manual_relations import ManualRelation, RelationFields
from domain_kg.graph.models import AssertionRecord, EntityRecord, SupportRecord
from domain_kg.imports.legacy import stable
from domain_kg.imports.legacy_models import LegacyGraph
from domain_kg.ingestion.models import ParsedChunk
from domain_kg.ingestion.resolution import fingerprint, normalized
from domain_kg.ingestion.resolved import ResolvedAssertion, ResolvedEntity
from domain_kg.models import Namespace, ScopedRef, SourceVersion
from domain_kg.revisions.projections import ProjectionBody


class RestoreRemapper:
    """Use a validated inventory; unlisted references never receive guessed IDs.

    Version/chunk/observation/curation IDs are namespace-local and retain their
    values. Canonical graph IDs change because their identity includes the KG.
    Evidence, timestamps, historical actors and original-byte locations stay exact.
    """

    def __init__(
        self,
        source: Namespace,
        target: Namespace,
        *,
        versions: tuple[SourceVersion, ...] = (),
        curations: tuple[ScopedRef, ...] = (),
        entities: tuple[EntityRecord, ...] = (),
        observations: tuple[ResolvedEntity, ...] = (),
        assertions: tuple[AssertionRecord, ...] = (),
        legacy_graphs: tuple[LegacyGraph, ...] = (),
    ) -> None:
        self.origin = Namespace(org_id=source.org_id, kg_id=source.kg_id)
        self.target = Namespace(org_id=target.org_id, kg_id=target.kg_id)
        if source.org_id != target.org_id or source.kg_id == target.kg_id:
            raise inaccessible_reference()
        if sum(map(len, (versions, curations, entities, observations, assertions))) > 100_000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Restore identity inventory is too large.")
        for item in (*versions, *curations, *entities, *observations, *assertions):
            self._scoped(item)
        self._versions = {item.version_id: item for item in versions}
        self._curations = {item.id: item for item in curations}
        self._entities = {item.id: item for item in entities}
        self._facts = {item.id: item for item in assertions}
        self._observations = {item.id: item for item in observations}
        if any(
            len(index) != len(items)
            for index, items in (
                (self._versions, versions),
                (self._curations, curations),
                (self._entities, entities),
                (self._facts, assertions),
                (self._observations, observations),
            )
        ):
            raise inaccessible_reference()
        identifiers: dict[UUID, UUID] = {}
        imported_entities, imported_assertions = self._legacy_ids(legacy_graphs)
        for observation in observations:
            self.source(observation)
            self._scoped(observation.entity)
            if observation.entity.id not in self._entities:
                raise inaccessible_reference()
            if observation.identifier:
                identifier = uuid5(
                    source.org_id,
                    f"{target.kg_id}/entity/identifier/{observation.entity_type}/"
                    f"{normalized(observation.identifier)}",
                )
                previous = identifiers.setdefault(observation.entity.id, identifier)
                if previous != identifier:
                    raise inaccessible_reference()
        self._entity_ids = {
            identifier: identifiers.get(
                identifier, uuid5(target.kg_id, f"restored/entity/{identifier}")
            )
            for identifier in self._entities
        }
        for old, new in imported_entities.items():
            if old in identifiers:
                raise inaccessible_reference()
            self._entity_ids[old] = new
        if len(set(self._entity_ids.values())) != len(self._entity_ids):
            raise inaccessible_reference()
        self._assertions: dict[UUID, AssertionRecord] = {}
        for fact in assertions:
            subject, endpoint = self.entity_ref(fact.subject), self.entity_ref(fact.target)
            digest = fingerprint(
                {
                    "subject": str(subject.id),
                    "target": str(endpoint.id),
                    "predicate": fact.predicate,
                    "polarity": fact.polarity,
                    "qualifiers": fact.qualifiers,
                }
            )
            self._assertions[fact.id] = AssertionRecord.model_validate(
                fact.model_dump()
                | {
                    **self.target.model_dump(),
                    "id": imported_assertions.get(
                        fact.id, uuid5(target.kg_id, f"assertion/{digest}")
                    ),
                    "subject": subject,
                    "target": endpoint,
                }
            )
        if len({item.id for item in self._assertions.values()}) != len(self._assertions):
            raise inaccessible_reference()

    def _scoped(self, item: Namespace) -> None:
        if (item.org_id, item.kg_id) != (self.origin.org_id, self.origin.kg_id):
            raise inaccessible_reference()

    def _legacy_ids(
        self, graphs: tuple[LegacyGraph, ...]
    ) -> tuple[dict[UUID, UUID], dict[UUID, UUID]]:
        entities: dict[UUID, UUID] = {}
        assertions: dict[UUID, UUID] = {}
        if len(graphs) > 1000 or sum(len(g.entities) + len(g.relations) for g in graphs) > 100_000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Restore import inventory is too large.")
        for graph in graphs:
            self._scoped(graph.namespace)
            names = {item.entity_id: item.legacy_id for item in graph.entities}
            for item in graph.entities:
                old = stable(self.origin, graph.origin, "entity", item.legacy_id)
                if old != item.entity_id or old not in self._entities:
                    raise inaccessible_reference()
                new = stable(self.target, graph.origin, "entity", item.legacy_id)
                if entities.setdefault(old, new) != new:
                    raise inaccessible_reference()
            for relation in graph.relations:
                fact = self._facts.get(relation.assertion_id)
                if (
                    fact is None
                    or names.get(relation.subject_id) is None
                    or names.get(relation.target_id) is None
                    or fact.subject.id != relation.subject_id
                    or fact.target.id != relation.target_id
                    or fact.predicate != relation.predicate
                    or fact.polarity != relation.polarity
                    or fact.qualifiers
                ):
                    raise inaccessible_reference()
                parts = (
                    graph.origin,
                    "assertion",
                    names[relation.subject_id],
                    relation.predicate,
                    names[relation.target_id],
                    relation.polarity,
                )
                if stable(self.origin, *parts) != relation.assertion_id:
                    raise inaccessible_reference()
                new = stable(self.target, *parts)
                if assertions.setdefault(relation.assertion_id, new) != new:
                    raise inaccessible_reference()
        return entities, assertions

    def source(self, item: SourceVersion) -> SourceVersion:
        self._scoped(item)
        expected = self._versions.get(item.version_id)
        if expected is None or item.material_id != expected.material_id:
            raise inaccessible_reference()
        return SourceVersion(
            **self.target.model_dump(), material_id=item.material_id, version_id=item.version_id
        )

    def entity_ref(self, item: ScopedRef) -> ScopedRef:
        self._scoped(item)
        identifier = self._entity_ids.get(item.id)
        if identifier is None:
            raise inaccessible_reference()
        return ScopedRef(**self.target.model_dump(), id=identifier)

    def assertion_ref(self, item: ScopedRef) -> ScopedRef:
        self._scoped(item)
        fact = self._assertions.get(item.id)
        if fact is None:
            raise inaccessible_reference()
        return ScopedRef(**self.target.model_dump(), id=fact.id)

    def curation_ref(self, item: ScopedRef) -> ScopedRef:
        self._scoped(item)
        if item.id not in self._curations:
            raise inaccessible_reference()
        return ScopedRef(**self.target.model_dump(), id=item.id)

    def observation(self, item: ResolvedEntity) -> ResolvedEntity:
        if self._observations.get(item.id) != item:
            raise inaccessible_reference()
        source = self.source(item)
        return ResolvedEntity.model_validate(
            item.model_dump() | source.model_dump() | {"entity": self.entity_ref(item.entity)}
        )

    def assertion(self, item: AssertionRecord) -> AssertionRecord:
        self._scoped(item)
        if self._facts.get(item.id) != item:
            raise inaccessible_reference()
        return self._assertions[item.id]

    def support(self, item: SupportRecord) -> SupportRecord:
        self._scoped(item)
        version = None
        if item.version:
            self._scoped(item.version)
            source = self._versions.get(item.version.id)
            if source is None:
                raise inaccessible_reference()
            version = ScopedRef(**self.target.model_dump(), id=source.version_id)
        return SupportRecord.model_validate(
            item.model_dump()
            | self.target.model_dump()
            | {
                "assertion": self.assertion_ref(item.assertion),
                "version": version,
                "curation": self.curation_ref(item.curation) if item.curation else None,
            }
        )

    def manual_node(self, item: ManualNode) -> ManualNode:
        self._materials(item.material_ids)
        return ManualNode.model_validate(
            item.model_dump()
            | {"curation": self.curation_ref(item.curation), "entity": self.entity_ref(item.entity)}
        )

    def _materials(self, identifiers: tuple[UUID, ...]) -> None:
        if not set(identifiers).issubset({item.material_id for item in self._versions.values()}):
            raise inaccessible_reference()

    def manual_relation(self, item: ManualRelation) -> ManualRelation:
        self._materials(item.material_ids)
        fields = item.fields
        updates = {}
        for name in ("subject_id", "target_id"):
            identifier = getattr(fields, name)
            if identifier is not None:
                updates[name] = self.entity_ref(
                    ScopedRef(**self.origin.model_dump(), id=identifier)
                ).id
        return ManualRelation.model_validate(
            item.model_dump()
            | {
                "curation": self.curation_ref(item.curation),
                "logical_assertion": self.assertion_ref(item.logical_assertion),
                "fields": RelationFields.model_validate(fields.model_dump() | updates),
                "fact": self.assertion(item.fact) if item.fact else None,
                "support": self.support(item.support) if item.support else None,
            }
        )

    def manual_identity(self, item: ManualIdentity) -> ManualIdentity:
        self._materials(item.material_ids)

        def entity(identifier: UUID) -> UUID:
            return self.entity_ref(ScopedRef(**self.origin.model_dump(), id=identifier)).id

        return ManualIdentity.model_validate(
            item.model_dump()
            | {
                "curation": self.curation_ref(item.curation),
                "target": self.entity_ref(item.target),
                "retracts": self.curation_ref(item.retracts) if item.retracts else None,
                "groups": tuple(
                    IdentityGroup(
                        canonical_id=entity(group.canonical_id),
                        member_ids=tuple(entity(member) for member in group.member_ids),
                    )
                    for group in item.groups
                ),
            }
        )

    def projection_body(self, item: ProjectionBody) -> ProjectionBody:
        """Rebuild typed content only; the caller must select valid target vectors.

        A human relationship's text contains canonical IDs, so its text can change
        during remapping even when its description is unchanged. Copying every old
        vector here would silently bind that vector to different text.
        """
        self._scoped(item)
        source = self.source(item.source) if item.source else None
        curation = self.curation_ref(item.curation) if item.curation else None
        chunk = None
        if item.chunk:
            chunk = ParsedChunk.model_validate(
                item.chunk.model_dump() | self.source(item.chunk).model_dump()
            )
        claim = None
        if item.assertion:
            claim = ResolvedAssertion.model_validate(
                item.assertion.model_dump()
                | {
                    "assertion": self.assertion(item.assertion.assertion),
                    "support": self.support(item.assertion.support),
                }
            )
        node = self.manual_node(item.manual_node) if item.manual_node else None
        relation = self.manual_relation(item.manual_relation) if item.manual_relation else None
        return ProjectionBody(
            **self.target.model_dump(),
            id=item.id,
            source=source,
            curation=curation,
            kind=item.kind,
            chunk=chunk,
            observation=self.observation(item.observation) if item.observation else None,
            assertion=claim,
            subject_observation=self.observation(item.subject_observation)
            if item.subject_observation
            else None,
            target_observation=self.observation(item.target_observation)
            if item.target_observation
            else None,
            manual_text=node.text if node else relation.text if relation else item.manual_text,
            provenance=item.provenance,
            manual_node=node,
            manual_relation=relation,
        )
