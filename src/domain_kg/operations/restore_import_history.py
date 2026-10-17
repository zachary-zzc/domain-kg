"""Reseal admitted legacy history and source-qualified identity mappings."""

import hashlib
import json
from typing import Any, Literal

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.models import DomainSchema
from domain_kg.imports.admission import record_matches_plan
from domain_kg.imports.build import owned_id
from domain_kg.imports.models import ImportPlan
from domain_kg.materials.models import BlobRef
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.operations.restore_documents import RestoreDocuments
from domain_kg.operations.restore_imports import RestoredImport


class RestoreImportHistory:
    def __init__(self, documents: RestoreDocuments) -> None:
        self.documents, self.mapping = documents, documents.mapping

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restored import history differs.")

    def bundle(self, source: SourceVersion) -> RestoredImport:
        self.mapping.source(source)
        result = self.documents.import_sources.get(source)
        if result is None:
            raise self.invalid()
        return result

    def plan(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            source = SourceVersion.model_validate(row["source"])
            bundle = self.bundle(source)
            plan = ImportPlan.model_validate(row["plan"])
            if (
                (row["org_id"], row["kg_id"]) != (str(source.org_id), str(source.kg_id))
                or row["state"] != "admitted"
                or row["id"] != str(plan.id)
                or not record_matches_plan(row, plan)
                or row["plan_hash"] != bundle.original.plan_hash
                or row["graph_hash"] != bundle.original.graph_hash
                or plan.id != bundle.original.plan_id
            ):
                raise self.invalid()
            schema = DomainSchema.model_validate(row["binding"]["schema"])
            if (schema.id, schema.revision) != (plan.schema_id, plan.schema_revision):
                raise self.invalid()
            blob = BlobRef.model_validate(row["binding"]["blob"])
            if self.documents.owner(blob) != source:
                raise self.invalid()
            intent = hashlib.sha256(
                json.dumps(bundle.plan.source.model_dump(mode="json"), sort_keys=True).encode()
            ).hexdigest()
            return (
                dict(row)
                | self.mapping.target.model_dump(mode="json")
                | {
                    "plan": bundle.plan.model_dump(mode="json"),
                    "graph": bundle.graph.model_dump(mode="json"),
                    "plan_hash": bundle.origin.plan_hash,
                    "graph_hash": bundle.origin.graph_hash,
                    "source": self.mapping.source(source).model_dump(mode="json"),
                    "intent": intent,
                    "binding": dict(row["binding"])
                    | {
                        "schema": self.documents.schema(schema).model_dump(mode="json"),
                        "blob": self.documents.original(blob).model_dump(mode="json"),
                    },
                }
            )
        except (ValidationError, KeyError, TypeError, ValueError):
            raise self.invalid() from None

    def mapping_row(
        self, kind: Literal["observation", "support"], row: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            source = SourceVersion.model_validate(
                {key: row[key] for key in SourceVersion.model_fields}
            )
            bundle = self.bundle(source)
            old_id = owned_id(bundle.original, kind, row["legacy_source_id"], row["legacy_id"])
            new_id = owned_id(bundle.origin, kind, row["legacy_source_id"], row["legacy_id"])
            if (
                row["id"] != str(old_id)
                or row["plan_id"] != str(bundle.original.plan_id)
                or row["origin"] != bundle.original.origin
            ):
                raise self.invalid()
            rows = bundle.graph.entities if kind == "observation" else bundle.graph.relations
            item = next(
                (
                    value
                    for value in rows
                    if (value.source_id, value.legacy_id)
                    == (row["legacy_source_id"], row["legacy_id"])
                ),
                None,
            )
            if item is None:
                raise self.invalid()
            key = "entity" if kind == "observation" else "assertion"
            reference = ScopedRef.model_validate(row[key])
            mapped = (
                self.mapping.entity_ref(reference)
                if kind == "observation"
                else self.mapping.assertion_ref(reference)
            )
            expected = getattr(item, "entity_id" if kind == "observation" else "assertion_id")
            if mapped.id != expected:
                raise self.invalid()
            result = self.mapping.source(source).model_dump(mode="json") | {
                "id": str(new_id),
                "plan_id": str(bundle.plan.id),
                "origin": bundle.origin.origin,
                "legacy_source_id": row["legacy_source_id"],
                "legacy_id": row["legacy_id"],
                key: mapped.model_dump(mode="json"),
            }
            if kind == "support":
                if ScopedRef.model_validate(row["support"]) != ScopedRef(
                    **self.mapping.origin.model_dump(), id=old_id
                ):
                    raise self.invalid()
                result["support"] = ScopedRef(
                    **self.mapping.target.model_dump(), id=new_id
                ).model_dump(mode="json")
            return result
        except (ValidationError, KeyError, TypeError, ValueError):
            raise self.invalid() from None
