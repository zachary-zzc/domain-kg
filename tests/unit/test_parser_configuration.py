"""Server-selected parsing limits survive reload and reject stale/invalid edits."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from domain_kg.errors import DomainError
from domain_kg.ingestion.configuration import ParserConfigFile, ParserConfiguration
from domain_kg.ingestion.models import ParseOptions


def test_parser_configuration_defaults_save_reload_and_stale_edit(tmp_path):
    path = tmp_path / "parser.json"
    store = ParserConfigFile(path)
    initial = store.read()
    assert initial.revision == 0 and initial.options.memory_limit_mib == 1024
    assert ParserConfigFile(None).read() == initial
    saved = store.save(ParseOptions(memory_limit_mib=2048, timeout_seconds=180, ocr=False), 0)
    assert saved.revision == 1 and store.read() == saved
    assert path.stat().st_mode & 0o777 == 0o600
    # A lost response can replay the same settings, but cannot overwrite a newer edit.
    assert store.save(saved.options, 0) == saved
    before = path.read_bytes()
    with pytest.raises(DomainError) as stale:
        store.save(ParseOptions(memory_limit_mib=4096), 0)
    assert stale.value.code == "VERSION_CONFLICT" and path.read_bytes() == before


def test_saving_defaults_creates_a_persistent_configuration(tmp_path):
    path = tmp_path / "parser.json"
    saved = ParserConfigFile(path).save(ParseOptions(), 0)
    assert path.is_file() and saved.revision == 1
    assert ParserConfigFile(path).read() == saved


@pytest.mark.parametrize("value", [0, 63, 8193, True, 1024.5, "1024"])
def test_memory_budget_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        ParseOptions(memory_limit_mib=value)


def test_invalid_configuration_is_explicit_and_never_overwritten(tmp_path):
    path = tmp_path / "parser.json"
    store = ParserConfigFile(path)
    for data in [b"{", b"x" * 65537, json.dumps({"options": {"memory_limit_mib": 9000}}).encode()]:
        path.write_bytes(data)
        with pytest.raises(DomainError) as failed:
            store.read()
        assert failed.value.code == "VALIDATION_ERROR"
        with pytest.raises(DomainError):
            store.save(ParseOptions(), 0)
        assert path.read_bytes() == data


def test_concurrent_saves_accept_only_one_reviewed_revision(tmp_path):
    store = ParserConfigFile(tmp_path / "parser.json")

    def save(memory):
        try:
            return store.save(ParseOptions(memory_limit_mib=memory), 0)
        except DomainError as error:
            return error

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(save, [2048, 3072]))
    assert sum(isinstance(result, ParserConfiguration) for result in results) == 1
    assert (
        sum(
            isinstance(result, DomainError) and result.code == "VERSION_CONFLICT"
            for result in results
        )
        == 1
    )
    assert store.read().revision == 1


async def test_configured_memory_budget_controls_real_parser(monkeypatch):
    import hashlib
    from uuid import uuid4

    from domain_kg.ingestion.parsers import Parser
    from domain_kg.materials.models import BlobRef

    data = b"Configurable evidence."
    blob = BlobRef(
        org_id=uuid4(),
        kg_id=uuid4(),
        material_id=uuid4(),
        version_id=uuid4(),
        object_id=uuid4(),
        sha256=hashlib.sha256(data).hexdigest(),
        size_bytes=len(data),
        media_type="text/plain",
    )

    class Original:
        async def open(self, _reference):
            yield data

    async def memory(_process):
        return 600 * 1024 * 1024

    parser = Parser(Original())
    monkeypatch.setattr(parser, "_rss", memory)
    with pytest.raises(DomainError, match="Parser memory limit exceeded"):
        await parser.parse(blob, ParseOptions(memory_limit_mib=512))
    parsed = await parser.parse(blob, ParseOptions(memory_limit_mib=2048))
    assert parsed.blocks[0].text == data.decode()


async def test_job_configuration_is_fixed_until_the_next_execution(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from domain_kg.ingestion.pipeline import Pipeline, PipelineStep, preparation_steps
    from domain_kg.jobs.models import Checkpoint
    from domain_kg.jobs.runner import JobRunner

    store = ParserConfigFile(tmp_path / "parser.json")
    observed = []
    queue = SimpleNamespace(
        authorize=AsyncMock(),
        load_checkpoint=AsyncMock(return_value=None),
        start_step=AsyncMock(),
        accepted=AsyncMock(return_value=False),
        checkpoint=AsyncMock(),
    )
    org, kg, job_id = uuid4(), uuid4(), uuid4()

    def pipeline():
        options = store.read().options
        steps = []
        for stage, digest in [("parsing", "a" * 64), ("chunking", "b" * 64)]:

            async def execute(_job, stage=stage, digest=digest):
                observed.append((stage, options.memory_limit_mib))
                if store.read().revision == 0:
                    store.save(ParseOptions(memory_limit_mib=2048), 0)
                return Checkpoint(
                    org_id=org,
                    kg_id=kg,
                    job_id=job_id,
                    stage=stage,
                    artifact_id=uuid4(),
                    input_hash=digest,
                    output_hash="c" * 64,
                )

            steps.append(PipelineStep(stage=stage, input_hash=digest, execute=execute))
        return Pipeline(tuple(steps))

    runner = JobRunner(queue, Pipeline(), pipeline_factory=pipeline)
    await runner._steps(SimpleNamespace(kind="ingest"))
    await runner._steps(SimpleNamespace(kind="ingest"))
    assert observed == [
        ("parsing", 1024),
        ("chunking", 1024),
        ("parsing", 2048),
        ("chunking", 2048),
    ]
    default = preparation_steps(queue, options=ParseOptions())
    changed = preparation_steps(queue, options=ParseOptions(memory_limit_mib=2048))
    assert default[1].input_hash != changed[1].input_hash
