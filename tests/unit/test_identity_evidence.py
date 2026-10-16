"""Search evidence discloses the human mapping without rewriting source facts."""

from test_identity_graph_views import graph_fixture

from domain_kg.ingestion.models import ParsedChunk
from domain_kg.retrieval.evidence import EvidenceData
from domain_kg.revisions.projections import ProjectionBody


def test_source_candidate_maps_only_its_source_context_with_identity_proof():
    data, alpha, beta, gamma, a, b, identity = graph_fixture()
    evidence = EvidenceData(data, (), ())
    for material in (a, b):
        source = next(item for item in data.scope.visible_sources if item.material_id == material)
        observation = next(item for item in data.observations if item.material_id == material)
        row = ProjectionBody(
            org_id=source.org_id,
            kg_id=source.kg_id,
            source=source,
            id=observation.chunk_id,
            kind="chunk",
            chunk=ParsedChunk(
                **source.model_dump(),
                id=observation.chunk_id,
                text="synthetic source",
                spans=observation.spans,
                parser_version="fixture",
                chunker_version="fixture",
            ),
        )
        candidate = evidence.candidate(row, "lexical", 1.0, 1)
        if material == a:
            assert set(candidate.entity_ids) == {alpha, gamma}
            assert any(
                proof.original_id == beta
                and proof.canonical_id == alpha
                and identity.curation in proof.curations
                for proof in candidate.identity_mappings
            )
        else:
            assert set(candidate.entity_ids) == {alpha, beta, gamma}
            assert candidate.identity_mappings == ()
