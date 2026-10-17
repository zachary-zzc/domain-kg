"""Restored source artifacts get new seals and dependency hashes, never copied leases."""

import hashlib
from uuid import uuid4

import pytest
from test_restore_documents import documents

from domain_kg.errors import DomainError
from domain_kg.jobs.models import Checkpoint, JobArtifact
from domain_kg.models import ScopedRef
from domain_kg.operations.restore_documents import RestoreDocuments


def artifact(original, job, value, stage, artifact_id=None):
    body = value.model_dump_json().encode()
    checksum = hashlib.sha256(body).hexdigest()
    blob = original.model_copy(
        update={
            "object_id": uuid4(),
            "sha256": checksum,
            "size_bytes": len(body),
            "media_type": "application/json",
        }
    )
    ns = {"org_id": original.org_id, "kg_id": original.kg_id}
    row = JobArtifact(
        **ns,
        id=artifact_id or uuid4(),
        job_id=job,
        source_version_id=original.version_id,
        stage=stage,
        input_hash="a" * 64,
        blob=blob,
    )
    checkpoint = Checkpoint(
        **ns,
        job_id=job,
        stage=stage,
        artifact_id=row.id,
        input_hash=row.input_hash,
        output_hash=blob.sha256,
    )
    return row, checkpoint, body


def test_artifact_chain_reseals_every_stage_with_new_namespace_and_dependencies():
    from domain_kg.operations.resealing import RestoreArtifactLedger

    mapping, parsed, extraction, resolved = documents()
    docs = RestoreDocuments(mapping, originals=(parsed.blob,))
    operation = ScopedRef(**mapping.origin.model_dump(), id=uuid4())
    ledger = RestoreArtifactLedger(docs, operation)
    job = uuid4()
    stages = [
        ("validating", parsed.blob, None),
        ("parsing", parsed, None),
        ("chunking", parsed, extraction.chunk_artifact_id),
        ("extracting", extraction, None),
        ("resolving", resolved, None),
    ]
    previous = None
    for stage, value, identifier in stages:
        row, checkpoint, body = artifact(parsed.blob, job, value, stage, identifier)
        result = ledger.rewrite(row, checkpoint, body)
        assert result.artifact.kg_id == mapping.target.kg_id
        assert result.artifact.id == row.id and result.artifact.job_id == job
        assert result.artifact.blob.sha256 == hashlib.sha256(result.body).hexdigest()
        assert result.checkpoint.output_hash == result.artifact.blob.sha256
        assert result.checkpoint.input_hash != checkpoint.input_hash
        assert result.checkpoint.attempt_id is None
        assert result.origin.restore_operation == operation
        assert result.origin.original == row and result.origin.restored == result.artifact
        assert ledger.rewrite(row, checkpoint, body) == result
        if previous:
            assert previous.checkpoint.output_hash in result.origin.dependency_hashes
        previous = result
    assert parsed.blocks[0].text.encode() in result.body
    assert b'"lease_token"' not in result.body


def test_resealing_rejects_corrupt_bytes_and_missing_or_cross_owned_dependencies():
    from domain_kg.operations.resealing import RestoreArtifactLedger

    mapping, parsed, extraction, _ = documents()
    operation = ScopedRef(**mapping.origin.model_dump(), id=uuid4())
    ledger = RestoreArtifactLedger(RestoreDocuments(mapping, originals=(parsed.blob,)), operation)
    row, checkpoint, body = artifact(parsed.blob, uuid4(), extraction, "extracting")
    with pytest.raises(DomainError):
        ledger.rewrite(row, checkpoint, body)  # Its source chunk artifact has not been rebuilt.
    row, checkpoint, body = artifact(parsed.blob, uuid4(), parsed.blob, "validating")
    with pytest.raises(DomainError):
        ledger.rewrite(row, checkpoint, body + b" ")
    with pytest.raises(DomainError):
        ledger.rewrite(row, checkpoint.model_copy(update={"job_id": uuid4()}), body)
    with pytest.raises(DomainError):
        ledger.rewrite(row.model_copy(update={"source_version_id": uuid4()}), checkpoint, body)


def test_validation_metadata_cannot_substitute_another_known_material_version():
    from domain_kg.models import SourceVersion
    from domain_kg.operations.remapping import RestoreRemapper
    from domain_kg.operations.resealing import RestoreArtifactLedger

    mapping, parsed, _, _ = documents()
    other = parsed.blob.model_copy(
        update={"material_id": uuid4(), "version_id": uuid4(), "object_id": uuid4()}
    )
    versions = tuple(
        SourceVersion(**{k: getattr(blob, k) for k in SourceVersion.model_fields})
        for blob in (parsed.blob, other)
    )
    mapping = RestoreRemapper(mapping.origin, mapping.target, versions=versions)
    ledger = RestoreArtifactLedger(
        RestoreDocuments(mapping, originals=(parsed.blob, other)),
        ScopedRef(**mapping.origin.model_dump(), id=uuid4()),
    )
    row, checkpoint, body = artifact(parsed.blob, uuid4(), other, "validating")
    with pytest.raises(DomainError):
        ledger.rewrite(row, checkpoint, body)
