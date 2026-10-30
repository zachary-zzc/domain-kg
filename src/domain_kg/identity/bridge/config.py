"""Only operators can install trusted issuer/client references and exchange secrets."""

import base64
import json
import os
import stat
from pathlib import Path
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, SecretStr, field_validator, model_validator

from domain_kg.identity.oidc import _safe_endpoint
from domain_kg.models import FrozenModel


class BridgeProfile(FrozenModel):
    id: UUID
    name: Annotated[str, Field(min_length=1, max_length=200)]
    allowed_org_ids: frozenset[UUID]
    caller_issuer: Annotated[str, Field(min_length=1, max_length=2048)]
    caller_audience: Annotated[str, Field(min_length=1, max_length=2048)]
    caller_client_id: Annotated[str, Field(min_length=1, max_length=512)]
    caller_subject: Annotated[str, Field(min_length=1, max_length=512)]
    subject_issuer: Annotated[str, Field(min_length=1, max_length=2048)]
    subject_audience: Annotated[str, Field(min_length=1, max_length=2048)]
    subject_client_id: Annotated[str, Field(min_length=1, max_length=512)]
    tenant_claim: Literal["tid", "tenant_id"] | None = None
    tenant_id: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    exchange_client_id: Annotated[str, Field(min_length=1, max_length=512)]
    exchange_client_secret: SecretStr = Field(min_length=1, max_length=4096, repr=False)
    exchange_subject_token_type: Literal[
        "urn:ietf:params:oauth:token-type:jwt",
        "urn:ietf:params:oauth:token-type:access_token",
    ] = "urn:ietf:params:oauth:token-type:jwt"
    exchange_parameter: Literal["resource", "audience"] = "resource"
    exchange_audience: Annotated[str, Field(min_length=1, max_length=2048)] | None = None
    subject_issuer_alias: Annotated[str, Field(min_length=1, max_length=512)] | None = None

    @field_validator(
        "caller_audience",
        "caller_client_id",
        "caller_subject",
        "subject_audience",
        "subject_client_id",
        "tenant_id",
        "exchange_client_id",
        "exchange_audience",
        "subject_issuer_alias",
    )
    @classmethod
    def non_blank_identifier(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("identity identifiers cannot be blank")
        return value

    @field_validator("caller_issuer", "subject_issuer")
    @classmethod
    def safe_issuer(cls, value: str) -> str:
        return _safe_endpoint(value)

    @model_validator(mode="after")
    def fixed_tenant_and_audience(self) -> Self:
        if (self.tenant_id is None) != (self.tenant_claim is None):
            raise ValueError("tenant claim and ID must be configured together")
        if (self.exchange_parameter == "audience") != bool(self.exchange_audience):
            raise ValueError("audience exchange requires a fixed audience")
        return self


class BridgeConfig(FrozenModel):
    response_key: SecretStr = Field(repr=False)
    profiles: Annotated[tuple[BridgeProfile, ...], Field(max_length=100)]

    @field_validator("response_key")
    @classmethod
    def encryption_key(cls, value: SecretStr) -> SecretStr:
        if len(base64.b64decode(value.get_secret_value(), altchars=b"-_", validate=True)) != 32:
            raise ValueError("response key must encode exactly 32 bytes")
        return value

    @model_validator(mode="after")
    def unique_profiles(self) -> Self:
        for values in (
            [p.id for p in self.profiles],
            [p.exchange_client_id for p in self.profiles],
        ):
            if len(set(values)) != len(values):
                raise ValueError("profile/client IDs must be unique")
        return self


def read_config(path: Path | None) -> BridgeConfig | None:
    if path is None:
        return None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > 1024 * 1024:
                raise ValueError("private regular configuration file required")
            return BridgeConfig.model_validate(json.loads(stream.read(1024 * 1024 + 1)))
    except (OSError, ValueError):
        raise ValueError("Invalid private identity bridge configuration.") from None
