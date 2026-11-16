"""Remote-only inference: Alibaba plus pinned bbio Ollama over an SSH tunnel."""

import asyncio
import json
import os
from pathlib import Path

import httpx

from domain_kg.providers.profiles import ProviderRegistry
from eval import kg_quality_bbio as bbio
from eval.public_qa_run import bounded_call
from eval.source_role_eval import digest
from eval.source_role_run import credential, read, write_new

API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
WORK_MODELS = (
    *bbio.MODELS,
    "qwen3.8-27b",
    "qwen3.6-35b-a3b",
    "qwen3.5-122b-a10b",
    "qwen3.5-397b-a17b",
)
JUDGE = "qwen3.5-397b-a17b"
EMBEDDING = "text-embedding-v4"
MODEL_SIZES = dict(zip(WORK_MODELS, (1.5, 7, 27, 35, 122, 397), strict=True))


def save_once(path, value):
    if path.exists():
        if read(path) != json.loads(json.dumps(value)):
            raise ValueError(f"immutable artifact changed: {path.name}")
    else:
        write_new(path, value)


def validate_endpoint(endpoint):
    if endpoint != API_BASE:
        raise ValueError("API-only evaluation requires the fixed Alibaba HTTPS endpoint")


def cloud_client():
    # bounded_call owns the total deadline. Do not let httpx's default 5s read
    # timeout preempt a valid slow cloud completion.
    return httpx.AsyncClient(
        trust_env=False, follow_redirects=False, timeout=httpx.Timeout(180, connect=10)
    )


def cloud_key(secret_file=None, config_path=Path("secrets/local-browser/providers.json")):
    if secret_file is not None or os.environ.get("DASHSCOPE_API_KEY"):
        return credential(secret_file)
    registry = ProviderRegistry.from_file(config_path)
    connection = registry.connections.get("alibaba")
    if connection is None:
        raise ValueError("configured Alibaba API credentials are required")
    validate_endpoint(connection.endpoint)
    return connection.api_key.get_secret_value()


def chat_request(model, instructions, data, max_tokens):
    if model not in WORK_MODELS:
        raise ValueError("unapproved API model")
    payload = {
        "model": model,
        "temperature": 0,
        "store": False,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
        ],
    }
    if model.startswith("qwen3"):
        payload["enable_thinking"] = False
    return payload


