"""Map immutable merge/split history and every independently fenced node counter."""

from typing import Any
from uuid import UUID, uuid5

from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.graph.identity_models import IdentityState
from domain_kg.graph.identity_records import identity_directive
from domain_kg.graph.models import DomainSchema
from domain_kg.identity.models import Principal
from domain_kg.models import ScopedRef, SourceVersion
from domain_kg.operations.restore_documents import RestoreDocuments
from domain_kg.revisions.projections import digest


class RestoreIdentity:
    def __init__(self, documents: RestoreDocuments) -> None:
        self.documents, self.mapping = documents, documents.mapping

    @staticmethod
    def invalid() -> DomainError:
        return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restored identity history differs.")

    def entity(self, value: str | UUID) -> UUID:
        return self.mapping.entity_ref(
            ScopedRef(**self.mapping.origin.model_dump(), id=UUID(str(value)))
        ).id

    def state(self, value: dict[str, Any]) -> dict[str, Any]:
        state = IdentityState.model_validate(value)
        return {
            "groups": [
                {
                    "canonical_id": str(self.entity(group.canonical_id)),
                    "member_ids": [str(self.entity(member)) for member in group.member_ids],
                }
                for group in state.groups
            ]
        }

    def revision(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            if (
                (row["org_id"], row["kg_id"])
                != (str(self.mapping.origin.org_id), str(self.mapping.origin.kg_id))
                or row["kind"] != "identity"
                or row["state"] != "accepted"
            ):
                raise self.invalid()
            Principal.model_validate(row["actor"])
            manual = identity_directive(row)
            scope = digest({"public": row["public"], "material_ids": sorted(row["material_ids"])})
            members = tuple(UUID(value) for value in row["members"])
            targets = row["target_revisions"]
            if (
                scope != row["scope_key"]
                or not 1 <= len(members) <= 100
                or len(set(members)) != len(members)
                or len(targets) != len(members)
                or {target["target_id"] for target in targets} != set(row["members"])
                or type(row["identity_sequence"]) is not int
                or row["identity_sequence"] < 1
                or type(row["expected_revision"]) is not int
                or row["expected_revision"] < 0
                or set(row["source_versions"]) != set(row["material_ids"])
            ):
                raise self.invalid()
            self.mapping._materials(tuple(UUID(value) for value in row["material_ids"]))
            for material, version in row["source_versions"].items():
                self.mapping.source(
                    SourceVersion(
                        **self.mapping.origin.model_dump(), material_id=material, version_id=version
                    )
                )
            counters = []
            for target in targets:
                if target["state_id"] != str(
                    uuid5(UUID(target["target_id"]), f"curation-scope/{scope}")
                ) or (
                    type(target["expected_revision"]) is not int or target["expected_revision"] < 0
                ):
                    raise self.invalid()
                entity = self.entity(target["target_id"])
                counters.append(
                    {
                        "target_id": str(entity),
                        "state_id": str(uuid5(entity, f"curation-scope/{scope}")),
                        "expected_revision": target["expected_revision"],
                    }
                )
            primary = next(
                (target for target in targets if target["target_id"] == row["target_id"]), None
            )
            if primary is None or (primary["state_id"], primary["expected_revision"]) != (
                row["target_state_id"],
                row["expected_revision"],
            ):
                raise self.invalid()
            if manual.retracts is None and (
                len(members) < 2
                or manual.members != frozenset(members)
                or IdentityState.model_validate(row["before"]).members != manual.members
                or row["operation"] == "merge"
                and (len(manual.groups) != 1 or manual.groups[0].canonical_id != manual.target.id)
                or row["operation"] == "split"
                and len(manual.groups) < 2
            ):
                raise self.invalid()
            expected = self.mapping.manual_identity(manual)
            result = (
                dict(row)
                | self.mapping.target.model_dump(mode="json")
                | {
                    "target_id": str(expected.target.id),
                    "target_state_id": str(uuid5(expected.target.id, f"curation-scope/{scope}")),
                    "members": [str(self.entity(member)) for member in members],
                    "target_revisions": counters,
                    "domain_schema": self.documents.schema(
                        DomainSchema.model_validate(row["domain_schema"])
                    ).model_dump(mode="json"),
                }
            )
            if manual.retracts is not None:
                if type(row["before"].get("enabled")) is not bool:
                    raise self.invalid()
                result["retracts"] = (
                    expected.retracts.model_dump(mode="json") if expected.retracts else None
                )
            else:
                result.update(before=self.state(row["before"]), after=self.state(row["after"]))
            if row.get("rollback_of"):
                self.mapping.curation_ref(
                    ScopedRef(**self.mapping.origin.model_dump(), id=row["rollback_of"])
                )
            if identity_directive(result) != expected:
                raise self.invalid()
            return result
        except (ValidationError, KeyError, TypeError, ValueError):
            raise self.invalid() from None
