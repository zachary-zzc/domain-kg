"""Private rebuild permits cannot authorize ordinary or cross-KG artifact reads."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.jobs.models import LeasedJob
from domain_kg.models import SourceVersion


def test_rebuild_permit_requires_actual_generation_job_and_exact_source():
    from domain_kg.operations.generation_capture import GenerationReadPermit, pin_id

    org, kg = uuid4(), uuid4()
    job = LeasedJob(
        org_id=org,
        kg_id=kg,
        id=uuid4(),
        kind="generation",
        target_id=uuid4(),
        idempotency_key="generation-permit",
        attempt_id=uuid4(),
        fence=1,
        lease_token=uuid4(),
        lease_until=datetime.now(UTC),
    )
    source = SourceVersion(org_id=org, kg_id=kg, material_id=uuid4(), version_id=uuid4())
    assert GenerationReadPermit(job=job, source=source, pin_id=pin_id(job)).source == source
    for changes in ({"kind": "backup"}, {"kg_id": uuid4()}):
        other = job.model_copy(update=changes)
        with pytest.raises(ValidationError):
            GenerationReadPermit(job=other, source=source, pin_id=pin_id(other))
    with pytest.raises(ValidationError):
        GenerationReadPermit(job=job, source=source, pin_id=uuid4())
