"""Explicit small connection checks; cached results never contain keys or provider payloads."""

import hashlib
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

import httpx
from pydantic import Field

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext
from domain_kg.models import SHA256, FrozenModel, Namespace, UTCDateTime
from domain_kg.providers.call_budget import CallBudget
from domain_kg.providers.completions import completion_reply, completion_request
from domain_kg.providers.configuration import ModelConfiguration, ModelSetup, PreparedModel
from domain_kg.providers.embeddings import EmbeddingClient, EmbeddingProfile
from domain_kg.providers.extraction import Usage
from domain_kg.providers.http import provider_headers, provider_json
from domain_kg.providers.profiles import ProviderConnection, embedding_space_id
from domain_kg.providers.service import ProviderService
from domain_kg.providers.usage_budget import BudgetBounds


class ComponentCheck(FrozenModel):
    state: Literal["passed", "failed", "skipped"]
    code: ErrorCode | None = None
    message: str
    milliseconds: Annotated[int, Field(ge=0)] = 0
    dimensions: int | None = None


class ConnectionCheck(Namespace):
    profile_id: UUID
    fingerprint: SHA256
    checked_at: UTCDateTime
    chat: ComponentCheck
    embedding: ComponentCheck

    @property
    def ready(self) -> bool:
        return self.chat.state == self.embedding.state == "passed"


