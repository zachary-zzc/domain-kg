"""Transport-independent immutable primitives shared by domain models."""

from datetime import UTC
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

NonNegative = Annotated[int, Field(ge=0, strict=True)]
IdempotencyKey = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
SHA256 = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
UTCDateTime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
PreservedText = Annotated[str, StringConstraints(strip_whitespace=False)]


class FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class Namespace(FrozenModel):
    org_id: UUID
    kg_id: UUID


class ScopedRef(Namespace):
    id: UUID


class SourceVersion(Namespace):
    material_id: UUID
    version_id: UUID


class ManualProvenance(FrozenModel):
    actor: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1, max_length=4000)]


class PageRequest(FrozenModel):
    limit: Annotated[int, Field(ge=1, le=100, strict=True)] = 25
    cursor: str | None = None
