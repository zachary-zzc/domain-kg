"""Public restore progress exposes only a fresh same-organization target."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.operations.restore_management import RestoreInfo


def test_restore_info_requires_a_fresh_scoped_target_and_no_connection_fields():
    org, kg = uuid4(), uuid4()
    fields = dict(
        org_id=org,
        kg_id=kg,
        job_id=uuid4(),
        target=dict(org_id=org, kg_id=uuid4()),
        placement_id=uuid4(),
        provider_profile_id=uuid4(),
        state="allocated",
        stage="queued",
        provider_approved=False,
    )
    assert RestoreInfo.model_validate(fields).target.kg_id != kg
    for changes in (
        {"target": dict(org_id=uuid4(), kg_id=uuid4())},
        {"target": dict(org_id=org, kg_id=kg)},
        {"endpoint": "private"},
    ):
        with pytest.raises(ValidationError):
            RestoreInfo.model_validate(fields | changes)
