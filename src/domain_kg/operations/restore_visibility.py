"""Typed suppression history and current masks for a fresh restore namespace."""

from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.visibility_models import CurationReceipt, IdentityFenceGroup, NodeFence
from domain_kg.identity.models import Principal
from domain_kg.models import ManualProvenance, ScopedRef, SourceVersion
from domain_kg.operations.restore_documents import RestoreDocuments
from domain_kg.revisions.projections import digest


class RestoreVisibility:
    def __init__(self, documents: RestoreDocuments) -> None:
        self.mapping = documents.mapping

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Restored visibility history differs."
        )

    def _namespace(self, row: dict[str, Any]) -> None:
        if (row.get("org_id"), row.get("kg_id")) != (
            str(self.mapping.origin.org_id),
            str(self.mapping.origin.kg_id),
        ):
            raise self.invalid()

    def _entity(self, identifier: str | UUID) -> UUID:
        return self.mapping.entity_ref(
            ScopedRef(**self.mapping.origin.model_dump(), id=UUID(str(identifier)))
        ).id

    def groups(self, values: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for value in values:
            group = IdentityFenceGroup.model_validate(value)
            if group.material_id is not None:
                self.mapping._materials((group.material_id,))
            result.append(
                group.model_dump(mode="json")
                | {"member_ids": [str(self._entity(member)) for member in group.member_ids]}
            )
        return result

    def revision(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            self._namespace(row)
            actor = Principal.model_validate(row["actor"])
            ManualProvenance.model_validate(row["provenance"])
            if (
                row["kind"] != "visibility"
                or row["state"] != "accepted"
                or row["job_id"] is not None
                or actor.kind != "human"
                or type(row["before"]["suppressed"]) is not bool
                or type(row["after"]["suppressed"]) is not bool
            ):
                raise self.invalid()
            materials = tuple(UUID(value) for value in row["material_ids"])
            self.mapping._materials(materials)
            scope = digest({"public": row["public"], "material_ids": sorted(row["material_ids"])})
            entity = self._entity(row["target_id"])
            curation = self.mapping.curation_ref(
                ScopedRef(**self.mapping.origin.model_dump(), id=row["id"])
            )
            if (
                row["scope_key"] != scope
                or row["target_state_id"]
                != str(uuid5(UUID(row["target_id"]), f"curation-scope/{scope}"))
                or set(row["source_versions"]) != set(row["material_ids"])
            ):
                raise self.invalid()
            for material, version in row["source_versions"].items():
                self.mapping.source(
                    SourceVersion(
                        **self.mapping.origin.model_dump(), material_id=material, version_id=version
                    )
                )
            receipt = CurationReceipt.model_validate(row["receipt"])
            expected = CurationReceipt(
                **self.mapping.origin.model_dump(),
                revision_id=row["id"],
                target_id=row["target_id"],
                action="suppress" if row["after"]["suppressed"] else "restore",
                target_revision=row["expected_revision"] + 1,
                visibility_revision=row["visibility_revision"],
            )
            if receipt != expected:
                raise self.invalid()
            result = (
                dict(row)
                | self.mapping.target.model_dump(mode="json")
                | {
                    "target_id": str(entity),
                    "target_state_id": str(uuid5(entity, f"curation-scope/{scope}")),
                    "identity_groups": self.groups(row["identity_groups"]),
                    "receipt": receipt.model_copy(
                        update={"kg_id": self.mapping.target.kg_id, "target_id": entity}
                    ).model_dump(mode="json"),
                }
            )
            if row.get("rollback_of"):
                self.mapping.curation_ref(
                    ScopedRef(**self.mapping.origin.model_dump(), id=row["rollback_of"])
                )
            NodeFence(
                **self.mapping.target.model_dump(),
                entity_id=entity,
                curation=curation,
                revision=receipt.target_revision,
                public=row["public"],
                material_ids=materials,
                identity_groups=tuple(
                    IdentityFenceGroup.model_validate(g) for g in result["identity_groups"]
                ),
            )
            return result
        except (ValidationError, KeyError, TypeError, ValueError):
            raise self.invalid() from None

    def fence(self, row: dict[str, Any], revision: dict[str, Any]) -> dict[str, Any]:
        try:
            self._namespace(row)
            original = NodeFence.model_validate({key: row[key] for key in NodeFence.model_fields})
            curation = self.mapping.curation_ref(original.curation)
            if (
                str(curation.id) != revision["id"]
                or row["id"] != str(uuid5(original.entity_id, f"curation-scope/{row['scope_key']}"))
                or str(self._entity(original.entity_id)) != revision["target_id"]
                or any(row[key] != revision[key] for key in ("scope_key", "public", "material_ids"))
                or row["revision"] != revision["expected_revision"] + 1
                or row["suppressed"] != revision["after"]["suppressed"]
                or self.groups(row["identity_groups"]) != revision["identity_groups"]
            ):
                raise self.invalid()
            return self.mapping.target.model_dump(mode="json") | {
                "id": revision["target_state_id"],
                "entity_id": revision["target_id"],
                "curation": curation.model_dump(mode="json"),
                "scope_key": revision["scope_key"],
                "revision": row["revision"],
                "public": row["public"],
                "material_ids": row["material_ids"],
                "suppressed": row["suppressed"],
                "identity_groups": revision["identity_groups"],
            }
        except (ValidationError, KeyError, TypeError, ValueError):
            raise self.invalid() from None
