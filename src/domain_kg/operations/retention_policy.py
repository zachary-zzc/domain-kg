"""Physical erasure adds a configurable grace after logical expiry; it grants no access."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Self

from pydantic import Field, TypeAdapter

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import FrozenModel, UTCDateTime
from domain_kg.operations.package_erasure import ErasureLimits


class RetentionPolicy(FrozenModel):
    grace_seconds: Annotated[int, Field(ge=3600, le=365 * 86400, strict=True)] = 86400
    max_targets: Annotated[int, Field(ge=1, le=100, strict=True)] = 10
    max_objects_per_target: Annotated[int, Field(ge=1, le=100000, strict=True)] = 100000
    max_verify_bytes: Annotated[int, Field(ge=1, le=2 * 1024**3, strict=True)] = 2 * 1024**3
    erasure: ErasureLimits = ErasureLimits()

    @classmethod
    def from_file(cls, path: Path | None) -> Self:
        if path is None:
            return cls()
        try:
            with path.open("rb") as stream:
                content = stream.read(65537)
            if len(content) > 65536:
                raise ValueError("configuration exceeds its bound")
            return cls.model_validate_json(content)
        except (OSError, ValueError):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "Retention configuration is invalid."
            ) from None

    def cutoff(self, at: datetime) -> datetime:
        return TypeAdapter(UTCDateTime).validate_python(at) - timedelta(seconds=self.grace_seconds)

    def eligible(self, expires_at: datetime, *, at: datetime) -> bool:
        return TypeAdapter(UTCDateTime).validate_python(expires_at) <= self.cutoff(at)
