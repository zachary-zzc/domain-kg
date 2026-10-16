"""Human identity can retain opaque originals without recovering old source labels."""

from typing import TYPE_CHECKING
from uuid import UUID

from domain_kg.graph.identity_partition import IdentityContext

if TYPE_CHECKING:
    from domain_kg.graph.repository import GraphData


def identity_presence(
    data: "GraphData", identifier: UUID
) -> tuple[tuple[UUID, IdentityContext], ...]:
    partition = data.identities
    if partition is None or data.scope.filters.requires_assertion_support:
        return ()
    masks = data.node_masks
    by_id = {item.curation: item for item in partition.directives}
    origins = {member for item in partition.directives for member in item.members}
    result = []
    for context in data.identity_contexts:
        owners = (context,) if isinstance(context, UUID) else (context or ())
        excluded = data.effective_filters(context).exclude_entity_ids
        if identifier in excluded:
            continue
        for origin in sorted(origins, key=str):
            proofs = partition.witnesses(origin, context)
            if not proofs or partition.canonical(origin, context) != identifier:
                continue
            if not data.scope.filters.allows_sourceless_manual and not any(
                not by_id[proof].public for proof in proofs
            ):
                continue
            if masks.blocked(origin, material_ids=owners) or masks.blocked(
                identifier, material_ids=owners
            ):
                continue
            result.append((origin, context))
    return tuple(result)
