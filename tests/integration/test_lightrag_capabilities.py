"""Accepted import trust markers are reconciled before any default retrieval metadata."""
# ruff: noqa: F811

from uuid import uuid4

import pytest
from test_publication import prepare, publishing, ready  # noqa: F401

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.models import SourceVersion
from domain_kg.revisions.journal import PublicationJournal

pytestmark = pytest.mark.storage


async def test_missing_import_marker_on_accepted_binding_fails_closed_before_visibility(publishing):
    _, editor, queue, coordinator, _, _ = publishing
    source, job, checkpoint = await prepare(publishing)
    await coordinator.publish(editor, await ready(publishing, job, checkpoint))
    origin = ImportOrigin(
        org_id=job.org_id,
        kg_id=job.kg_id,
        material_id=source.material_id,
        version_id=source.version_id,
        plan_id=uuid4(),
        plan_hash="b" * 64,
        graph_hash="c" * 64,
        origin="fixture",
    )
    # Simulate a lost binding extension while the authoritative origin ledger survives.
    await queue.database.import_source_origins.insert_one(origin.model_dump(mode="json"))
    with pytest.raises(DomainError) as stripped:
        await PublicationJournal(queue).load_metadata(editor, job.kg_id)
    assert stripped.value.code == ErrorCode.PROJECTION_INCONSISTENT
    assert origin.matches(
        SourceVersion(**{field: getattr(origin, field) for field in SourceVersion.model_fields})
    )
