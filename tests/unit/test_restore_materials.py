"""Restored material metadata starts private and keeps source/version references exact."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from test_restore_documents import documents

from domain_kg.errors import DomainError
from domain_kg.operations.restore_documents import RestoreDocuments


def test_material_access_drops_old_members_groups_and_public_visibility():
    from domain_kg.operations.restore_materials import RestoreMaterials

    mapping, parsed, _, _ = documents()
    source = RestoreDocuments.owner(parsed.blob)
    row = source.model_dump(mode="json") | dict(
        id=str(source.material_id),
        acl_mode="kg",
        member_ids=[str(uuid4())],
        group_ids=[str(uuid4())],
        revision=7,
        metadata_epoch=2,
        active_version_id=str(source.version_id),
        excluded=True,
        tombstoned=False,
    )
    row["metadata"] = dict(
        material_id=str(source.material_id),
        version_id=str(source.version_id),
        media_type="text/plain",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    result = RestoreMaterials(RestoreDocuments(mapping, originals=(parsed.blob,))).access(
        row, source
    )
    assert result.kg_id == mapping.target.kg_id and result.acl_mode == "restricted"
    assert result.member_ids == () and result.group_ids == ()
    assert result.excluded and not result.tombstoned and result.revision == 7
    with pytest.raises(DomainError):
        RestoreMaterials(RestoreDocuments(mapping, originals=(parsed.blob,))).access(
            row | {"kg_id": str(uuid4())}, source
        )


def test_version_metadata_preserves_literals_and_hash_but_not_upload_authority():
    from domain_kg.operations.restore_materials import RestoreMaterials

    mapping, parsed, _, _ = documents()
    blob = parsed.blob
    literal = f"Literal {mapping.origin.kg_id}"
    row = dict(
        org_id=str(blob.org_id),
        kg_id=str(blob.kg_id),
        id=str(blob.version_id),
        material_id=str(blob.material_id),
        blob=blob.model_dump(mode="json"),
        state="accepted",
        created_at=datetime.now(UTC),
        upload_id=str(uuid4()),
        proposed=dict(
            kg_id=str(blob.kg_id),
            title=literal,
            idempotency_key="historical",
            tags=[literal],
            author=literal,
            source=literal,
            language="zh",
            acl_mode="kg",
        ),
    )
    result = RestoreMaterials(RestoreDocuments(mapping, originals=(blob,))).version(row)
    assert result.kg_id == mapping.target.kg_id and result.blob.sha256 == blob.sha256
    assert result.proposed.title == literal and result.proposed.source == literal
    assert (
        result.proposed.kg_id == mapping.target.kg_id and result.proposed.acl_mode == "restricted"
    )
    assert "upload_id" not in result.model_dump()
    with pytest.raises(DomainError):
        RestoreMaterials(RestoreDocuments(mapping, originals=(blob,))).version(
            row | {"material_id": str(uuid4())}
        )
