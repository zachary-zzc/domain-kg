"""Bound JSON envelopes before framework parsing; originals keep streaming upload limits."""

from uuid import uuid4

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RequestBodyLimit:
    def __init__(self, app: ASGIApp, max_json_bytes: int = 2 * 1024 * 1024) -> None:
        if max_json_bytes < 1:
            raise ValueError("JSON request limit must be positive")
        self.app, self.limit = app, max_json_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") not in ("POST", "PUT", "PATCH", "DELETE")
            or not (path.startswith(("/api/v1/", "/admin/")) or path.rstrip("/") == "/mcp")
            or path.endswith("/uploads")
        ):
            await self.app(scope, receive, send)
            return

        async def reject() -> None:
            await JSONResponse(
                {
                    "code": "QUOTA_EXCEEDED",
                    "message": "JSON request exceeds its byte limit.",
                    "retryable": False,
                    "request_id": str(uuid4()),
                },
                status_code=413,
                headers={"Cache-Control": "no-store"},
            )(scope, receive, send)

        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    if int(value) > self.limit:
                        await reject()
                        return
                except ValueError:
                    pass  # The actual received bytes are still bounded below.
        parts = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                await reject()
                return
            parts.append(body)
            if not message.get("more_body", False):
                break
        envelope = b"".join(parts)
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                return await receive()
            delivered = True
            return {"type": "http.request", "body": envelope, "more_body": False}

        await self.app(scope, replay, send)
