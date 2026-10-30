"""Operator-managed model setup; secrets stay in the configured private file."""

import asyncio
import fcntl
import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, SecretStr, StringConstraints, field_validator, model_validator

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.models import FrozenModel, IdempotencyKey, Namespace, NonNegative
from domain_kg.providers.profiles import (
    ProviderConfig,
    ProviderConnection,
    ProviderProfile,
    ProviderRegistry,
)
from domain_kg.providers.service import ProviderService

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Endpoint = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]


class ModelConfiguration(FrozenModel):
    name: Name
    endpoint: Endpoint
    api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    api_format: Literal["openai", "anthropic"] = "openai"
    credential_profile_id: UUID | None = None
    extraction_model: Name
    supports_images: bool = Field(default=False, exclude_if=lambda v: not v)
    image_input_tokens: Annotated[int, Field(ge=256, le=32768)] = Field(
        default=4096, exclude_if=lambda v: v == 4096
    )
    embedding_model: Name
    embedding_endpoint: Endpoint = ""
    embedding_api_key: SecretStr = Field(default=SecretStr(""), repr=False)
    embedding_profile_id: UUID | None = None
    embedding_send_dimensions: bool = False
    response_format: Literal["json_object", "json_schema"] = "json_object"
    thinking: Literal["default", "disabled", "enabled"] = "default"
    extraction_context_tokens: Annotated[int, Field(ge=1024, le=131072)] = 16384
    embedding_context_tokens: Annotated[int, Field(ge=256, le=32768)] = 8192
    max_completion_tokens: Annotated[int, Field(ge=128, le=16384)] = 4096
    timeout_seconds: Annotated[int, Field(ge=1, le=120)] = 60
    data_approval: Literal["approved"]
    allow_restricted: bool = False
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey

    @field_validator("endpoint", "embedding_endpoint")
    @classmethod
    def endpoint_shape(cls, value: str) -> str:
        if not value:
            return value
        if urlsplit(value).query:
            raise ValueError("Use a base URL without query parameters")
        return ProviderConnection(ref="validation", endpoint=value, api_key=SecretStr("")).endpoint

    @model_validator(mode="after")
    def connection_shape(self) -> "ModelConfiguration":
        if not self.endpoint:
            raise ValueError("A model API base URL is required")
        if self.embedding_api_key.get_secret_value() and not self.embedding_endpoint:
            raise ValueError("A separate embedding key requires an embedding base URL")
        if self.embedding_profile_id and (
            self.embedding_endpoint or self.embedding_api_key.get_secret_value()
        ):
            raise ValueError(
                "Choose either an existing embedding configuration or a separate endpoint"
            )
        if self.api_format == "anthropic" and not (
            self.embedding_profile_id or self.embedding_endpoint
        ):
            raise ValueError("Claude requires a separate embedding configuration")
        if self.api_format == "anthropic" and self.thinking != "default":
            raise ValueError("Native Claude does not support this thinking override")
        for key in (self.api_key, self.embedding_api_key):
            value = key.get_secret_value()
            if len(value) > 16384 or any(char.isspace() for char in value):
                raise ValueError("Invalid API key")
        return self


class ProfileApproval(FrozenModel):
    profile_id: UUID
    data_approval: Literal["approved"]
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey


@dataclass(frozen=True)
class PreparedModel:
    profile: ProviderProfile
    chat: ProviderConnection
    embedding: ProviderConnection
    connections: tuple[ProviderConnection, ...]


