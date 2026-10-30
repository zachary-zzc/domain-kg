"""Approved server configuration. Requests can select IDs, never endpoints or secrets."""

import hashlib
import json
import stat
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, Field, SecretStr, StrictBool, field_validator

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.models import FrozenModel, Namespace
from domain_kg.providers.usage_budget import BudgetPolicy


class ProviderConnection(FrozenModel):
    ref: str
    endpoint: str
    api_key: SecretStr = Field(repr=False)
    api_format: Literal["openai", "anthropic"] = Field(
        default="openai", exclude_if=lambda value: value == "openai"
    )

    @field_validator("endpoint")
    @classmethod
    def approved_endpoint(cls, value: str) -> str:
        from domain_kg.identity.oidc import _safe_endpoint

        AnyHttpUrl(value)
        return _safe_endpoint(value).rstrip("/")


class ProviderProfile(FrozenModel):
    id: UUID
    name: Annotated[
        str | None, Field(min_length=1, max_length=200, exclude_if=lambda v: v is None)
    ] = None
    revision: Annotated[int, Field(ge=1, strict=True)] = 1
    connection_ref: str
    embedding_connection_ref: str | None = None
    extraction_model: Annotated[str, Field(min_length=1, max_length=200)]
    supports_images: StrictBool = Field(default=False, exclude_if=lambda v: not v)
    image_input_tokens: Annotated[int, Field(ge=256, le=32768, strict=True)] = Field(
        default=4096, exclude_if=lambda v: v == 4096
    )
    extraction_revision: Annotated[str, Field(min_length=1, max_length=200)] = "1"
    extraction_format: Literal["full", "source_refs_v1", "source_names_v1"] = "full"
    extraction_semantics: Literal["legacy", "source_roles_v1"] = "legacy"
    # Omit historical defaults from serialization so existing cache identities stay stable.
    extraction_response_format: Literal["json_schema", "json_object"] = Field(
        default="json_schema", exclude_if=lambda value: value == "json_schema"
    )
    extraction_enable_thinking: StrictBool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    embedding_model: Annotated[str, Field(min_length=1, max_length=200)]
    embedding_dimensions: Annotated[int, Field(ge=1, le=4096, strict=True)]
    embedding_revision: Annotated[str, Field(min_length=1, max_length=200)] = "1"
    allowed_namespaces: tuple[Namespace, ...]
    data_policy_approved: bool
    allow_restricted: bool = False
    max_completion_tokens: Annotated[int, Field(ge=128, le=16384, strict=True)] = 4096
    schema_string_limit: Annotated[int, Field(ge=64, le=1000, strict=True)] | None = None
    timeout_seconds: Annotated[int, Field(ge=1, le=120, strict=True)] = 60
    extraction_context_tokens: Annotated[int, Field(ge=1024, le=131072, strict=True)] = 16384
    embedding_context_tokens: Annotated[int, Field(ge=256, le=32768, strict=True)] = 8192
    schema_in_context: bool = True
    embedding_batch_size: Annotated[int, Field(ge=1, le=16, strict=True)] = 16
    embedding_query_prefix: Annotated[str, Field(max_length=1000)] = ""
    embedding_send_dimensions: bool = Field(default=False, exclude_if=lambda value: not value)


class ProviderConfig(FrozenModel):
    budgets: BudgetPolicy = BudgetPolicy()
    connections: tuple[ProviderConnection, ...] = ()
    profiles: tuple[ProviderProfile, ...] = ()
    default_profile_id: UUID | None = Field(default=None, exclude_if=lambda value: value is None)


def embedding_space_id(profile: ProviderProfile, connection: ProviderConnection) -> str:
    """Stable compatibility identity; credential and extraction changes do not rotate it."""
    identity = {
        "endpoint": connection.endpoint,
        "model": profile.embedding_model,
        "dimensions": profile.embedding_dimensions,
        "weights_revision": profile.embedding_revision,
        "input_window": profile.embedding_context_tokens,
        "query_prefix": profile.embedding_query_prefix,
        "window_pooling": "utf8-bound-weighted-mean-v1",
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


class ProviderRegistry:
    def __init__(self, config: ProviderConfig | None = None) -> None:
        config = config or ProviderConfig()
        self.config_path: Path | None = None
        self._signature: tuple[int, int, int, int] | None = None
        self.budgets = config.budgets
        self.default_profile_id = config.default_profile_id
        self.profiles = {profile.id: profile for profile in config.profiles}
        self.connections = {connection.ref: connection for connection in config.connections}
        if len(self.profiles) != len(config.profiles) or len(self.connections) != len(
            config.connections
        ):
            raise ValueError("duplicate approved provider configuration")
        if any(profile.connection_ref not in self.connections for profile in config.profiles):
            raise ValueError("provider connection reference is not approved")
        if any(
            profile.embedding_connection_ref is not None
            and profile.embedding_connection_ref not in self.connections
            for profile in config.profiles
        ):
            raise ValueError("embedding connection reference is not approved")
        if self.default_profile_id is not None and self.default_profile_id not in self.profiles:
            raise ValueError("default provider profile is not configured")

    @classmethod
    def from_file(cls, path: Path | None) -> "ProviderRegistry":
        if path is None:
            return cls()
        # This file contains private credentials and is deliberately never a tool argument.
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise ValueError("provider configuration must be an owner-only regular file")
        registry = cls(ProviderConfig.model_validate(json.loads(path.read_bytes())))
        registry.config_path = path
        return registry

    def refresh(self) -> None:
        """API and workers observe atomically replaced private configuration files."""
        if self.config_path is None:
            return
        try:
            info = self.config_path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                raise ValueError("unsafe provider file")
            signature = (info.st_ino, info.st_mtime_ns, info.st_size, info.st_mode)
            if signature == self._signature:
                return
            updated = self.from_file(self.config_path)
            self.profiles, self.connections = updated.profiles, updated.connections
            self.budgets = updated.budgets
            self.default_profile_id = updated.default_profile_id
            self._signature = signature
        except (OSError, ValueError):
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "The private model configuration could not be loaded. "
                "Ask a platform operator to check it.",
            ) from None

    def resolve(
        self, profile_id: UUID, namespace: Namespace, *, restricted: bool
    ) -> tuple[ProviderProfile, ProviderConnection]:
        self.refresh()
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise DomainError(ErrorCode.PROVIDER_UNAVAILABLE, "An approved provider is required.")
        if (
            not profile.data_policy_approved
            or namespace not in profile.allowed_namespaces
            or (restricted and not profile.allow_restricted)
        ):
            raise inaccessible_reference()
        return profile, self.connections[profile.connection_ref]

    def resolve_embedding(
        self, profile_id: UUID, namespace: Namespace, *, restricted: bool
    ) -> tuple[ProviderProfile, ProviderConnection]:
        profile, _ = self.resolve(profile_id, namespace, restricted=restricted)
        connection = self.connections[profile.embedding_connection_ref or profile.connection_ref]
        if connection.api_format != "openai":
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE,
                "Claude needs a separate embedding API. Choose Alibaba, OpenAI "
                "or a local embedding model.",
            )
        return profile, connection
