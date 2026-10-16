"""Manual records retain provenance and separate namespace/attempt identities."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.models import ManualProvenance, ScopedRef
from domain_kg.revisions.projections import ProjectionRow


def test_source_less_curation_ids_are_scoped_and_require_manual_provenance():
    org, kg, curation = uuid4(), uuid4(), uuid4()
    row = ProjectionRow(
        org_id=org,
        kg_id=kg,
        id=curation,
        curation=ScopedRef(org_id=org, kg_id=kg, id=curation),
        kind="curation",
        manual_text="An explicitly curated statement.",
        vector=(1.0, 0.0, 0.0),
        provenance=ManualProvenance(actor="fixture-owner", reason="Reviewed manually"),
    )
    attempt, manifest = uuid4(), uuid4()
    identity = row.projection_id(attempt, manifest)
    assert identity == row.projection_id(attempt, manifest)
    assert identity != row.projection_id(uuid4(), manifest)
    assert identity != row.projection_id(attempt, uuid4())
    assert row.source is None and row.chunk is None and row.assertion is None
    with pytest.raises(ValidationError):
        ProjectionRow.model_validate(row.model_copy(update={"provenance": None}))
    with pytest.raises(ValidationError):
        ProjectionRow.model_validate(row.model_copy(update={"kg_id": uuid4()}))


async def test_vector_verification_rejects_foreign_namespace_before_io():
    from unittest.mock import AsyncMock

    from domain_kg.errors import DomainError
    from domain_kg.models import Namespace
    from domain_kg.storage.qdrant import VectorProjection
    from domain_kg.storage.router import VectorBinding

    namespace = Namespace(org_id=uuid4(), kg_id=uuid4())
    client = AsyncMock()
    client.retrieve.side_effect = AssertionError("Foreign records must not be read.")
    binding = VectorBinding(namespace, client, "unused")
    with pytest.raises(DomainError):
        await VectorProjection(binding).verify(
            [
                {
                    "org_id": str(namespace.org_id),
                    "kg_id": str(uuid4()),
                    "projection_id": str(uuid4()),
                }
            ],
            [[1.0, 0.0, 0.0]],
        )
    client.retrieve.assert_not_called()


def test_source_payload_keeps_its_pre_curation_extension_body():
    import json

    from domain_kg.ingestion.models import ParsedChunk
    from domain_kg.models import SourceVersion
    from domain_kg.providers.embeddings import EmbeddingProfile
    from domain_kg.revisions.projections import digest

    org, kg, material, version, chunk = (uuid4() for _ in range(5))
    source = SourceVersion(org_id=org, kg_id=kg, material_id=material, version_id=version)
    row = ProjectionRow(
        org_id=org,
        kg_id=kg,
        id=chunk,
        kind="chunk",
        source=source,
        chunk=ParsedChunk(
            **source.model_dump(),
            id=chunk,
            text="Source evidence",
            spans=(),
            parser_version="fixture",
            chunker_version="fixture",
        ),
        vector=(1.0, 0.0, 0.0),
    )
    # The older accepted body lacks the new optional manual field entirely.
    original = row.model_dump(mode="json", exclude={"vector", "manual_node"})
    restored = ProjectionRow.model_validate(original | {"vector": [1.0, 0.0, 0.0]})
    payload = restored.payload(
        attempt=uuid4(),
        manifest=uuid4(),
        generation=uuid4(),
        content_hash="a" * 64,
        profile=EmbeddingProfile(
            org_id=org,
            kg_id=kg,
            id=uuid4(),
            revision=1,
            model="fixture",
            dimensions=3,
            space_id="b" * 64,
        ),
    )
    assert json.loads(payload["body"]) == original
    assert payload["body_hash"] == digest(original)


def test_manual_node_seal_roundtrips_partial_changes():
    from datetime import UTC, datetime

    from domain_kg.graph.curation_models import ManualNode, NodeChanges

    org, kg, revision, entity, material = (uuid4() for _ in range(5))
    manual = ManualNode(
        curation=ScopedRef(org_id=org, kg_id=kg, id=revision),
        entity=ScopedRef(org_id=org, kg_id=kg, id=entity),
        changes=NodeChanges(name="Human name"),
        revision=1,
        created_at=datetime.now(UTC),
        material_ids=(material,),
        provenance=ManualProvenance(actor="fixture-editor", reason="Reviewed"),
    )
    assert ManualNode.model_validate_json(manual.model_dump_json()) == manual


def test_compensating_override_state_can_be_empty_but_public_edits_cannot():
    from datetime import UTC, datetime

    from domain_kg.graph.curation_models import ManualNode, NodeChanges, NodeFields

    org, kg, revision, entity = (uuid4() for _ in range(4))
    with pytest.raises(ValidationError):
        NodeChanges()
    cleared = ManualNode(
        curation=ScopedRef(org_id=org, kg_id=kg, id=revision),
        entity=ScopedRef(org_id=org, kg_id=kg, id=entity),
        changes=NodeFields(),
        revision=2,
        public=True,
        created_at=datetime.now(UTC),
        provenance=ManualProvenance(actor="fixture-owner", reason="Withdraw the public override"),
    )
    assert cleared.text == ""
    assert ManualNode.model_validate_json(cleared.model_dump_json()) == cleared
