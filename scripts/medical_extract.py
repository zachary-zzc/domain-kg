"""Three genuine PDF quality checks before spending a full-document model budget."""

import argparse
import asyncio
import hashlib
import json
import os
import time
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import SecretStr

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.extraction import Extractor
from domain_kg.ingestion.models import ParsedChunk, ParsedDocument, Span
from domain_kg.models import Namespace
from domain_kg.providers.extraction import ExtractionClient
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile, ProviderRegistry
from scripts.native_model import CACHE, ROOT, model


def configuration():
    return json.loads((ROOT / "config/medical-model.approved.json").read_bytes())


def profile_for(namespace):
    settings = configuration()
    if registry := api_registry():
        if len(registry.profiles) != 1:
            raise RuntimeError("The public-PDF API file must select one profile.")
        selected = next(iter(registry.profiles.values()))
        if not selected.data_policy_approved:
            raise RuntimeError("Approve the public PDF data policy in the private API file first.")
        if selected.allowed_namespaces and namespace not in selected.allowed_namespaces:
            raise RuntimeError("This public-PDF namespace is outside the approved API scope.")
        connection = registry.connections[selected.connection_ref]
        if ".invalid" in connection.endpoint or selected.extraction_model.startswith("REPLACE"):
            raise RuntimeError("Fill the API endpoint/model in the private configuration first.")
        # Only this CLI's checksum-verified public PDF uses this template binding.
        # Empty namespaces never mean a wildcard in the service registry itself.
        return selected.model_copy(
            update={
                "id": uuid5(namespace.kg_id, str(selected.id)),
                "allowed_namespaces": (namespace,),
            }
        )
    embeddings = json.loads((ROOT / settings["embedding_manifest"]).read_bytes())["embeddings"]
    return ProviderProfile(
        id=uuid5(namespace.kg_id, settings["sha256"] + settings["protocol"]),
        connection_ref="real-local-chat",
        embedding_connection_ref="real-local-embedding",
        extraction_model=settings["served_model_name"],
        extraction_revision=settings["revision"] + "/" + settings["prompt_revision"],
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
    if registry := api_registry():
        return tuple(registry.connections.values())
    key = SecretStr((CACHE / "llm-api-key").read_text().strip())
    return (
        ProviderConnection(
            ref="real-local-chat", endpoint=configuration()["endpoint"], api_key=key
        ),
        ProviderConnection(
            ref="real-local-embedding", endpoint="http://127.0.0.1:29501/v1", api_key=key
        ),
    )


def api_registry():
    value = os.environ.get("DOMAIN_KG_MEDICAL_API_CONFIG")
    return ProviderRegistry.from_file(Path(value)) if value else None


def provider_fingerprint(profile):
    connection = next(c for c in connections() if c.ref == profile.connection_ref)
    return hashlib.sha256(
        json.dumps(
            {
                "endpoint": connection.endpoint,
                "model": profile.extraction_model,
                "revision": profile.extraction_revision,
                "format": profile.extraction_format,
                "prompt_revision": configuration()["prompt_revision"],
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


async def quality(pdf, *, selected_case=None):
    source = json.loads((ROOT / "config/medical-pdf-smoke.json").read_bytes())
    original = pdf.read_bytes()
    assert len(original) == source["size_bytes"]
    assert hashlib.sha256(original).hexdigest() == source["sha256"]
    document = ParsedDocument.model_validate_json(pdf.with_name("parsed.json").read_bytes())
    assert document.blob.sha256 == source["sha256"]
    namespace = Namespace(org_id=document.blob.org_id, kg_id=document.blob.kg_id)
    profile = profile_for(namespace)
    connection = next(c for c in connections() if c.ref == profile.connection_ref)
    output = CACHE / "real-medical-kg"
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    report = {
        "model": profile.extraction_model,
        "model_sha256": None if api_registry() else configuration()["sha256"],
        "provider_fingerprint": provider_fingerprint(profile),
        "provider_kind": "api" if api_registry() else "native",
        "pdf_sha256": source["sha256"],
        "protocol": profile.extraction_format,
        "domain_schema": configuration()["domain_schema"],
        "prompt_revision": configuration()["prompt_revision"],
        "real_model_calls": 0,
        "graph_publications": 0,
        "cases": [],
        "usage": [],
    }
    async with AsyncExitStack() as runtime:
        guard = await runtime.enter_async_context(model("chat")) if not api_registry() else None

        async def authorize(chunk):
            assert (chunk.org_id, chunk.kg_id) == (namespace.org_id, namespace.kg_id)
            if guard is not None:
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
                if selected_case is not None and name != selected_case:
                    continue
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

                async def capture(response, label=name):
                    raw = await response.aread()
                    if len(raw) > 2 * 1024**2:
                        raise RuntimeError("Bounded local response exceeded its cap.")
                    (output / f"quality-{label}-raw.json").write_bytes(raw)

                client.client.event_hooks["response"] = [capture]

                class Recording:
                    id = client.id
                    label = name

                    async def complete(self, current, schema):
                        value = await client.complete(current, schema)
                        (output / f"quality-{self.label}-canonical.json").write_text(
                            json.dumps(value)
                        )
                        return value

                report["real_model_calls"] += 1
                try:
                    batch = await Extractor(Recording()).extract(
                        chunk, schema_for(namespace, configuration()["domain_schema"])
                    )
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
        peak_rss_bytes=guard.peak_rss_bytes if guard else 0,
        rss_cap_bytes=guard.cap if guard else 0,
        owned_server_stopped=guard.process.returncode is not None if guard else True,
    )
    report["passed"] = len(report["cases"]) == 3 and all(
        c["status"] == "validated" and len(c["entities"]) >= 2 and len(c["assertions"]) >= 1
        for c in report["cases"]
    )
    target = output / (
        f"quality-{selected_case}-report.json" if selected_case else "quality-report.json"
    )
    target.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    os.chmod(target, 0o600)
    print(json.dumps({k: v for k, v in report.items() if k not in {"cases", "usage"}}), flush=True)
    return report["passed"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", choices=("cholesterol", "coeliac", "thyroid"))
    parser.add_argument(
        "--pdf", type=Path, default=CACHE / "smoke-medical/child-diabetes-annual-review.pdf"
    )
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(quality(args.pdf, selected_case=args.case)) else 1)
