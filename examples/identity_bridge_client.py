"""Minimal platform-side client. Credentials stay in the calling backend, outside model context.

Supply fresh signed assertions from the approved IdP. Persist each request key and
the exact body before sending; a retry must reuse both. Do not log response tokens.
"""

from uuid import UUID

import httpx


class BridgeFailure(Exception):
    def __init__(self, status: int, code: str, request_id: str, retryable: bool) -> None:
        self.status, self.code, self.request_id, self.retryable = (
            status,
            code,
            request_id,
            retryable,
        )
        super().__init__(f"KG identity request failed: HTTP {status}, {code}, request {request_id}")


class KGIdentityClient:
    def __init__(self, http: httpx.AsyncClient, platform_token: str, integration_id: UUID) -> None:
        # Configure http with the operator-provided KG HTTPS base_url, timeout and trust_env=False.
        self.http, self._credential, self.integration_id = http, platform_token, integration_id

    async def _post(
        self, path: str, body: dict[str, object], request_key: str | None = None
    ) -> dict[str, object]:
        headers = {"Authorization": "Bearer " + self._credential}
        if request_key is not None:
            headers["Idempotency-Key"] = request_key
        response = await self.http.post(path, json=body, headers=headers, follow_redirects=False)
        if response.status_code != 200:
            # Never propagate raw IdP/HTTP response bodies to logs or the model.
            try:
                error = response.json()
                code, request_id = str(error["code"]), str(error["request_id"])
                retryable = error.get("retryable") is True
            except (ValueError, KeyError, TypeError):
                code, request_id, retryable = "INVALID_RESPONSE", "unavailable", False
            raise BridgeFailure(response.status_code, code, request_id, retryable)
        result = response.json()
        if not isinstance(result, dict):
            raise BridgeFailure(200, "INVALID_RESPONSE", "unavailable", False)
        return result

    async def bootstrap(self, assertion: str, request_key: str) -> dict[str, object]:
        return await self._post(
            "/api/v1/identity-bridge/bootstrap",
            {
                "integration_id": str(self.integration_id),
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "subject_token": assertion,
            },
            request_key,
        )

    async def exchange(
        self,
        assertion: str,
        request_key: str,
        kg_ids: tuple[UUID, ...],
        actions: tuple[str, ...] = ("read", "search"),
        connection_seconds: int = 3600,
    ) -> dict[str, object]:
        # This assertion must be fresh: bootstrap already consumed its own assertion's jti.
        return await self._post(
            "/api/v1/identity-bridge/token",
            {
                "integration_id": str(self.integration_id),
                "subject_token_type": "urn:ietf:params:oauth:token-type:jwt",
                "subject_token": assertion,
                "kg_ids": [str(value) for value in kg_ids],
                "actions": list(actions),
                "connection_seconds": connection_seconds,
            },
            request_key,
        )

    async def disable_user(
        self, subject: str, event_id: str, source_version: int
    ) -> dict[str, object]:
        return await self._post(
            "/api/v1/identity-bridge/events",
            {
                "integration_id": str(self.integration_id),
                "external_subject": subject,
                "event_id": event_id,
                "source_version": source_version,
                "operation": "disable",
            },
        )
