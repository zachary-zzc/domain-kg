"""Failure audit accepts an actual attempt and safe code, never arbitrary error payloads."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.jobs.queue import now
from domain_kg.operations.audit import AuditEvent


def test_owner_failure_event_has_typed_attempt_and_redacted_code():
    fields = dict(
        org_id=uuid4(),
        kg_id=uuid4(),
        id=uuid4(),
        operation_id=uuid4(),
        job_id=uuid4(),
        actor_id=uuid4(),
        kind="backup",
        outcome="failed",
        created_at=now(),
        attempt_id=uuid4(),
        error_code="BACKEND_UNAVAILABLE",
    )
    assert AuditEvent.model_validate(fields).attempt_id == fields["attempt_id"]
    with pytest.raises(ValidationError):
        AuditEvent.model_validate(fields | {"error_code": "private endpoint and token"})