def configuration_fingerprint(prepared: PreparedModel) -> str:
    value = {
        "profile": prepared.profile.model_dump(mode="json", exclude={"allowed_namespaces", "name"}),
        "chat": prepared.chat.model_dump(mode="json", exclude={"api_key"}),
        "embedding": prepared.embedding.model_dump(mode="json", exclude={"api_key"}),
        "chat_key": hashlib.sha256(prepared.chat.api_key.get_secret_value().encode()).hexdigest(),
        "embedding_key": hashlib.sha256(
            prepared.embedding.api_key.get_secret_value().encode()
        ).hexdigest(),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ProviderChecks:
    def __init__(self, providers: ProviderService) -> None:
        self.providers, self.setup = providers, ModelSetup(providers)
        self.database = providers.database

    async def _selected(self, access: AccessContext, kg: UUID) -> PreparedModel:
        current = await self.setup.authorize(access, kg)
        selection = await self.providers.selected(current, kg)
        ns = Namespace(org_id=current.org_id, kg_id=kg)
        profile, chat = self.providers.registry.resolve(
            UUID(selection["profile_id"]), ns, restricted=False
        )
        _, embedding = self.providers.registry.resolve_embedding(profile.id, ns, restricted=False)
        if profile.revision != selection["profile_revision"] or embedding_space_id(
            profile, embedding
        ) != selection.get("embedding_space_id"):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Model selection changed. Refresh settings."
            )
        return PreparedModel(profile, chat, embedding, ())

    async def last(self, access: AccessContext, kg: UUID) -> ConnectionCheck | None:
        prepared = await self._selected(access, kg)
        key = self._key(Namespace(org_id=access.org_id, kg_id=kg), prepared)
        raw = await self.database.provider_connection_checks.find_one(key)
        await self._selected(access, kg)
        return (
            ConnectionCheck.model_validate({k: v for k, v in raw.items() if k != "_id"})
            if raw
            else None
        )

    @staticmethod
    def _key(namespace: Namespace, prepared: PreparedModel) -> dict[str, str]:
        return {
            "org_id": str(namespace.org_id),
            "kg_id": str(namespace.kg_id),
            "profile_id": str(prepared.profile.id),
            "fingerprint": configuration_fingerprint(prepared),
        }

    async def selected(
        self, access: AccessContext, kg: UUID, *, force: bool = False
    ) -> ConnectionCheck:
        return await self._check(access, kg, await self._selected(access, kg), force=force)

    async def configuration(
        self, access: AccessContext, kg: UUID, command: ModelConfiguration, *, force: bool = False
    ) -> ConnectionCheck:
        prepared = await self.setup.prepare(access, kg, command)
        return await self._check(access, kg, prepared, force=force, command=command)

    async def _check(
        self,
        access: AccessContext,
        kg: UUID,
        prepared: PreparedModel,
        *,
        force: bool,
        command: ModelConfiguration | None = None,
    ) -> ConnectionCheck:
        current = await self.setup.authorize(access, kg)
        namespace = Namespace(org_id=current.org_id, kg_id=kg)
        fingerprint = configuration_fingerprint(prepared)

        async def authorize() -> None:
            await self.setup.authorize(current, kg)
            refreshed = (
                await self.setup.prepare(current, kg, command)
                if command
                else await self._selected(current, kg)
            )
            if configuration_fingerprint(refreshed) != fingerprint:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Model configuration changed. Test again."
                )

        await authorize()
        key = self._key(namespace, prepared)
        if not force:
            cached = await self.database.provider_connection_checks.find_one(key)
            if cached:
                previous = ConnectionCheck.model_validate(
                    {k: v for k, v in cached.items() if k != "_id"}
                )
                if previous.ready and previous.checked_at > datetime.now(UTC) - timedelta(
                    minutes=15
                ):
                    await authorize()
                    return previous

        profile = prepared.profile
        chat_result = ComponentCheck(state="skipped", message="Not tested.")
        embedding_result = ComponentCheck(
            state="skipped", message="Fix the chat connection first, then test again."
        )
        started = time.monotonic()
        try:
            payload: dict[str, object] = {
                "model": profile.extraction_model,
                "store": False,
                "max_completion_tokens": 128,
                "messages": [
                    {"role": "system", "content": 'Return ONLY the JSON object {"hello":"ok"}.'},
                    {"role": "user", "content": "Hello"},
                ],
                "response_format": {"type": "json_object"},
            }
            if profile.extraction_response_format == "json_schema":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "hello_check",
                        "strict": True,
                        "schema": {
                            "type": "object",
                            "properties": {"hello": {"type": "string", "enum": ["ok"]}},
                            "required": ["hello"],
                            "additionalProperties": False,
                        },
                    },
                }
            if profile.extraction_enable_thinking is not None:
                payload["enable_thinking"] = profile.extraction_enable_thinking
            endpoint, payload = completion_request(payload, prepared.chat)
            budget = CallBudget(
                self.providers.budgets,
                namespace,
                profile.id,
                BudgetBounds(input_tokens=256, output_tokens=128),
                authorize=authorize,
            )
            async with budget.call() as charge:
                async with httpx.AsyncClient(
                    transport=self.providers.transport,
                    trust_env=False,
                    follow_redirects=False,
                    timeout=httpx.Timeout(min(profile.timeout_seconds, 30), connect=5),
                ) as client:
                    charge.attempted = True
                    async with client.stream(
                        "POST", endpoint, json=payload, headers=provider_headers(prepared.chat)
                    ) as response:
                        reply = completion_reply(
                            await provider_json(response, limit=65536), prepared.chat
                        )
                used = Usage(
                    **namespace.model_dump(),
                    profile_id=profile.id,
                    input_tokens=reply.input_tokens,
                    output_tokens=reply.output_tokens,
                )
                await charge.report(used)
                await self.providers.record_usage(used, probe=True)
                if reply.finish != "stop" or json.loads(reply.text) != {"hello": "ok"}:
                    raise DomainError(
                        ErrorCode.PROVIDER_UNAVAILABLE,
                        "The model did not return the expected Hello JSON. Check "
                        "structured output settings.",
                    )
            await authorize()
            chat_result = ComponentCheck(
                state="passed",
                message="Hello JSON test passed.",
                milliseconds=int((time.monotonic() - started) * 1000),
            )
        except (httpx.HTTPError, ValueError, TimeoutError):
            chat_result = ComponentCheck(
                state="failed",
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message=(
                    "Cannot connect or read a valid response. Check the API URL, "
                    "loaded model and local server."
                ),
            )
        except DomainError as error:
            if error.code in (
                ErrorCode.FORBIDDEN,
                ErrorCode.UNAUTHENTICATED,
                ErrorCode.POLICY_CHANGED,
                ErrorCode.VERSION_CONFLICT,
            ):
                raise
            chat_result = ComponentCheck(state="failed", code=error.code, message=error.message)

        if chat_result.state == "passed":
            started = time.monotonic()

            async def usage(value: Usage) -> None:
                await self.providers.record_usage(value, embedding=True, probe=True)

            embedding_profile = EmbeddingProfile(
                **namespace.model_dump(),
                id=profile.id,
                revision=profile.revision,
                model=profile.embedding_model,
                dimensions=profile.embedding_dimensions,
                space_id=embedding_space_id(profile, prepared.embedding),
            )
            embedding_client = EmbeddingClient(
                profile,
                prepared.embedding,
                authorize=authorize,
                record_usage=usage,
                transport=self.providers.transport,
                budget=CallBudget(
                    self.providers.budgets,
                    namespace,
                    profile.id,
                    BudgetBounds(input_tokens=256),
                    authorize=authorize,
                ),
            )
            try:
                await embedding_client.embed(["Hello"], embedding_profile)
                embedding_result = ComponentCheck(
                    state="passed",
                    message="Embedding test passed.",
                    dimensions=profile.embedding_dimensions,
                    milliseconds=int((time.monotonic() - started) * 1000),
                )
            except DomainError as error:
                if error.code in (
                    ErrorCode.FORBIDDEN,
                    ErrorCode.UNAUTHENTICATED,
                    ErrorCode.POLICY_CHANGED,
                    ErrorCode.VERSION_CONFLICT,
                ):
                    raise
                embedding_result = ComponentCheck(
                    state="failed", code=error.code, message=error.message
                )
            finally:
                await embedding_client.close()
        await authorize()
        report = ConnectionCheck(
            **namespace.model_dump(),
            profile_id=profile.id,
            fingerprint=fingerprint,
            checked_at=datetime.now(UTC),
            chat=chat_result,
            embedding=embedding_result,
        )
        await self.database.provider_connection_checks.create_index(
            [("org_id", 1), ("kg_id", 1), ("profile_id", 1), ("fingerprint", 1)], unique=True
        )
        await self.database.provider_connection_checks.update_one(
            key, {"$set": report.model_dump(mode="json")}, upsert=True
        )
        await authorize()
        return report

    async def models(
        self,
        access: AccessContext,
        kg: UUID,
        *,
        component: Literal["chat", "embedding"],
        command: ModelConfiguration | None = None,
    ) -> tuple[str, ...]:
        current = await self.setup.authorize(access, kg)
        prepared = (
            await self.setup.prepare(current, kg, command)
            if command
            else await self._selected(current, kg)
        )
        connection: ProviderConnection = (
            prepared.chat if component == "chat" else prepared.embedding
        )
        async with httpx.AsyncClient(
            transport=self.providers.transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(10, connect=3),
        ) as client:
            try:
                async with client.stream(
                    "GET", connection.endpoint + "/models", headers=provider_headers(connection)
                ) as response:
                    raw = await provider_json(response, limit=2 * 1024**2)
            except httpx.HTTPError:
                raise DomainError(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "Cannot read the model list. Start the local server or enter a "
                    "model name manually.",
                ) from None
        data = raw.get("data")
        if not isinstance(data, list) or len(data) > 10000:
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE, "The API did not return a supported model list."
            )
        names = []
        for item in data:
            value = item.get("id") if isinstance(item, dict) else None
            if (
                isinstance(value, str)
                and 0 < len(value) <= 200
                and not any(ord(c) < 32 for c in value)
            ):
                names.append(value)
        await self.setup.authorize(current, kg)
        refreshed = (
            await self.setup.prepare(current, kg, command)
            if command
            else await self._selected(current, kg)
        )
        if configuration_fingerprint(refreshed) != configuration_fingerprint(prepared):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Model configuration changed. Test again."
            )
        return tuple(sorted(set(names))[:500])
