"""Three genuine PDF quality checks before spending a full-document model budget."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from native_model import CACHE, ROOT, model
from pydantic import SecretStr

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.extraction import Extractor
from domain_kg.ingestion.models import ParsedChunk, ParsedDocument, Span
from domain_kg.models import Namespace
from domain_kg.providers.extraction import ExtractionClient
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile


def configuration():
    return json.loads((ROOT / "config/medical-model.approved.json").read_bytes())


def profile_for(namespace):
    settings = configuration()
    embeddings = json.loads((ROOT / settings["embedding_manifest"]).read_bytes())["embeddings"]
    return ProviderProfile(
        id=uuid5(namespace.kg_id, settings["sha256"] + settings["protocol"]),
        connection_ref="real-local-chat",
        embedding_connection_ref="real-local-embedding",
        extraction_model=settings["served_model_name"],
        extraction_revision=settings["revision"],
        extraction_format=settings["protocol"],
        embedding_model=embeddings["served_model_name"],
        embedding_dimensions=1024,
        embedding_revision=embeddings["sha256"],
        allowed_namespaces=(namespace,),
        data_policy_approved=True,
        extraction_context_tokens=settings["context_tokens"],
        embedding_context_tokens=2048,
        max_completion_tokens=settings["max_completion_tokens"],
        schema_in_context=False,
        embedding_batch_size=1,
        timeout_seconds=settings["request_timeout_seconds"],
        embedding_query_prefix="Instruct: Retrieve relevant evidence passages.\nQuery: ",
    )


def connections():
    key = SecretStr((CACHE / "llm-api-key").read_text().strip())
    return (
        ProviderConnection(
            ref="real-local-chat", endpoint=configuration()["endpoint"], api_key=key
        ),
        ProviderConnection(
            ref="real-local-embedding", endpoint="http://127.0.0.1:29501/v1", api_key=key
        ),
    )


async def quality(pdf):
    source = json.loads((ROOT / "config/medical-pdf-smoke.json").read_bytes())
    original = pdf.read_bytes()
    assert len(original) == source["size_bytes"]
    assert hashlib.sha256(original).hexdigest() == source["sha256"]
    document = ParsedDocument.model_validate_json(pdf.with_name("parsed.json").read_bytes())
    assert document.blob.sha256 == source["sha256"]
    namespace = Namespace(org_id=document.blob.org_id, kg_id=document.blob.kg_id)
    profile, connection = profile_for(namespace), connections()[0]
    output = CACHE / "real-medical-kg"
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {
        "model": profile.extraction_model,
        "model_sha256": configuration()["sha256"],
        "pdf_sha256": source["sha256"],
        "protocol": profile.extraction_format,
        "real_model_calls": 0,
        "graph_publications": 0,
        "cases": [],
        "usage": [],
    }
    async with model("chat") as guard:

        async def authorize(chunk):
            assert (chunk.org_id, chunk.kg_id) == (namespace.org_id, namespace.kg_id)
            await guard.require_alive()

        async def usage(value):
            report["usage"].append(value.model_dump(mode="json"))

        client = ExtractionClient(profile, connection, authorize=authorize, record_usage=usage)
        try:
            for name, indices in (
                ("cholesterol", (138, 139)),
                ("coeliac", (129, 130, 131)),
                ("thyroid", (133,)),
            ):
                blocks = [document.blocks[i] for i in indices]
                chunk = ParsedChunk(
                    **{
                        key: getattr(document.blob, key)
                        for key in ("org_id", "kg_id", "material_id", "version_id")
                    },
                    id=uuid5(NAMESPACE_URL, document.blob.sha256 + name),
                    text="\n".join(b.text for b in blocks),
                    spans=tuple(
                        Span(location=b.location, start=0, end=len(b.text), quote=b.text)
                        for b in blocks
                    ),
                    parser_version=document.parser_version,
                    chunker_version="real-medical-quality-v2",
                )
                case = {"name": name, "blocks": indices, "source_characters": len(chunk.text)}
                started = time.monotonic()
                report["real_model_calls"] += 1
                try:
                    batch = await Extractor(client).extract(chunk, schema_for(namespace, "general"))
                    names = {e.local_id: e.name for e in batch.entities}
                    case.update(
                        status="validated",
                        entities=list(names.values()),
                        assertions=[
                            {
                                "subject": names[a.subject],
                                "predicate": a.predicate,
                                "target": names[a.target],
                                "polarity": a.polarity,
                                "evidence": [s.quote for s in a.spans],
                            }
                            for a in batch.assertions
                        ],
                    )
                except DomainError as error:
                    case.update(status="rejected", code=str(error.code))
                case["seconds"] = round(time.monotonic() - started, 3)
                report["cases"].append(case)
                print(json.dumps(case, ensure_ascii=False), flush=True)
        finally:
            await client.close()
    report.update(
        peak_rss_bytes=guard.peak_rss_bytes,
        rss_cap_bytes=guard.cap,
        owned_server_stopped=guard.process.returncode is not None,
    )
    report["passed"] = len(report["cases"]) == 3 and all(
        c["status"] == "validated" and len(c["entities"]) >= 2 and len(c["assertions"]) >= 1
        for c in report["cases"]
    )
    target = output / "quality-report.json"
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    os.chmod(target, 0o600)
    print(json.dumps({k: v for k, v in report.items() if k not in {"cases", "usage"}}), flush=True)
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pdf", type=Path, default=CACHE / "smoke-medical/child-diabetes-annual-review.pdf"
    )
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(quality(args.pdf)) else 1)
