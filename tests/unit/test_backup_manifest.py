"""Retained package references cannot cross operation, attempt or KG boundaries."""

from uuid import uuid4

import pytest
from pydantic import ValidationError


def test_package_pages_bind_each_object_to_one_namespace_and_attempt():
    from domain_kg.operations.packages import PackageObject, PackagePage, PackagePart

    org, kg, backup, attempt = uuid4(), uuid4(), uuid4(), uuid4()
    reference = PackageObject(
        org_id=org,
        kg_id=kg,
        backup_id=backup,
        attempt_id=attempt,
        id=uuid4(),
        sha256="a" * 64,
        size_bytes=1,
        media_type="application/json",
    )
    part = PackagePart(object=reference, kind="authority")
    page = PackagePage(
        org_id=org, kg_id=kg, backup_id=backup, attempt_id=attempt, index=0, parts=(part,)
    )
    assert page.parts[0].object.id == reference.id
    for field in ("kg_id", "backup_id", "attempt_id"):
        with pytest.raises(ValidationError):
            PackagePage.model_validate(page.model_dump() | {field: uuid4()})


def test_original_package_part_requires_exact_hash_and_source_ownership():
    from domain_kg.operations.packages import PackageObject, PackagePart

    from domain_kg.materials.models import BlobRef
    from domain_kg.models import SourceVersion

    org, kg, material, version = uuid4(), uuid4(), uuid4(), uuid4()
    source = SourceVersion(org_id=org, kg_id=kg, material_id=material, version_id=version)
    original = BlobRef(
        **source.model_dump(),
        object_id=uuid4(),
        sha256="a" * 64,
        size_bytes=2,
        media_type="text/plain",
    )
    copied = PackageObject(
        org_id=org,
        kg_id=kg,
        backup_id=uuid4(),
        attempt_id=uuid4(),
        id=uuid4(),
        sha256="a" * 64,
        size_bytes=2,
        media_type="text/plain",
    )
    valid = PackagePart(object=copied, kind="original", source=source, original=original)
    for update in ({"sha256": "b" * 64}, {"size_bytes": 3}, {"kg_id": uuid4()}):
        with pytest.raises(ValidationError):
            PackagePart.model_validate(
                valid.model_dump() | {"original": original.model_copy(update=update).model_dump()}
            )
