"""Import manifests are bounded metadata; commands cannot select arbitrary source behavior."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.materials.import_runs import CreateImportRun, ImportFile, ImportRunSubmission


def original(path="folder/a.pdf"):
    return {"path": path, "sha256": "0" * 64, "size_bytes": 20, "media_type": "application/pdf"}


@pytest.mark.parametrize(
    "path", ["/a.pdf", "a/../b.pdf", "./a.pdf", "a\\b.pdf", "a//b.pdf", "a/\x00.pdf"]
)
def test_manifest_rejects_unsafe_relative_paths(path):
    with pytest.raises(ValidationError):
        ImportFile.model_validate(original(path))


def test_manifest_binds_distinct_paths_sizes_hashes_and_bounded_file_count():
    assert ImportFile.model_validate(original()).path == "folder/a.pdf"
    for values in (
        {"files": [original(), original()], "idempotency_key": "key"},
        {"files": [], "idempotency_key": "key"},
        {"files": [original()] * 101, "idempotency_key": "key"},
        {"files": [original() | {"sha256": "invalid"}], "idempotency_key": "key"},
        {"files": [original() | {"size_bytes": True}], "idempotency_key": "key"},
        {"files": [original() | {"size_bytes": 51 * 1024 * 1024}], "idempotency_key": "key"},
    ):
        with pytest.raises(ValidationError):
            CreateImportRun.model_validate(values)


def test_item_command_has_one_input_and_explicit_replacement_binding():
    upload, job, material, version = (uuid4() for _ in range(4))
    for values in (
        {},
        {"upload_id": upload, "job_id": job},
        {"job_id": job, "material_id": material, "expected_active_version": version},
        {"upload_id": upload, "material_id": material},
        {"upload_id": upload, "expected_revision": 0},
        {"upload_id": upload, "inline_text": "untrusted"},
    ):
        with pytest.raises(ValidationError):
            ImportRunSubmission.model_validate(values)
    assert ImportRunSubmission(job_id=job).job_id == job
    assert (
        ImportRunSubmission(
            upload_id=upload, material_id=material, expected_active_version=version
        ).material_id
        == material
    )
