"""Import transport commands contain typed plans or owned uploads, never identity or paths."""

from uuid import uuid4

import pytest
from pydantic import TypeAdapter, ValidationError

from domain_kg.imports.requests import ImportRequest


def test_import_request_rejects_path_and_identity_fields():
    adapter = TypeAdapter(ImportRequest)
    source = {
        "kg_id": str(uuid4()),
        "upload_id": str(uuid4()),
        "format": "csv",
        "idempotency_key": "preview",
    }
    command = adapter.validate_python({"operation": "plan", "source": source})
    assert command.operation == "plan" and command.source.kg_id
    for extra in ({"path": "/etc/passwd"}, {"principal": "owner"}, {"role": "owner"}):
        with pytest.raises(ValidationError):
            adapter.validate_python({"operation": "plan", "source": source} | extra)
    with pytest.raises(ValidationError):
        adapter.validate_python({"operation": "execute", "plan": {"kg_id": source["kg_id"]}})