def prepare_model(
    registry: ProviderRegistry,
    identifier: UUID,
    namespace: Namespace,
    command: ModelConfiguration,
    *,
    dimensions: int,
) -> PreparedModel:
    key = command.api_key
    if command.credential_profile_id:
        old, original = registry.resolve(command.credential_profile_id, namespace, restricted=False)
        if not key.get_secret_value():
            if command.endpoint == original.endpoint and command.api_format == original.api_format:
                key = original.api_key
            elif urlsplit(command.endpoint).hostname not in ("127.0.0.1", "localhost", "::1"):
                raise DomainError(
                    ErrorCode.VALIDATION_ERROR,
                    "Enter a new API key when changing the API URL or protocol.",
                )
    connection = ProviderConnection(
        ref=f"model-{identifier}",
        endpoint=command.endpoint,
        api_key=key,
        api_format=command.api_format,
    )
    embedding = (
        ProviderConnection(
            ref=f"embedding-{identifier}",
            endpoint=command.embedding_endpoint,
            api_key=command.embedding_api_key,
        )
        if command.embedding_endpoint
        else connection
    )
    inherited = None
    if command.embedding_profile_id:
        inherited = registry.profiles.get(command.embedding_profile_id)
        if (
            inherited is None
            or not inherited.data_policy_approved
            or not any(ns.org_id == namespace.org_id for ns in inherited.allowed_namespaces)
            or inherited.embedding_dimensions != dimensions
        ):
            raise inaccessible_reference()
        embedding = registry.connections[
            inherited.embedding_connection_ref or inherited.connection_ref
        ]
    if embedding.api_format != "openai":
        raise DomainError(ErrorCode.VALIDATION_ERROR, "Choose a separate embedding API for Claude.")
    profile = ProviderProfile(
        id=identifier,
        name=command.name,
        connection_ref=connection.ref,
        embedding_connection_ref=embedding.ref if embedding.ref != connection.ref else None,
        extraction_model=command.extraction_model,
        supports_images=command.supports_images,
        image_input_tokens=command.image_input_tokens,
        embedding_model=inherited.embedding_model if inherited else command.embedding_model,
        embedding_dimensions=dimensions,
        data_policy_approved=True,
        allowed_namespaces=(namespace,),
        allow_restricted=command.allow_restricted,
        extraction_format="source_names_v1",
        extraction_semantics="source_roles_v1",
        extraction_response_format=command.response_format,
        extraction_enable_thinking=None
        if command.thinking == "default"
        else command.thinking == "enabled",
        extraction_context_tokens=command.extraction_context_tokens,
        embedding_context_tokens=inherited.embedding_context_tokens
        if inherited
        else command.embedding_context_tokens,
        embedding_send_dimensions=inherited.embedding_send_dimensions
        if inherited
        else command.embedding_send_dimensions,
        embedding_query_prefix=inherited.embedding_query_prefix if inherited else "",
        embedding_batch_size=inherited.embedding_batch_size if inherited else 16,
        embedding_revision=inherited.embedding_revision if inherited else "1",
        max_completion_tokens=command.max_completion_tokens,
        timeout_seconds=command.timeout_seconds,
    )
    connections = (connection, embedding) if command.embedding_endpoint else (connection,)
    return PreparedModel(profile, connection, embedding, connections)


def configuration_unavailable() -> DomainError:
    return DomainError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        "Model setup requires a writable owner-only provider configuration file on the server.",
    )


