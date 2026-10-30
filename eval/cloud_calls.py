"""Explicit bounded Alibaba Cloud evaluation calls; no local process startup."""

import json
import re
import time
from urllib.parse import urlsplit

import httpx


def checked_endpoint(value):
    parsed = urlsplit(value)
    legacy = {
        "dashscope.aliyuncs.com",
        "dashscope-intl.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
        "cn-hongkong.dashscope.aliyuncs.com",
    }
    workspace = re.fullmatch(
        r"llm-[a-z0-9]+\.(?:cn-beijing|ap-southeast-1|us-east-1|eu-central-1|ap-northeast-1|cn-hongkong)\.maas\.aliyuncs\.com",
        parsed.hostname or "",
    )
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/compatible-mode/v1"
        or (parsed.hostname not in legacy and not workspace)
    ):
        raise ValueError("Only a verified official Alibaba Cloud HTTPS endpoint is allowed")
    return value.rstrip("/")


def parse_answer(raw, allowed):
    choice = raw["choices"][0]
    message = choice["message"]
    if choice["finish_reason"] != "stop" or message.get("tool_calls") or message.get("refusal"):
        raise ValueError("Truncated, refused or actionable provider response")
    answer = json.loads(message["content"])
    if (
        not isinstance(answer, dict)
        or set(answer) != {"answer", "citations"}
        or not isinstance(answer["answer"], str)
        or not answer["answer"].strip()
        or not isinstance(answer["citations"], list)
        or len(answer["citations"]) > 3
        or any(not isinstance(c, str) for c in answer["citations"])
        or len(set(answer["citations"])) != len(answer["citations"])
        or not set(answer["citations"]) <= set(allowed)
    ):
        raise ValueError("Malformed or out-of-scope answer")
    return answer


class CloudCalls:
    def __init__(self, endpoint, key, output, *, client=None):
        self.endpoint = checked_endpoint(endpoint)
        self.key, self.output, self.client = key, output, client
        self.owns_client = client is None
        self.records = []
        self.failures = self.timeouts = self.output_tokens = 0
        self.halted = False
        self.started = time.monotonic()
        output.mkdir(parents=True, mode=0o700, exist_ok=True)

    async def close(self):
        if self.owns_client and self.client:
            await self.client.aclose()

    async def invoke(self, case, request, allowed):
        if self.halted:
            raise RuntimeError("Cloud experiment halted; no further request sent")
        if (
            len(self.records) >= 144
            or self.output_tokens >= 27648
            or time.monotonic() - self.started >= 900
        ):
            self.halted = True
            raise RuntimeError("Cloud experiment halted at its cumulative budget")
        if len(json.dumps(request.get("messages", [])).encode()) > 8192:
            raise ValueError("Oversized cloud evaluation prompt")
        if self.client is None:
            self.client = httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=45)
        record = {"case": case, "request": request}
        tick = time.monotonic()
        answer, error = None, None
        try:
            async with self.client.stream(
                "POST",
                self.endpoint + "/chat/completions",
                json=request,
                headers={"Authorization": "Bearer " + self.key},
                follow_redirects=False,
            ) as response:
                body = bytearray()
                async for part in response.aiter_bytes():
                    body.extend(part)
                    if len(body) > 2 * 1024**2:
                        raise ValueError("Oversized cloud response")
                record.update(
                    http_status=response.status_code, raw_response=body.decode(errors="replace")
                )
                if response.status_code != 200:
                    error = "http_" + str(response.status_code)
                    if response.status_code in (301, 302, 307, 308, 401, 403, 404):
                        self.halted = True
                else:
                    raw = json.loads(body)
                    usage = raw["usage"]
                    if any(
                        type(usage[k]) is not int or usage[k] < 0
                        for k in ("prompt_tokens", "completion_tokens")
                    ):
                        raise ValueError("Invalid provider usage")
                    self.output_tokens += usage["completion_tokens"]
                    record["usage"] = usage
                    answer = parse_answer(raw, allowed)
                    self.timeouts = 0
        except httpx.TimeoutException:
            error = "timeout"
            self.timeouts += 1
        except Exception as failure:
            error = type(failure).__name__
        if error:
            self.failures += 1
        self.halted |= self.failures >= 5 or self.timeouts >= 2
        record.update(seconds=round(time.monotonic() - tick, 3), answer=answer, error=error)
        # No headers/credentials are logged; redact even unexpected provider echoes.
        sanitized = json.loads(
            json.dumps(record, ensure_ascii=False).replace(self.key, "[REDACTED]")
        )
        self.records.append(sanitized)
        path = self.output / f"call-{len(self.records):03d}.json"
        path.write_text(json.dumps(sanitized, ensure_ascii=False, indent=2) + "\n")
        path.chmod(0o600)
        return {
            "answer": sanitized["answer"],
            "error": error,
            "seconds": sanitized["seconds"],
            "call": len(self.records),
        }
