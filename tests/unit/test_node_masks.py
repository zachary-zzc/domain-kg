"""Current node fences hide known source chunks without touching independent owners."""

from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.graph.visibility import NodeMasks
from domain_kg.graph.visibility_models import NodeFence
from domain_kg.ingestion.models import ParsedChunk
from domain_kg.ingestion.resolved import ResolvedEntity
from domain_kg.models import Namespace, ScopedRef, SourceVersion
from domain_kg.revisions.projections import ProjectionBody


def test_source_mask_covers_sibling_fields_in_the_same_chunk_but_not_another_material():
    org, kg, a, b, alpha, beta = (uuid4() for _ in range(6))
    ns = Namespace(org_id=org, kg_id=kg)
    fence = NodeFence(
        **ns.model_dump(),
        entity_id=alpha,
        curation=ScopedRef(**ns.model_dump(), id=uuid4()),
        revision=1,
        material_ids=(a,),
    )
    masks = NodeMasks(ns, (fence,))
    rows = []
    for material in (a, b):
        source = SourceVersion(**ns.model_dump(), material_id=material, version_id=uuid4())
        chunk = ParsedChunk(
            **source.model_dump(),
            id=uuid4(),
            text="Alpha uses Beta",
            spans=(),
            parser_version="fixture",
            chunker_version="fixture",
        )
        rows.append(
            ProjectionBody(**ns.model_dump(), id=chunk.id, source=source, kind="chunk", chunk=chunk)
        )
        for entity, name in ((alpha, "Alpha"), (beta, "Beta")):
            observation = ResolvedEntity(
                **source.model_dump(),
                id=uuid4(),
                chunk_id=chunk.id,
                entity=ScopedRef(**ns.model_dump(), id=entity),
                name=name,
                entity_type="concept",
                description="A field mentioning Alpha",
                spans=(),
            )
            rows.append(
                ProjectionBody(
                    **ns.model_dump(),
                    id=observation.id,
                    source=source,
                    kind="observation",
                    observation=observation,
                )
            )
    chunks = masks.blocked_chunks(tuple(rows))
    assert len(chunks) == 1
    assert all(masks.hides(row, chunks) for row in rows[:3])
    assert all(not masks.hides(row, chunks) for row in rows[3:])
    assert masks.blocked(alpha, material_ids=(a,))
    assert not masks.blocked(alpha, material_ids=(b,))
    assert not masks.blocked(alpha)  # independent, explicitly public human field


def test_public_mask_covers_all_owners_and_refuses_foreign_namespace_fences():
    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    alpha = uuid4()
    fence = NodeFence(
        **ns.model_dump(),
        entity_id=alpha,
        curation=ScopedRef(**ns.model_dump(), id=uuid4()),
        revision=1,
        public=True,
    )
    masks = NodeMasks(ns, (fence,))
    assert masks.blocked(alpha) and masks.blocked(alpha, material_ids=(uuid4(),))
    with pytest.raises(DomainError):
        NodeMasks(Namespace(org_id=ns.org_id, kg_id=uuid4()), (fence,))


def test_metadata_only_manual_fields_are_masked_without_inventing_projection_rows():
    from datetime import UTC, datetime

    from domain_kg.graph.curation_models import ManualNode, NodeFields
    from domain_kg.models import ManualProvenance

    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    alpha, material = uuid4(), uuid4()
    fence = NodeFence(
        **ns.model_dump(),
        entity_id=alpha,
        curation=ScopedRef(**ns.model_dump(), id=uuid4()),
        revision=1,
        material_ids=(material,),
    )
    node = ManualNode(
        curation=ScopedRef(**ns.model_dump(), id=uuid4()),
        entity=ScopedRef(**ns.model_dump(), id=alpha),
        changes=NodeFields(tags=("reviewed",)),
        revision=1,
        created_at=datetime.now(UTC),
        material_ids=(material,),
        provenance=ManualProvenance(actor="fixture-editor", reason="Manual metadata"),
    )
    assert node.text == ""
    masks = NodeMasks(ns, (fence,))
    assert masks.hides_directive(node)
    assert not masks.hides_directive(node.model_copy(update={"public": True, "material_ids": ()}))