class ProfileFile:
    """Serialize edits across API processes and replace the complete file atomically."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _update(self, edit: Callable[[ProviderConfig], ProviderConfig]) -> None:
        try:
            descriptor = os.open(
                self.path.with_suffix(self.path.suffix + ".lock"),
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            with os.fdopen(descriptor, "r+") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                registry = ProviderRegistry.from_file(self.path)
                config = ProviderConfig(
                    budgets=registry.budgets,
                    profiles=tuple(registry.profiles.values()),
                    connections=tuple(registry.connections.values()),
                    default_profile_id=registry.default_profile_id,
                )
                updated = edit(config)
                ProviderRegistry(updated)  # Check complete connection/profile references.
                if updated == config:
                    return
                raw = updated.model_dump(mode="json")
                for row, connection in zip(raw["connections"], updated.connections, strict=True):
                    row["api_key"] = connection.api_key.get_secret_value()
                handle, temporary = tempfile.mkstemp(prefix=".providers-", dir=self.path.parent)
                try:
                    with os.fdopen(handle, "w") as output:
                        json.dump(raw, output, ensure_ascii=False, indent=2)
                        output.write("\n")
                        output.flush()
                        os.fsync(output.fileno())
                    os.replace(temporary, self.path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
        except (OSError, ValueError):
            raise configuration_unavailable() from None

    def add(
        self,
        identifier: UUID,
        namespace: Namespace,
        command: ModelConfiguration,
        *,
        dimensions: int,
    ) -> UUID:
        def edit(config: ProviderConfig) -> ProviderConfig:
            prepared = prepare_model(
                ProviderRegistry(config), identifier, namespace, command, dimensions=dimensions
            )
            profile, connections = prepared.profile, prepared.connections
            old = next((item for item in config.profiles if item.id == identifier), None)
            if old is not None:
                old_connections = {item.ref: item for item in config.connections}
                if (
                    namespace not in old.allowed_namespaces
                    or old.model_dump(exclude={"allowed_namespaces"})
                    != profile.model_dump(exclude={"allowed_namespaces"})
                ) or any(old_connections.get(item.ref) != item for item in connections):
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Model setup key has different input."
                    )
                return config
            return config.model_copy(
                update={
                    "profiles": (*config.profiles, profile),
                    "connections": (*config.connections, *connections),
                }
            )

        self._update(edit)
        return identifier

    def prepare(
        self,
        identifier: UUID,
        namespace: Namespace,
        command: ModelConfiguration,
        *,
        dimensions: int,
    ) -> PreparedModel:
        return prepare_model(
            ProviderRegistry.from_file(self.path),
            identifier,
            namespace,
            command,
            dimensions=dimensions,
        )

    def set_default(self, namespace: Namespace, profile_id: UUID) -> None:
        def edit(config: ProviderConfig) -> ProviderConfig:
            profile = next((item for item in config.profiles if item.id == profile_id), None)
            if (
                profile is None
                or not profile.data_policy_approved
                or not any(ns.org_id == namespace.org_id for ns in profile.allowed_namespaces)
            ):
                raise inaccessible_reference()
            return config.model_copy(update={"default_profile_id": profile_id})

        self._update(edit)

    def approve(self, namespace: Namespace, profile_id: UUID, *, dimensions: int) -> UUID:
        def edit(config: ProviderConfig) -> ProviderConfig:
            original = next((item for item in config.profiles if item.id == profile_id), None)
            if (
                original is None
                or not original.data_policy_approved
                or not any(item.org_id == namespace.org_id for item in original.allowed_namespaces)
            ):
                raise inaccessible_reference()
            if original.embedding_dimensions != dimensions:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Embedding dimensions differ from this KG."
                )
            if namespace in original.allowed_namespaces:
                return config
            # Approval adds a namespace only. Model/cache revision and other KGs are unchanged.
            approved = original.model_copy(
                update={"allowed_namespaces": (*original.allowed_namespaces, namespace)}
            )
            return config.model_copy(
                update={
                    "profiles": tuple(
                        approved if item.id == profile_id else item for item in config.profiles
                    )
                }
            )

        self._update(edit)
        return profile_id


class ModelSetup:
    def __init__(self, providers: ProviderService) -> None:
        self.providers, self.policy = providers, providers.policy

    async def authorize(self, access: AccessContext, kg_id: UUID) -> AccessContext:
        current = await self.policy.revalidate(access, action=Action.CONFIGURE)
        if kg_id not in current.kg_ids:
            raise inaccessible_reference()
        await self.policy.resolve_access(
            current.principal, [], Action.REGISTER_BACKEND, org_id=current.org_id
        )
        return current

    def store(self) -> ProfileFile:
        path = self.providers.registry.config_path
        if path is None:
            raise configuration_unavailable()
        return ProfileFile(path)

    async def prepare(
        self, access: AccessContext, kg_id: UUID, command: ModelConfiguration
    ) -> PreparedModel:
        current = await self.authorize(access, kg_id)
        identifier = uuid5(
            NAMESPACE_URL,
            f"model-setup/{current.org_id}/{kg_id}/{current.principal.issuer}/"
            f"{current.principal.subject}/{command.idempotency_key}",
        )
        try:
            selected = await self.providers.selected(current, kg_id)
        except DomainError as error:
            if error.code != ErrorCode.PROVIDER_UNAVAILABLE:
                raise
            selected = None
        if (
            selected
            and selected["revision"] != command.expected_revision
            and selected["profile_id"] != str(identifier)
        ):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Model selection changed. Refresh settings."
            )
        prepared = await asyncio.to_thread(
            self.store().prepare,
            identifier,
            Namespace(org_id=current.org_id, kg_id=kg_id),
            command,
            dimensions=self.providers.router.dimensions,
        )
        kg = await self.policy.database.kgs.find_one(
            {"org_id": str(current.org_id), "id": str(kg_id)}
        )
        if kg is None:
            raise inaccessible_reference()
        from domain_kg.providers.profiles import embedding_space_id

        if (
            selected
            and kg["published_revision"]
            and selected.get("embedding_space_id")
            != embedding_space_id(prepared.profile, prepared.embedding)
        ):
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Embedding changes require a new generation."
            )
        return prepared

    async def set_default(self, access: AccessContext, kg_id: UUID, profile_id: UUID) -> None:
        current = await self.authorize(access, kg_id)
        # A default is a selection hint, never an approval for another organization.
        self.providers.registry.resolve(
            profile_id, Namespace(org_id=current.org_id, kg_id=kg_id), restricted=False
        )
        await asyncio.to_thread(
            self.store().set_default,
            Namespace(org_id=current.org_id, kg_id=kg_id),
            profile_id,
        )
        await self.authorize(current, kg_id)
        self.providers.registry.refresh()

    async def save(
        self, access: AccessContext, kg_id: UUID, command: ModelConfiguration | ProfileApproval
    ) -> int:
        current = await self.authorize(access, kg_id)
        try:
            selected = await self.providers.selected(current, kg_id)
        except DomainError as error:
            if error.code != ErrorCode.PROVIDER_UNAVAILABLE:
                raise
            selected = None
        if selected and selected["revision"] != command.expected_revision:
            # bind() owns idempotent replay; avoid adding another configuration on a stale form.
            identifier = (
                command.profile_id
                if isinstance(command, ProfileApproval)
                else uuid5(
                    NAMESPACE_URL,
                    f"model-setup/{current.org_id}/{kg_id}/{current.principal.issuer}/"
                    f"{current.principal.subject}/{command.idempotency_key}",
                )
            )
            if selected["profile_id"] != str(identifier):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Model selection changed. Refresh settings."
                )
        namespace = Namespace(org_id=current.org_id, kg_id=kg_id)
        store = self.store()
        if isinstance(command, ProfileApproval):
            identifier = await asyncio.to_thread(
                store.approve,
                namespace,
                command.profile_id,
                dimensions=self.providers.router.dimensions,
            )
        else:
            identifier = uuid5(
                NAMESPACE_URL,
                f"model-setup/{current.org_id}/{kg_id}/{current.principal.issuer}/"
                f"{current.principal.subject}/{command.idempotency_key}",
            )
            await asyncio.to_thread(
                store.add,
                identifier,
                namespace,
                command,
                dimensions=self.providers.router.dimensions,
            )
        current = await self.authorize(current, kg_id)
        self.providers.registry.refresh()
        revision = await self.providers.bind(
            current,
            kg_id,
            identifier,
            expected_revision=command.expected_revision,
            idempotency_key=command.idempotency_key,
        )
        await self.policy.database.policy_audit.update_one(
            {
                "operation": "browser_model_setup",
                "org_id": str(current.org_id),
                "kg_id": str(kg_id),
                "key": command.idempotency_key,
            },
            {
                "$setOnInsert": {
                    "issuer": current.principal.issuer,
                    "subject": current.principal.subject,
                    "profile_id": str(identifier),
                    "revision": revision,
                }
            },
            upsert=True,
        )
        return revision
