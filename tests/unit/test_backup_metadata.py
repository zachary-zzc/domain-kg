"""Retained metadata must describe one exact capture, not merely one namespace."""

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.models import Namespace
from domain_kg.operations.backup_models import AuthoritySection, BackupManifest
from domain_kg.operations.models import SnapshotPointer
from domain_kg.operations.package_reader import PackageReader
from domain_kg.operations.packages import PackageObject, PackagePage, PackagePart
from domain_kg.revisions.models import PublishedView


@pytest.mark.parametrize("field", ["id", "revision", "visibility_revision", "configuration_epoch"])
async def test_package_rejects_metadata_outside_its_exact_snapshot(field):
    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    backup, attempt = uuid4(), uuid4()
    pointer = SnapshotPointer(
        **ns.model_dump(),
        revision=0,
        generation=uuid4(),
        manifest_id=None,
        visibility_revision=3,
        metadata_revision=2,
        configuration_epoch=1,
    )
    records = {
        **ns.model_dump(mode="json"),
        "id": str(ns.kg_id),
        "revision": 2,
        "visibility_revision": 3,
        "configuration_epoch": 1,
        "published_revision": 0,
        "generation": str(pointer.generation),
        "published_manifest_id": None,
    }
    records[field] = str(uuid4()) if field == "id" else 999
    bodies = {}

    def store(body):
        reference = PackageObject(
            **ns.model_dump(),
            id=uuid4(),
            backup_id=backup,
            attempt_id=attempt,
            sha256=hashlib.sha256(body).hexdigest(),
            size_bytes=len(body),
        )
        bodies[reference.id] = body
        return reference

    metadata = AuthoritySection(
        **ns.model_dump(), backup_id=backup, area="control", table="kg_metadata", records=(records,)
    )
    view = PublishedView(**ns.model_dump(), revision=0, generation=pointer.generation)
    root = AuthoritySection(
        **ns.model_dump(),
        backup_id=backup,
        area="control",
        table="published_view",
        records=(view.model_dump(mode="json"),),
    )
    page = PackagePage(
        **ns.model_dump(),
        backup_id=backup,
        attempt_id=attempt,
        index=0,
        parts=tuple(
            PackagePart(kind="authority", object=store(section.model_dump_json().encode()))
            for section in (metadata, root)
        ),
    )
    manifest = BackupManifest(
        **ns.model_dump(),
        backup_id=backup,
        job_id=uuid4(),
        attempt_id=attempt,
        snapshot=pointer,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1),
        source_count=0,
        curation_count=0,
        part_count=2,
        pages=(store(page.model_dump_json().encode()),),
    )

    class MemoryStore:
        async def open(self, reference):
            yield bodies[reference.id]

    async def guard():
        return None

    with pytest.raises(DomainError):
        await PackageReader(MemoryStore(), guard).verify(manifest)
