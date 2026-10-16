"""Explicit unverified reads remain distinguishable from document-verified or human evidence."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.citations.models import Citation
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.retrieval.models import EvidenceCandidate


def origin():
    return ImportOrigin(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        plan_id=uuid4(),
        plan_hash="a" * 64,
        graph_hash="b" * 64,
        origin="legacy",
    )


def test_imported_candidates_and_citations_cannot_claim_verified_or_human_provenance():
    source = origin()
    candidate = EvidenceCandidate(
        kind="source",
        kg_id=source.kg_id,
        material_id=source.material_id,
        version_id=source.version_id,
        chunk_id=uuid4(),
        citation_ids=(uuid4(),),
        text="actual export context",
        import_origin=source,
    )
    assert candidate.support_status == "imported_unverified"
    with pytest.raises(ValidationError):
        EvidenceCandidate.model_validate(
            candidate.model_copy(update={"support_status": "verified"})
        )
    with pytest.raises(ValidationError):
        EvidenceCandidate.model_validate(candidate.model_copy(update={"version_id": uuid4()}))
    citation = Citation(
        id=candidate.citation_ids[0],
        kg_id=source.kg_id,
        kind="source",
        text=candidate.text,
        resolver_url="/api/v1/citations/fixture",
        material_id=source.material_id,
        version_id=source.version_id,
        chunk_id=candidate.chunk_id,
        import_origin=source,
    )
    assert citation.support_status == "imported_unverified" and not citation.spans
    with pytest.raises(ValidationError):
        Citation.model_validate(citation.model_copy(update={"support_status": "verified"}))


async def test_import_node_suppression_hides_all_raw_export_context_not_independent_materials():
    from test_identity_graph_views import graph_fixture

    from domain_kg.graph.repository import scoped_graph_data
    from domain_kg.graph.visibility_models import NodeFence
    from domain_kg.ingestion.models import ParsedChunk
    from domain_kg.revisions.projections import ProjectionBody

    data, alpha, _, _, a, b, identity = graph_fixture()
    imported = next(source for source in data.scope.visible_sources if source.material_id == a)
    marker = ImportOrigin(
        **imported.model_dump(),
        plan_id=uuid4(),
        plan_hash="a" * 64,
        graph_hash="b" * 64,
        origin="legacy",
    )
    view = data.scope.view.model_copy(
        update={
            "projections": tuple(
                binding.model_copy(update={"import_origin": marker})
                if binding.source == imported
                else binding
                for binding in data.scope.view.projections
            )
        }
    )
    fence = NodeFence(
        org_id=imported.org_id,
        kg_id=imported.kg_id,
        entity_id=alpha,
        curation=identity.curation,
        revision=1,
        material_ids=(a,),
    )
    scope = data.scope.model_copy(update={"view": view, "node_fences": (fence,)})
    rows = list(data.rows)
    for source in data.scope.visible_sources:
        for identifier in (source.version_id, uuid4()):
            chunk = ParsedChunk(
                **source.model_dump(),
                id=identifier,
                text="Alpha appears again in this export context.",
                spans=(),
                parser_version="fixture",
                chunker_version="fixture",
            )
            rows.append(
                ProjectionBody(
                    org_id=source.org_id,
                    kg_id=source.kg_id,
                    id=identifier,
                    source=source,
                    kind="chunk",
                    chunk=chunk,
                )
            )
    visible = await scoped_graph_data(None, scope, tuple(rows), ())
    assert not any(row.chunk for row in visible.rows if row.source.material_id == a)
    assert (
        len([row.chunk for row in visible.rows if row.source.material_id == b and row.chunk]) == 2
    )
