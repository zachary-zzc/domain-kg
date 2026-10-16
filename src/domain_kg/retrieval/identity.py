"""One mapping for source recall, graph recall and final evidence authentication."""

from uuid import UUID

from domain_kg.graph.identity_models import IdentityWitness
from domain_kg.graph.repository import GraphData
from domain_kg.revisions.projections import ProjectionBody


def evidence_identity(
    data: GraphData, row: ProjectionBody
) -> tuple[tuple[UUID, ...], tuple[IdentityWitness, ...]]:
    context = data.context(row)
    if row.manual_node:
        pairs = tuple(
            (row.manual_node.entity.id, context) for context in data.node_contexts(row.manual_node)
        )
        return tuple(
            sorted({data.entity_id(origin, context) for origin, context in pairs}, key=str)
        ), tuple(
            dict.fromkeys(
                proof
                for origin, context in pairs
                if (proof := data.identity_witness(origin, context)) is not None
            )
        )
    if row.chunk:
        originals = {
            item.entity.id
            for item in data.observations
            if item.chunk_id == row.chunk.id
            and row.source is not None
            and item.material_id == row.source.material_id
            and item.version_id == row.source.version_id
        }
    elif row.claim:
        originals = {row.claim.assertion.subject.id, row.claim.assertion.target.id}
    else:
        originals = set()
    entities = tuple(sorted({data.entity_id(item, context) for item in originals}, key=str))
    witnesses = tuple(
        proof
        for item in sorted(originals, key=str)
        if (proof := data.identity_witness(item, context)) is not None
    )
    return entities, witnesses
