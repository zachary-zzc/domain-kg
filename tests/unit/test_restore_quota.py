"""Original restore bytes are charged once under the exact target/source scope."""

from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.materials.models import BlobRef
from domain_kg.models import Namespace, SourceVersion
from domain_kg.operations.packages import PackageObject, PackagePart


def original_part():
    ns = Namespace(org_id=uuid4(), kg_id=uuid4())
    source = SourceVersion(**ns.model_dump(), material_id=uuid4(), version_id=uuid4())
    blob = BlobRef(
        **source.model_dump(),
        object_id=uuid4(),
        sha256="a" * 64,
        size_bytes=13,
        media_type="text/plain",
    )
    obj = PackageObject(
        **ns.model_dump(),
        id=uuid4(),
        backup_id=uuid4(),
        attempt_id=uuid4(),
        sha256=blob.sha256,
        size_bytes=blob.size_bytes,
        media_type=blob.media_type,
    )
    return ns, PackagePart(object=obj, kind="original", source=source, original=blob)


def test_restore_original_budget_uses_exact_parts_and_rejects_duplicates():
    from domain_kg.operations.restore_quota import original_bytes

    ns, part = original_part()
    assert original_bytes((part,), ns) == 13
    assert original_bytes((), ns) == 0
    with pytest.raises(DomainError):
        original_bytes((part, part), ns)
    with pytest.raises(DomainError):
        original_bytes((part,), Namespace(org_id=ns.org_id, kg_id=uuid4()))


def test_restore_reservation_reads_mongo_utc_dates_without_changing_its_scope():
    from datetime import UTC, datetime

    from domain_kg.operations.restore_quota import RestoreQuota, RestoreQuotaReservation

    ns, _ = original_part()
    target = Namespace(org_id=ns.org_id, kg_id=uuid4())
    timestamp = datetime.now(UTC)
    reservation = RestoreQuotaReservation(
        **ns.model_dump(),
        id=uuid4(),
        job_id=uuid4(),
        target=target,
        source_header_sha="a" * 64,
        byte_count=13,
        created_at=timestamp,
    )
    stored = reservation.model_dump(mode="json") | {"created_at": timestamp.replace(tzinfo=None)}
    assert RestoreQuota.saved(stored) == reservation
