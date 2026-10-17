"""Physical erasure adds a configurable grace after logical expiry; it grants no access."""

from datetime import datetime, timedelta
from typing import Annotated

from pydantic import Field, TypeAdapter

from domain_kg.models import FrozenModel, UTCDateTime
from domain_kg.operations.package_erasure import ErasureLimits


class RetentionPolicy(FrozenModel):
    grace_seconds: Annotated[int, Field(ge=3600, le=365 * 86400, strict=True)] = 86400
    max_targets: Annotated[int, Field(ge=1, le=100, strict=True)] = 10
    erasure: ErasureLimits = ErasureLimits()

    def cutoff(self, at: datetime) -> datetime:
        return TypeAdapter(UTCDateTime).validate_python(at) - timedelta(seconds=self.grace_seconds)

    def eligible(self, expires_at: datetime, *, at: datetime) -> bool:
        return TypeAdapter(UTCDateTime).validate_python(expires_at) <= self.cutoff(at)