class CloudCalls:
    """Record bounded transport retries; never retry judgments or ambiguous launches."""

    def __init__(self, root, key):
        self.root, self.key = root, key
        self.locks = {}
        self.remote_lock = asyncio.Lock()
        self.retry_calls = {}

    async def raw(self, client, payload, *, kind="chat"):
        current = self
        for attempt in range(4):
            row = await current._raw_once(client, payload, kind=kind)
            if (
                row["error"]
                not in {
                    "ConnectError",
                    "ConnectTimeout",
                    "ReadTimeout",
                    "timeout",
                    "http_429",
                    "http_500",
                    "http_502",
                    "http_503",
                    "http_504",
                }
                or attempt == 3
            ):
                return row
            await asyncio.sleep(0.5 * (attempt + 1))
            current = self.retry_calls.setdefault(
                attempt + 2, CloudCalls(self.root / f"retry-{attempt + 2}", self.key)
            )
        raise AssertionError("unreachable retry state")

    async def _raw_once(self, client, payload, *, kind="chat"):
        if client.follow_redirects:
            raise ValueError("API-only calls must not follow redirects")
        if kind not in ("chat", "embedding"):
            raise ValueError("unknown API operation")
        allowed = WORK_MODELS if kind == "chat" else (EMBEDDING,)
        if payload["model"] not in allowed:
            raise ValueError("unapproved API model")
        if len(json.dumps(payload).encode()) > 120_000:
            raise ValueError("API payload exceeds frozen request bound")
        if (
            kind == "chat"
            and not 1 <= payload.get("max_tokens", payload.get("max_completion_tokens", 0)) <= 16384
        ):
            raise ValueError("invalid API output budget")
        remote = payload["model"] in bbio.MODELS
        wire = bbio.wire_request(payload) if remote else payload
        url = (
            (bbio.BASE + "/api/chat")
            if remote
            else API_BASE + ("/embeddings" if kind == "embedding" else "/chat/completions")
        )
        launch_data = {"endpoint": url, "payload": payload, "request_sha256": digest(payload)}
        if remote:
            launch_data.update(
                wire_payload=wire,
                inference_host=bbio.HOST,
                model_identity=bbio.MODELS[payload["model"]],
            )
        identity = digest(launch_data) if remote else digest([url, payload])
        lock = self.locks.setdefault(identity, asyncio.Lock())
        async with lock:
            launch = self.root / (identity + ".launch.json")
            result = self.root / (identity + ".result.json")
            if launch.exists():
                if read(launch) != launch_data:
                    raise ValueError("immutable API request changed")
                if not result.exists():
                    return {
                        "error": "ambiguous_launch",
                        "response": None,
                        "http_status": None,
                        "request_sha256": digest(payload),
                        "model": payload["model"],
                    }
                row = read(result)
                if row["request_sha256"] != digest(payload):
                    raise ValueError("API result request identity changed")
                self.check_access(row)
                return row
            if remote:
                async with self.remote_lock:
                    bbio.validate_identity(await bbio.inventory(client), payload["model"])
                    write_new(launch, launch_data)
                    row = await bounded_call(client, url, wire, None, deadline=bbio.DEADLINE)
                    row["native_response"] = row.get("response")
                    if not row["error"]:
                        try:
                            row["response"] = bbio.completion(row["response"], wire["model"])
                        except (AttributeError, TypeError, ValueError):
                            row["error"] = "invalid_remote_completion"
                    # Recheck the tag after inference as well; never accept a changed model.
                    try:
                        bbio.validate_identity(await bbio.inventory(client), payload["model"])
                    except (httpx.HTTPError, ValueError):
                        row["error"] = "remote_identity_unverified_after_call"
            else:
                write_new(launch, launch_data)
                row = await bounded_call(client, url, payload, self.key, deadline=180)
            row.update(request_sha256=digest(payload), model=payload["model"])
            write_new(result, row)
            self.check_access(row)
            return row

    @staticmethod
    def check_access(row):
        if row["error"] in ("http_401", "http_403") or (
            isinstance((row.get("response") or {}).get("error"), dict)
            and row["response"]["error"].get("code") == "Arrearage"
        ):
            raise RuntimeError("Alibaba authorization/billing prevents API evaluation")

    async def chat(self, client, payload):
        row = {**await self.raw(client, payload), "content": None}
        if row["error"]:
            return row
        try:
            choice = row["response"]["choices"][0]
            message = choice["message"]
            if (
                choice["finish_reason"] != "stop"
                or not isinstance(message.get("content"), str)
                or not message["content"].strip()
                or message.get("tool_calls")
                or message.get("refusal")
            ):
                raise ValueError("invalid completion")
            row["content"] = message["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            row["error"] = "invalid_completion"
        return row


async def catalog(key, *, models=WORK_MODELS):
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=30) as client:
        response = await client.get(
            API_BASE + "/models", headers={"Authorization": "Bearer " + key}
        )
        response.raise_for_status()
        ids = sorted(item["id"] for item in response.json()["data"])
        remote_models = [m for m in models if m in bbio.MODELS]
        remote_inventory = await bbio.inventory(client) if remote_models else None
        for model in remote_models:
            bbio.validate_identity(remote_inventory, model)
        ids.extend(remote_models)
    return {
        "endpoint": API_BASE,
        "ids": ids,
        "bbio": remote_inventory,
        "missing": sorted(set((*models, EMBEDDING)) - set(ids)),
    }
