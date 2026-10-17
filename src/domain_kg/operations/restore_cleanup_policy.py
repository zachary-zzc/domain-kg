"""Bounded private cleanup configuration; each cleanup targets one recorded restore."""

from pathlib import Path
from typing import Annotated, Self

from pydantic import Field

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import FrozenModel
from domain_kg.operations.package_erasure import ErasureLimits


class RestoreCleanupPolicy(FrozenModel):
    max_write_records: Annotated[int, Field(ge=1, le=100000, strict=True)] = 10000
    max_objects: Annotated[int, Field(ge=1, le=10000, strict=True)] = 1000
    max_graph_nodes: Annotated[int, Field(ge=1, le=100000, strict=True)] = 10000
    max_vector_points: Annotated[int, Field(ge=1, le=100000, strict=True)] = 10000
    max_rows_per_collection: Annotated[int, Field(ge=1, le=100000, strict=True)] = 10000
    batch_size: Annotated[int, Field(ge=1, le=1000, strict=True)] = 100
    erasure: ErasureLimits = ErasureLimits()

    @classmethod
    def from_file(cls, path: Path | None) -> Self:
        if path is None:
            return cls()
        try:
            with path.open("rb") as stream:
                body = stream.read(65537)
            if len(body) > 65536:
                raise ValueError("configuration exceeds its bound")
            return cls.model_validate_json(body)
        except (OSError, ValueError):
            raise DomainError(
                ErrorCode.VALIDATION_ERROR, "Restore cleanup configuration is invalid."
            ) from None
