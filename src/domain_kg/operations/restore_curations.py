"""Remap typed human node/relation history without replaying intent or authority."""

from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.curation_models import ManualNode, NodeFields
from domain_kg.graph.manual_relations import ManualRelation, RelationFields
from domain_kg.graph.models import AssertionRecord, DomainSchema
from domain_kg.identity.models import Principal
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.operations.restore_documents import RestoreDocuments
from domain_kg.revisions.curation_publication import directive
from domain_kg.revisions.models import RevisionReceipt
from domain_kg.revisions.projections import digest


class RestoreCurations:
    def __init__(self, documents: RestoreDocuments) -> None:
        self.documents, self.mapping = documents, documents.mapping

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Restored human history references differ."
        )

    def target(self, kind: str, identifier: UUID) -> ScopedRef:
        original = ScopedRef(**self.mapping.origin.model_dump(), id=identifier)
        return (
            self.mapping.entity_ref(original)
            if kind == "node"
            else self.mapping.assertion_ref(original)
        )

    def receipt(self, original: dict[str, Any], transformed: dict[str, Any]) -> dict[str, Any]:
        if "receipt" not in original:
            return transformed
        try:
            receipt = RevisionReceipt.model_validate(original["receipt"])
            if (receipt.org_id, receipt.kg_id, receipt.revision_id, receipt.state) != (
                self.mapping.origin.org_id,
                self.mapping.origin.kg_id,
                UUID(original["id"]),
                "published",
            ) or (
                "published_revision" in original
                and receipt.published_revision != original["published_revision"]
            ):
                raise self.invalid()
            return transformed | {
                "receipt": receipt.model_copy(update=self.mapping.target.model_dump()).model_dump(
                    mode="json"
                )
            }
        except (ValidationError, KeyError, TypeError, ValueError):
            raise self.invalid() from None

    def fields(self, kind: str, values: dict[str, Any]) -> dict[str, Any]:
        if kind == "node":
            return NodeFields.model_validate(values).model_dump(
                mode="json", exclude_unset=True, exclude_none=True
            )
        fields = RelationFields.model_validate(values)
        result = fields.model_dump(mode="json", exclude_unset=True, exclude_none=True)
        for key in ("subject_id", "target_id"):
            identifier = getattr(fields, key)
            if identifier is not None:
                result[key] = str(
                    self.mapping.entity_ref(
                        ScopedRef(**self.mapping.origin.model_dump(), id=identifier)
                    ).id
                )
        return result

    def revision(self, row: dict[str, Any]) -> dict[str, Any]:
        if (row.get("org_id"), row.get("kg_id")) != (
            str(self.mapping.origin.org_id),
            str(self.mapping.origin.kg_id),
        ):
            raise self.invalid()
        if row.get("kind") not in ("node", "relation"):
            raise DomainError(
                ErrorCode.UNSUPPORTED_FORMAT, "This human history kind needs its dedicated mapping."
            )
        try:
            kind = row["kind"]
            original = directive(row)
            Principal.model_validate(row["actor"])
            scope = {"public": row["public"], "material_ids": sorted(row["material_ids"])}
            if kind == "relation":
                scope["kind"] = "relation"
            if digest(scope) != row["scope_key"] or row["state"] != "accepted":
                raise self.invalid()
            target = self.target(kind, UUID(row["target_id"]))
            old_state = uuid5(UUID(row["target_id"]), f"curation-scope/{row['scope_key']}")
            if row["target_state_id"] != str(old_state):
                raise self.invalid()
            if not isinstance(row["source_versions"], dict):
                raise self.invalid()
            source_versions = {}
            for material, version in row["source_versions"].items():
                source = self.mapping.source(
                    SourceVersion(
                        **self.mapping.origin.model_dump(), material_id=material, version_id=version
                    )
                )
                source_versions[str(source.material_id)] = str(source.version_id)
            self.mapping._materials(tuple(UUID(value) for value in row["material_ids"]))
            result = (
                dict(row)
                | self.mapping.target.model_dump(mode="json")
                | {
                    "target_id": str(target.id),
                    "target_state_id": str(uuid5(target.id, f"curation-scope/{row['scope_key']}")),
                    "before": self.fields(kind, row["before"]),
                    "after": self.fields(kind, row["after"]),
                    "source_versions": source_versions,
                    "domain_schema": self.documents.schema(
                        DomainSchema.model_validate(row["domain_schema"])
                    ).model_dump(mode="json"),
                    "base_fact": self.mapping.assertion(
                        AssertionRecord.model_validate(row["base_fact"])
                    ).model_dump(mode="json")
                    if row["base_fact"]
                    else None,
                }
            )
            if row.get("rollback_of"):
                result["rollback_of"] = str(
                    self.mapping.curation_ref(
                        ScopedRef(**self.mapping.origin.model_dump(), id=row["rollback_of"])
                    ).id
                )
            if isinstance(original, ManualNode):
                expected: ManualNode | ManualRelation = self.mapping.manual_node(original)
            elif isinstance(original, ManualRelation):
                expected = self.mapping.manual_relation(original)
            else:
                raise self.invalid()
            if directive(result) != expected:
                raise self.invalid()
            return result
        except (ValidationError, KeyError, TypeError, ValueError):
            raise self.invalid() from None
