"""Transform canonical authority records with explicit typed reference handling."""

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import AssertionRecord, EntityRecord, SupportRecord
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.operations.remapping import RestoreRemapper
from domain_kg.storage.integrity import RECORD_TYPES, CanonicalRecord
from domain_kg.storage.models import CurationRecord, MaterialRecord, MaterialVersionRecord


class RestoreCanonical:
    def __init__(self, mapping: RestoreRemapper) -> None:
        self.mapping = mapping

    @staticmethod
    def inconsistent() -> DomainError:
        return DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Restore canonical references differ."
        )

    def records(
        self, tables: Mapping[str, Sequence[dict[str, Any]]]
    ) -> tuple[CanonicalRecord, ...]:
        order = (
            "materials",
            "material_versions",
            "entities",
            "curations",
            "assertions",
            "supports",
        )
        if sum(len(tables.get(table, ())) for table in order) > 100000:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "Restore canonical record budget exceeded.")
        result = []
        seen = set()
        try:
            for table in order:
                for row in tables.get(table, ()):
                    record = RECORD_TYPES[table].model_validate(row)
                    if (record.org_id, record.kg_id) != (
                        self.mapping.origin.org_id,
                        self.mapping.origin.kg_id,
                    ):
                        raise self.inconsistent()
                    transformed = self._record(record)
                    identity = (type(transformed), transformed.id)
                    if identity in seen:
                        raise self.inconsistent()
                    seen.add(identity)
                    result.append(transformed)
        except ValidationError:
            raise self.inconsistent() from None
        return tuple(result)

    def _record(self, record: CanonicalRecord) -> CanonicalRecord:
        if isinstance(record, MaterialRecord):
            self.mapping._materials((record.id,))
            return MaterialRecord.model_validate(
                record.model_dump() | self.mapping.target.model_dump()
            )
        if isinstance(record, MaterialVersionRecord):
            if (record.material.org_id, record.material.kg_id) != (record.org_id, record.kg_id):
                raise self.inconsistent()
            target = self.mapping.source(
                SourceVersion(
                    org_id=record.org_id,
                    kg_id=record.kg_id,
                    material_id=record.material.id,
                    version_id=record.id,
                )
            )
            return MaterialVersionRecord(
                **self.mapping.target.model_dump(),
                id=target.version_id,
                material=ScopedRef(**self.mapping.target.model_dump(), id=target.material_id),
            )
        if isinstance(record, EntityRecord):
            return EntityRecord(**self.mapping.entity_ref(record).model_dump())
        if isinstance(record, CurationRecord):
            return CurationRecord(
                **self.mapping.curation_ref(record).model_dump(),
                actor=record.actor,
                reason=record.reason,
            )
        if isinstance(record, AssertionRecord):
            return self.mapping.assertion(record)
        if isinstance(record, SupportRecord):
            return self.mapping.support(record)
        raise self.inconsistent()
