"""Versioned retained package pages have exact namespace, operation and attempt ownership."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.materials.models import BlobRef
from domain_kg.models import SHA256, FrozenModel, Namespace, NonNegative, ScopedRef, SourceVersion


class PackageObject(ScopedRef):
    backup_id: UUID
    attempt_id: UUID
    sha256: SHA256
    size_bytes: Annotated[int, Field(ge=1, le=50 * 1024 * 1024, strict=True)]
    media_type: Annotated[str, Field(min_length=1, max_length=200)] = "application/json"


class PackagePart(FrozenModel):
    object: PackageObject
    kind: Literal["authority", "original", "source_projection", "curation_projection"]
    source: SourceVersion | None = None
    curation: ScopedRef | None = None
    original: BlobRef | None = None

    @model_validator(mode="after")
    def exact_owner(self) -> Self:
        namespace = (self.object.org_id, self.object.kg_id)
        if any(
            (ref.org_id, ref.kg_id) != namespace
            for ref in (self.source, self.curation, self.original)
            if ref is not None
        ):
            raise ValueError("package references must share their exact namespace")
        if self.kind == "authority":
            if self.source or self.curation or self.original:
                raise ValueError("authority pages have no fabricated source owner")
        elif self.kind == "curation_projection":
            if self.curation is None or self.source or self.original:
                raise ValueError("curation projection requires its exact curation owner")
        elif self.source is None or self.curation:
            raise ValueError("source data requires its exact source owner")
        if self.kind == "original":
            if (
                self.original is None
                or any(
                    getattr(self.original, field) != getattr(self.source, field)
                    for field in SourceVersion.model_fields
                )
                or (self.original.sha256, self.original.size_bytes, self.original.media_type)
                != (self.object.sha256, self.object.size_bytes, self.object.media_type)
            ):
                raise ValueError("retained originals must preserve exact ownership and bytes")
        elif self.original is not None:
            raise ValueError("only original parts carry an original file reference")
        return self


class PackagePage(Namespace):
    backup_id: UUID
    attempt_id: UUID
    index: NonNegative
    parts: Annotated[tuple[PackagePart, ...], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def one_attempt(self) -> Self:
        identifiers = [part.object.id for part in self.parts]
        if len(set(identifiers)) != len(identifiers) or any(
            (part.object.org_id, part.object.kg_id, part.object.backup_id, part.object.attempt_id)
            != (self.org_id, self.kg_id, self.backup_id, self.attempt_id)
            for part in self.parts
        ):
            raise ValueError("package pages require distinct objects from the same attempt")
        return self
