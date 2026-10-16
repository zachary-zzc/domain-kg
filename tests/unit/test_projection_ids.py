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
