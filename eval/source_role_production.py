"""Budgeted cloud smoke through the real extraction and LightRAG mapping clients.

Uses synthetic public text, no stores or accepted corpus. Reservations normalize
the token-cap name only for the existing cost ledger; the actual wire hash is
recorded separately and the production request is sent unchanged.
"""

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import httpx
from pydantic import SecretStr

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for
from domain_kg.ingestion.extraction import Extractor
from domain_kg.ingestion.models import ParsedChunk, SourceLocation, Span
from domain_kg.models import Namespace
from domain_kg.providers.extraction import ExtractionClient
from domain_kg.providers.profiles import ProviderConnection, ProviderProfile
from eval.source_role_eval import EXTRACTOR, JUDGE, digest
from eval.source_role_expand import CumulativeLedger, verify
from eval.source_role_run import ENDPOINT, PRICES, credential, read, run_lock, write_new

TEXT = "Alpha uses Beta. Beta is part of Gamma."


def uid(name):
    return uuid5(NAMESPACE_URL, "domain-kg-public-production-probe-v1/" + name)


def fixture():
    ns = Namespace(org_id=uid("org"), kg_id=uid("kg"))
    span = Span(
        location=SourceLocation(kind="text", block_id=uid("block"), paragraph=0),
        start=0,
        end=len(TEXT),
        quote=TEXT,
    )
    return ParsedChunk(
        **ns.model_dump(),
        material_id=uid("material"),
        version_id=uid("version"),
        id=uid("chunk"),
        text=TEXT,
        spans=(span,),
        parser_version="synthetic-probe-v1",
        chunker_version="synthetic-probe-v1",
    )


class ReservedTransport(httpx.AsyncBaseTransport):
    def __init__(self, ledger, label, inner=None):
        self.ledger, self.label = ledger, label
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0)
        self.status = None

    async def handle_async_request(self, request):
        if str(request.url) != ENDPOINT or request.method != "POST":
            raise ValueError("unexpected production endpoint")
        payload = json.loads(request.content)
        accounting = {**payload, "max_tokens": payload["max_completion_tokens"]}
        if not self.ledger.reserve(self.label, accounting):
            raise ValueError("previous dispatch cannot be repeated")
        path = self.ledger.directory / (digest(self.label) + ".wire.json")
        write_new(
            path,
            {
                "label": self.label,
                "wire_sha256": hashlib.sha256(request.content).hexdigest(),
                "payload_sha256": digest(payload),
                "ledger_payload_sha256": digest(accounting),
                "reservation_note": "max_tokens added only to reservation, never wire request",
            },
        )
        response = await self.inner.handle_async_request(request)
        self.status = response.status_code
        return response

    async def aclose(self):
        await self.inner.aclose()


async def execute(root, key):
    frozen, _ = verify(root)
    pilot = Path(frozen["pilot"])
    with run_lock(root), run_lock(pilot):
        if len(list((root / "documents").glob("*.json"))) != 228:
            raise ValueError("construction must finish first")
        ledger = CumulativeLedger(root / "calls", [pilot / "calls"])
        output = root / "production-probe"
        output.mkdir(exist_ok=True)
        files = [
            "eval/source_role_production.py",
            "src/domain_kg/providers/profiles.py",
            "src/domain_kg/providers/extraction.py",
            "src/domain_kg/providers/compact.py",
            "src/domain_kg/providers/semantics.py",
            "src/domain_kg/ingestion/extraction.py",
        ]
        manifest = {
            "source": TEXT,
            "code": {
                name: hashlib.sha256(await asyncio.to_thread(Path(name).read_bytes)).hexdigest()
                for name in files
            },
        }
        manifest_path = output / "frozen.json"
        if manifest_path.exists():
            if read(manifest_path) != manifest:
                raise ValueError("production probe code changed")
        else:
            write_new(manifest_path, manifest)
        chunk = fixture()
        ns = Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id)
        schema = schema_for(ns, "general")
        for model in (EXTRACTOR, JUDGE):
            modes = [(f, "json_object") for f in ("full", "source_refs_v1", "source_names_v1")]
            modes.append(("source_names_v1", "json_schema"))
            for format, response_format in modes:
                label = f"production-probe-v1:{model}:{format}:{response_format}"
                destination = output / (digest(label) + ".json")
                if destination.exists():
                    continue
                launch, result = ledger.paths(label)
                if launch.exists():
                    raise ValueError("ambiguous production launch must not be repeated")
                profile = ProviderProfile(
                    id=uid(label),
                    connection_ref="approved-public-experiment",
                    extraction_model=model,
                    extraction_format=format,
                    extraction_response_format=response_format,
                    extraction_enable_thinking=False,
                    extraction_semantics="source_roles_v1",
                    embedding_model="unused",
                    embedding_dimensions=1024,
                    allowed_namespaces=(ns,),
                    data_policy_approved=True,
                    max_completion_tokens=4096,
                    extraction_context_tokens=32768,
                )
                connection = ProviderConnection(
                    ref=profile.connection_ref,
                    endpoint=ENDPOINT.removesuffix("/chat/completions"),
                    api_key=SecretStr(key),
                )
                authorizations, usage = [], []

                async def authorize(incoming, authorizations=authorizations):
                    if incoming != chunk:
                        raise ValueError("source or scope changed")
                    authorizations.append(str(incoming.id))

                async def record(value, usage=usage):
                    usage.append(value)

                transport = ReservedTransport(ledger, label)
                client = ExtractionClient(
                    profile,
                    connection,
                    authorize=authorize,
                    record_usage=record,
                    transport=transport,
                )
                row = {
                    "label": label,
                    "model": model,
                    "error": None,
                    "content": None,
                    "usage": None,
                    "actual_rmb": None,
                }
                batch = None
                try:
                    batch = await Extractor(client).extract(chunk, schema)
                    row["content"] = "canonical_validation_and_lightrag_mapping_passed"
                except DomainError as error:
                    row["error"] = str(error.code)
                finally:
                    await client.close()
                if usage:
                    value = usage[0]
                    row["usage"] = {
                        "prompt_tokens": value.input_tokens,
                        "completion_tokens": value.output_tokens,
                    }
                    prices = PRICES[model]
                    row["actual_rmb"] = (
                        value.input_tokens * prices[0] + value.output_tokens * prices[1]
                    ) / 1_000_000
                if launch.exists():
                    write_new(result, row)
                write_new(
                    destination,
                    {
                        **row,
                        "http_status": transport.status,
                        "authorizations": len(authorizations),
                        "profile": profile.model_dump(mode="json"),
                        "batch": batch.model_dump(mode="json") if batch is not None else None,
                    },
                )
                print(
                    json.dumps(
                        {
                            "label": label,
                            "http_status": transport.status,
                            "error": row["error"],
                            "actual_rmb": row["actual_rmb"],
                        }
                    ),
                    flush=True,
                )
                if transport.status in {401, 403}:
                    raise ValueError("provider authorization failed")
                if (
                    row["actual_rmb"] is not None
                    and row["actual_rmb"] > read(launch)["reserved_rmb"]
                ):
                    raise ValueError("provider usage exceeded reservation")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--secret-file", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(execute(args.root, credential(args.secret_file)))


if __name__ == "__main__":
    main()
