"""A job page cannot serialize a foreign organization or KG receipt."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.jobs.inspection import JobPage
from domain_kg.jobs.models import JobReceipt


def test_job_page_requires_one_exact_namespace():
    org, kg = uuid4(), uuid4()
    receipt = JobReceipt(org_id=org, kg_id=kg, job_id=uuid4())
    assert JobPage(org_id=org, kg_id=kg, items=(receipt,)).items == (receipt,)
    for changes in ({"org_id": uuid4()}, {"kg_id": uuid4()}):
        with pytest.raises(ValidationError):
            JobPage(org_id=org, kg_id=kg, items=(receipt.model_copy(update=changes),))
