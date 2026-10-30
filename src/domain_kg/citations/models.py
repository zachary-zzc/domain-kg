"""Authorized citations contain true source spans or explicit human provenance."""

from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from domain_kg.imports.provenance import ImportOrigin
from domain_kg.ingestion.models import Span, span_status
from domain_kg.materials.inspection_models import ImageSourceView
from domain_kg.models import FrozenModel, ManualProvenance


class Citation(FrozenModel):
    id: UUID
    kg_id: UUID
    kind: Literal["source", "manual"]
    text: str
    resolver_url: str
    material_id: UUID | None = None
    version_id: UUID | None = None
    chunk_id: UUID | None = None
    spans: tuple[Span, ...] = ()
    original_url: str | None = None
    images: tuple[ImageSourceView, ...] = Field(default=(), exclude_if=lambda v: not v)
    curation_revision_id: UUID | None = None
    provenance: ManualProvenance | None = None
    import_origin: ImportOrigin | None = None
    support_status: (
        Literal["verified", "model_interpreted", "manual", "imported_unverified"] | None
    ) = None

    @model_validator(mode="after")
    def honest_import_citation(self) -> Self:
        expected = (
            "imported_unverified"
            if self.import_origin
            else ("manual" if self.kind == "manual" else span_status(self.spans))
        )
        if self.support_status is None:
            object.__setattr__(self, "support_status", expected)
        elif self.support_status != expected:
            raise ValueError("citation status differs from its provenance")
        if self.import_origin is not None and (
            self.kind != "source"
            or self.spans
            or self.provenance is not None
            or (self.kg_id, self.material_id, self.version_id)
            != (
                self.import_origin.kg_id,
                self.import_origin.material_id,
                self.import_origin.version_id,
            )
        ):
            raise ValueError("import citations cannot invent original document provenance")
        return self
