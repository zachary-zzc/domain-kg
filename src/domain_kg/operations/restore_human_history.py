"""Stage accepted node/relation history and its current scoped revision indexes."""

from uuid import UUID

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.models import ScopedRef
from domain_kg.operations.restore_control import RestoreControl, Row, decoded
from domain_kg.operations.restore_curations import RestoreCurations
from domain_kg.operations.restore_identity import RestoreIdentity
from domain_kg.operations.restore_preparation import PreparedRestore
from domain_kg.operations.restore_visibility import RestoreVisibility


class RestoreHumanHistory(RestoreControl):
    async def rows(self, staged: PreparedRestore) -> tuple[Row, ...]:
        inventory = staged.inventory
        mapping = inventory.mapping(staged.binding.target.target)
        documents = await inventory.documents(mapping)
        service = RestoreCurations(documents)
        visibility = RestoreVisibility(documents)
        identity = RestoreIdentity(documents)
        revisions = {
            row["id"]: (
                visibility.revision(decoded(row))
                if row["kind"] == "visibility"
                else identity.revision(decoded(row))
                if row["kind"] == "identity"
                else service.revision(decoded(row))
            )
            for row in inventory.rows("curation_revisions")
        }
        original = {row["id"]: row for row in inventory.rows("curation_revisions")}
        revisions = {
            identifier: service.receipt(original[identifier], row)
            for identifier, row in revisions.items()
        }
        states = {row["target_state_id"]: revisions[row["id"]] for row in original.values()}
        for row in original.values():
            if row["kind"] == "identity":
                mapped = revisions[row["id"]]
                for old, new in zip(
                    row["target_revisions"], mapped["target_revisions"], strict=True
                ):
                    states[old["state_id"]] = mapped | {
                        "target_state_id": new["state_id"],
                        "target_id": new["target_id"],
                    }
        ns = mapping.target.model_dump(mode="json")
        result: list[Row] = [("control", "curation_revisions", row) for row in revisions.values()]
        for row in inventory.rows("curation_access"):
            revision = revisions.get(row["id"])
            if revision is None or any(
                row.get(key) != revision[key] for key in ("public", "material_ids")
            ):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Curation visibility history differs."
                )
            mapping.curation_ref(ScopedRef(**mapping.origin.model_dump(), id=row["id"]))
            if type(row.get("suppressed")) is not bool:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Curation visibility flag is invalid."
                )
            if (row.get("kind") == "identity") != (revision["kind"] == "identity"):
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Structural curation ownership differs."
                )
            result.append(
                (
                    "control",
                    "curation_access",
                    ns
                    | {
                        "id": row["id"],
                        "public": row["public"],
                        "material_ids": row["material_ids"],
                        "suppressed": row["suppressed"],
                    }
                    | ({"kind": "identity"} if revision["kind"] == "identity" else {}),
                )
            )
        for row in inventory.rows("curation_targets"):
            revision = states.get(row["id"])
            if revision is None or row["scope_key"] != revision["scope_key"]:
                raise DomainError(
                    ErrorCode.UNSUPPORTED_FORMAT,
                    "Unresolved curation state requires explicit history recovery.",
                )
            result.append(
                (
                    "control",
                    "curation_targets",
                    ns
                    | {
                        "id": revision["target_state_id"],
                        "kind": "node"
                        if revision["kind"] in ("visibility", "identity")
                        else revision["kind"],
                        "target_id": revision["target_id"],
                        "scope_key": revision["scope_key"],
                        "revision": row["revision"],
                    },
                )
            )
        for row in inventory.rows("curation_scopes"):
            revision = revisions.get(row["curation_id"])
            if revision is None or row["scope_key"] != revision["scope_key"]:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Curation scope history differs."
                )
            expected = service.target(
                revision["kind"], UUID(original[row["curation_id"]]["target_id"])
            )
            result.append(
                (
                    "control",
                    "curation_scopes",
                    ns
                    | {
                        "target_id": str(expected.id),
                        "scope_key": row["scope_key"],
                        "curation_id": row["curation_id"],
                        "changes": service.fields(revision["kind"], row["changes"]),
                    },
                )
            )
        for row in inventory.rows("node_visibility"):
            revision = revisions.get(row["curation"]["id"])
            if revision is None or revision["kind"] != "visibility":
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Node fence history is missing."
                )
            result.append(("control", "node_visibility", visibility.fence(decoded(row), revision)))
        return tuple(result)
