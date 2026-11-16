"""Pinned remote Ollama models, reached only through the explicit bbio SSH tunnel."""

BASE = "http://127.0.0.1:29434"
HOST = "bbio"
DEADLINE = 600
MODELS = {
    "bbio-qwen2.5-1.5b-q4_k_m": {
        "tag": "qwen2.5:1.5b-instruct-q4_K_M",
        "digest": "65ec06548149b04c096a120e4a6da9d4017ea809c91734ea5631e89f96ddc57b",
        "parameter_size": "1.5B",
    },
    "bbio-qwen2.5-7b-q4_k_m": {
        "tag": "qwen2.5:latest",
        "digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
        "parameter_size": "7.6B",
    },
}
OPTIONS = {"temperature": 0, "num_ctx": 16384, "num_thread": 8}


async def inventory(client):
    response = await client.get(BASE + "/api/tags", timeout=15)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("models"), list):
        raise ValueError("invalid remote Ollama inventory")
    return data


def validate_identity(data, model):
    spec = MODELS[model]
    matches = [m for m in data["models"] if isinstance(m, dict) and m.get("name") == spec["tag"]]
    if len(matches) != 1:
        raise ValueError("remote model identity missing or ambiguous")
    row = matches[0]
    details = row.get("details") or {}
    if (
        row.get("digest") != spec["digest"]
        or details.get("parameter_size") != spec["parameter_size"]
        or details.get("quantization_level") != "Q4_K_M"
    ):
        raise ValueError("remote model identity changed; freeze a new experiment")


def wire_request(payload):
    if payload.get("response_format") != {"type": "json_object"}:
        raise ValueError("remote evaluation requires the shared JSON-object protocol")
    return {
        "model": MODELS[payload["model"]]["tag"],
        "messages": payload["messages"],
        "stream": False,
        "format": "json",
        "keep_alive": "2m",
        "options": {
            **OPTIONS,
            "num_predict": payload.get("max_tokens", payload.get("max_completion_tokens")),
        },
    }


def completion(raw, tag):
    message = raw.get("message") or {}
    if (
        raw.get("model") != tag
        or raw.get("done") is not True
        or raw.get("done_reason") != "stop"
        or not isinstance(message.get("content"), str)
        or not message["content"].strip()
        or message.get("tool_calls")
        or message.get("refusal")
    ):
        raise ValueError("invalid or truncated remote completion")
    counts = [raw.get(k) for k in ("prompt_eval_count", "eval_count")]
    if any(type(n) is not int or n < 0 for n in counts):
        raise ValueError("invalid remote token usage")
    return {
        "model": tag,
        "choices": [{"finish_reason": "stop", "message": message}],
        "usage": {
            "prompt_tokens": counts[0],
            "completion_tokens": counts[1],
            "total_tokens": sum(counts),
        },
    }
