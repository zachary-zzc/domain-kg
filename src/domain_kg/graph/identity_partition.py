"""Deterministic per-source partitions; no pointer chains or name-based joins."""

from dataclasses import dataclass
from uuid import UUID

from domain_kg.errors import inaccessible_reference
from domain_kg.graph.identity_models import ManualIdentity
from domain_kg.models import Namespace, ScopedRef

IdentityContext = UUID | tuple[UUID, ...] | None


@dataclass(frozen=True)
class _Group:
    canonical_id: UUID
    members: frozenset[UUID]
    witnesses: tuple[ScopedRef, ...]


class IdentityPartition:
    def __init__(self, namespace: Namespace, directives: tuple[ManualIdentity, ...]) -> None:
        directives = tuple(ManualIdentity.model_validate(item) for item in directives)
        if any(
            (item.curation.org_id, item.curation.kg_id) != (namespace.org_id, namespace.kg_id)
            for item in directives
        ):
            raise inaccessible_reference()
        self.directives = tuple(
            sorted(directives, key=lambda item: (item.created_at, str(item.curation.id)))
        )
        self._contexts: dict[frozenset[UUID] | None, dict[UUID, _Group]] = {}

    def _context(self, material: IdentityContext) -> dict[UUID, _Group]:
        owners = (
            frozenset((material,))
            if isinstance(material, UUID)
            else (frozenset(material) if material else None)
        )
        if owners in self._contexts:
            return self._contexts[owners]
        current: dict[UUID, _Group] = {}
        for item in self.directives:
            if not item.public and (owners is None or not owners.issubset(item.material_ids)):
                continue
            affected = item.members
            if not affected:
                continue
            previous = {group.canonical_id: group for group in current.values()}
            for group in previous.values():
                remaining = group.members - affected
                if remaining == group.members:
                    continue
                for origin in group.members:
                    current.pop(origin, None)
                if remaining:
                    canonical = (
                        group.canonical_id
                        if group.canonical_id in remaining
                        else min(remaining, key=str)
                    )
                    witnesses = group.witnesses
                    if canonical != group.canonical_id and item.curation not in witnesses:
                        witnesses = (*witnesses, item.curation)
                    retained = _Group(canonical, remaining, witnesses)
                    current.update({origin: retained for origin in remaining})
            for proposed in item.groups:
                accepted = _Group(
                    proposed.canonical_id, frozenset(proposed.member_ids), (item.curation,)
                )
                current.update({origin: accepted for origin in proposed.member_ids})
        self._contexts[owners] = current
        return current

    def canonical(self, origin: UUID, material: IdentityContext) -> UUID:
        group = self._context(material).get(origin)
        return group.canonical_id if group else origin

    def members(self, origin: UUID, material: IdentityContext) -> tuple[UUID, ...]:
        group = self._context(material).get(origin)
        return tuple(sorted(group.members, key=str)) if group else (origin,)

    def witnesses(self, origin: UUID, material: IdentityContext) -> tuple[ScopedRef, ...]:
        group = self._context(material).get(origin)
        return group.witnesses if group else ()
