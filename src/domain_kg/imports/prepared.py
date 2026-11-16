"""Import preparation is explicit and never pretends a provider extracted its claims."""

from typing import Annotated, Self

from pydantic import Field, model_validator

from domain_kg.graph.models import DomainSchema
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.jobs.models import Checkpoint
from domain_kg.models import SourceVersion
from domain_kg.revisions.projections import ProjectionBody


class PreparedImport(SourceVersion):
    import_origin: ImportOrigin
    domain_schema: DomainSchema
    chunk_checkpoint: Checkpoint
    rows: Annotated[tuple[ProjectionBody, ...], Field(max_length=10000)]
    warnings: tuple[str, ...] = ()

    @model_validator(mode="after")
    def unverified_source_rows(self) -> Self:
        source = SourceVersion(
            **{field: getattr(self, field) for field in SourceVersion.model_fields}
        )
        if not self.import_origin.matches(source):
            raise ValueError("prepared import provenance must bind its source")
        for reference in (self.domain_schema, self.chunk_checkpoint):
            if (reference.org_id, reference.kg_id) != (self.org_id, self.kg_id):
                raise ValueError("prepared import dependencies must share its namespace")
        if self.chunk_checkpoint.stage != "chunking":
            raise ValueError("prepared import must retain its real parsed snapshot")
        if len({(row.kind, row.id) for row in self.rows}) != len(self.rows):
            raise ValueError("prepared import rows must be unique")
        for row in self.rows:
            if row.source != source or row.curation is not None or row.provenance is not None:
                raise ValueError("prepared import row ownership differs")
            if row.assertion and (
                row.assertion.support.status != "imported_unverified"
                or row.assertion.support.spans
                or row.assertion.support.curation is not None
                or row.assertion.support.provenance is not None
            ):
                raise ValueError("legacy claims cannot acquire verified or human provenance")
            for observation in (row.observation, row.subject_observation, row.target_observation):
                if observation and (observation.spans or observation.description):
                    raise ValueError(
                        "legacy observations cannot invent quotations or source descriptions"
                    )
            if row.observation and not self.domain_schema.allows_entity_type(
                row.observation.entity_type
            ):
                raise ValueError("imported entity type differs from the selected schema")
            if row.assertion and not self.domain_schema.allows_predicate(
                row.assertion.assertion.predicate
            ):
                raise ValueError("imported relation differs from the selected schema")
        return self
