"""Explicit loopback-only calls and exact provisional reference scoring."""

import asyncio
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid5

import httpx
from pydantic import BaseModel

from domain_kg.graph.models import DomainSchema
from domain_kg.ingestion.models import ParsedChunk, SourceLocation, Span
from domain_kg.providers.compact import NamedContent, expand
from domain_kg.providers.extraction import extraction_payload, strict_schema
from eval.recursive_models import Mentions, Proposals
from eval.recursive_prompts import INSTRUCTIONS


def relation_score(predicted, gold):
    def key(row):
        return tuple(str(value).strip().casefold() for value in row)

    found, expected = {key(r) for r in predicted}, {key(r) for r in gold}
    tp = len(found & expected)
    precision = tp / len(found) if found else 0
    recall = tp / len(expected) if expected else None
    return {
        "tp": tp,
        "fp": len(found - expected),
        "fn": len(expected - found),
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if recall and precision else 0,
        "no_answer_fp": bool(found) and not expected,
        "direction_errors": sum((t, p, s, q) in expected for s, p, t, q in found - expected),
        "polarity_errors": sum(
            any(
                (s, p, t, other) in expected
                for other in ("positive", "negative", "uncertain")
                if other != q
            )
            for s, p, t, q in found - expected
        ),
    }


def claims(content):
    names = {entity.local_id: entity.name for entity in content.entities}
    return [
        [names[a.subject], a.predicate, names[a.target], a.polarity] for a in content.assertions
    ]


def case_chunk(case, parent: ParsedChunk) -> ParsedChunk:
    if "blocks" in case:
        indices = case["blocks"]
        if len(set(indices)) != len(indices) or any(
            type(i) is not int or not 0 <= i < len(parent.spans) for i in indices
        ):
            raise ValueError("Invalid frozen source block selection")
        spans = tuple(parent.spans[i] for i in indices)
    else:
        spans = tuple(
            Span(
                location=SourceLocation(
                    kind="text", block_id=uuid5(parent.id, case["id"] + str(i)), paragraph=i
                ),
                start=0,
                end=len(line),
                quote=line,
            )
            for i, line in enumerate(case["lines"])
        )
    return ParsedChunk.model_validate(
        parent.model_copy(
            update={
                "id": uuid5(parent.id, "recursive-v1/" + case["id"]),
                "text": "\n".join(s.quote for s in spans),
                "spans": spans,
                "chunker_version": "recursive-experiment-v1",
            }
        )
    )


@dataclass
class CallLimits:
    endpoint: str
    max_calls: int = 48
    calls: int = 0
    output_tokens: int = 0
    started: float = field(default_factory=time.monotonic)

    def __post_init__(self):
        endpoint = urlsplit(self.endpoint)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname != "127.0.0.1"
            or (
                endpoint.port != 29500
                or endpoint.username
                or endpoint.password
                or endpoint.path != "/v1"
                or endpoint.query
                or endpoint.fragment
            )
        ):
            raise ValueError("Only the owned loopback model is allowed")
        if type(self.max_calls) is not int or not 1 <= self.max_calls <= 48:
            raise ValueError("Invalid call budget")

    def reserve(self):
        if (
            self.calls >= self.max_calls
            or time.monotonic() - self.started >= 1800
            or (self.output_tokens >= 27648)
        ):
            raise RuntimeError("Experiment model budget exhausted")
        self.calls += 1


class LocalCalls:
    def __init__(self, profile, connection, guard, output: Path):
        self.profile, self.connection, self.guard, self.output = profile, connection, guard, output
        self.limits = CallLimits(connection.endpoint)
        self.records = []
        self.case_id = ""
        self.client = httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=httpx.Timeout(120, connect=5)
        )

    async def close(self):
        await self.client.aclose()

    async def invoke(self, stage, request):
        # llama.cpp applies response grammar outside the prompt in this pinned profile.
        size = len(json.dumps(request["messages"], ensure_ascii=False).encode())
        if size + request["max_completion_tokens"] + 256 > 4096:
            raise RuntimeError("Experiment prompt exceeds conservative context budget")
        await self.guard.require_alive()
        self.limits.reserve()
        record = {"case": self.case_id, "stage": stage, "request": request}
        started = time.monotonic()
        try:
            async with (
                asyncio.timeout(120),
                self.client.stream(
                    "POST",
                    self.connection.endpoint + "/chat/completions",
                    json=request,
                    headers={
                        "Authorization": "Bearer " + self.connection.api_key.get_secret_value()
                    },
                ) as response,
            ):
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2 * 1024**2:
                        raise RuntimeError("Experiment response exceeds size limit")
                record["http_status"] = response.status_code
                record["raw_response"] = body.decode(errors="replace")
                if response.status_code != 200:
                    raise RuntimeError("Local model request failed")
            raw = json.loads(body)
            usage = raw["usage"]
            if any(
                type(usage[k]) is not int or not 0 <= usage[k] <= 100000
                for k in ("prompt_tokens", "completion_tokens")
            ):
                raise RuntimeError("Invalid model usage")
            self.limits.output_tokens += usage["completion_tokens"]
            record["usage"] = usage
            choice = raw["choices"][0]
            if (
                choice["finish_reason"] != "stop"
                or choice["message"].get("tool_calls")
                or (choice["message"].get("refusal"))
            ):
                raise RuntimeError("Truncated, refused or actionable model output")
            result = json.loads(choice["message"]["content"])
            await self.guard.require_alive()
            return result
        except BaseException as error:
            record["error"] = type(error).__name__
            raise
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            self.records.append(record)
            path = self.output / f"call-{self.limits.calls:03d}.json"
            path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n")
            path.chmod(0o600)
            print(
                json.dumps(
                    {
                        "case": self.case_id,
                        "stage": stage,
                        "call": self.limits.calls,
                        "seconds": record["seconds"],
                        "error": record.get("error"),
                    }
                ),
                flush=True,
            )

    async def __call__(self, stage: str, payload: dict, response_model: type[BaseModel]):
        schema = response_model.model_json_schema()
        if response_model is Mentions:
            schema["$defs"]["RefEntity"]["properties"]["type"]["enum"] = list(payload["types"])
        if response_model is Proposals:
            props = schema["$defs"]["Proposal"]["properties"]
            props["predicate"]["enum"] = list(payload["predicates"])
            names = [entity["name"] for entity in payload["entities"]]
            if names:
                props["subject"]["enum"] = names
                props["target"]["enum"] = names
        request = {
            "model": self.profile.extraction_model,
            "temperature": 0,
            "seed": 19,
            "store": False,
            "max_completion_tokens": 512,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS[stage]},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "recursive_" + stage,
                    "strict": True,
                    "schema": strict_schema(schema),
                },
            },
        }
        return await self.invoke(stage, request)

    async def baseline(self, chunk: ParsedChunk, schema: DomainSchema):
        request = extraction_payload(chunk, schema, self.profile)
        request["seed"] = 19
        value = NamedContent.model_validate(await self.invoke("baseline", request))
        return expand(value, chunk, schema, self.profile.id).model_dump(mode="json")
