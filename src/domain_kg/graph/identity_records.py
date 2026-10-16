"""Validate immutable identity ownership and all shared node counter references."""

from datetime import UTC
from typing import Any
from uuid import UUID, uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.identity_models import IdentityGroup, IdentityState, ManualIdentity
from domain_kg.identity.models import AccessContext
from domain_kg.jobs.queue import JobQueue
from domain_kg.materials.service import digest
from domain_kg.models import ManualProvenance, ScopedRef


def inconsistent() -> DomainError:
    return DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Identity revision ownership differs.")


def identity_directive(record: dict[str, Any]) -> ManualIdentity:
    ns = {"org_id": record["org_id"], "kg_id": record["kg_id"]}
    controlled = record.get("retracts") is not None
    groups: tuple[IdentityGroup, ...]
    if controlled:
        if (
            record["operation"] != "identity_control"
            or type(record["after"].get("enabled")) is not bool
        ):
            raise inconsistent()
        groups = ()
        retracts = ScopedRef.model_validate(record["retracts"])
        enabled = record["after"]["enabled"]
    else:
        if record["operation"] not in ("merge", "split"):
            raise inconsistent()
        groups = IdentityState.model_validate(record["after"]).groups
        retracts, enabled = None, True
    return ManualIdentity(
        curation=ScopedRef(**ns, id=record["id"]),
        target=ScopedRef(**ns, id=record["target_id"]),
        groups=groups,
        provenance=ManualProvenance.model_validate(record["provenance"]),
        revision=record["expected_revision"] + 1,
        created_at=record["created_at"].replace(tzinfo=UTC),
        sequence=record["identity_sequence"],
        public=record["public"],
        material_ids=tuple(UUID(value) for value in record["material_ids"]),
        retracts=retracts,
        enabled=enabled,
    )


async def validate_identity_record(
    queue: JobQueue,
    access: AccessContext,
    record: dict[str, Any],
    *,
    preparing: bool,
    session: AsyncClientSession | None,
) -> None:
    manual = identity_directive(record)
    kg = await queue.database.kgs.find_one(
        {"org_id": record["org_id"], "id": record["kg_id"]}, session=session
    )
    if kg is None or not 1 <= manual.sequence <= kg.get("identity_sequence", 0):
        raise inconsistent()
    scope = digest({"public": record["public"], "material_ids": sorted(record["material_ids"])})
    members = tuple(UUID(value) for value in record["members"])
    targets = record["target_revisions"]
    if (
        record["scope_key"] != scope
        or not 1 <= len(members) <= 100
        or len(set(members)) != len(members)
        or len(targets) != len(members)
        or {row["target_id"] for row in targets} != set(map(str, members))
    ):
        raise inconsistent()
    if manual.retracts is None and manual.members != frozenset(members):
        raise inconsistent()
    if manual.retracts is None and (
        len(manual.members) < 2
        or IdentityState.model_validate(record["before"]).members != manual.members
        or (
            record["operation"] == "merge"
            and (len(manual.groups) != 1 or manual.groups[0].canonical_id != manual.target.id)
        )
        or record["operation"] == "split"
        and len(manual.groups) < 2
    ):
        raise inconsistent()
    ns = {"org_id": record["org_id"], "kg_id": record["kg_id"]}
    stores = await queue.router.for_sources(access, UUID(record["kg_id"]))
    for target in targets:
        entity = UUID(target["target_id"])
        expected_id = uuid5(entity, f"curation-scope/{scope}")
        if (
            target["state_id"] != str(expected_id)
            or type(target["expected_revision"]) is not int
            or target["expected_revision"] < 0
        ):
            raise inconsistent()
        await stores.authority._require(
            "entities",
            ScopedRef(org_id=manual.curation.org_id, kg_id=manual.curation.kg_id, id=entity),
            session=session,
        )
        current = await queue.database.curation_targets.find_one(
            ns | {"id": str(expected_id)}, session=session
        )
        if current is None or (current["kind"], current["target_id"], current["scope_key"]) != (
            "node",
            str(entity),
            scope,
        ):
            raise inconsistent()
        if preparing and current["revision"] != target["expected_revision"]:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "An identity target revision changed.")
        if record["state"] == "accepted" and current["revision"] < target["expected_revision"] + 1:
            raise inconsistent()
    primary = next((value for value in targets if value["target_id"] == record["target_id"]), None)
    if primary is None or (primary["state_id"], primary["expected_revision"]) != (
        record["target_state_id"],
        record["expected_revision"],
    ):
        raise inconsistent()
    if manual.retracts is not None:
        original = await queue.database.curation_revisions.find_one(
            ns | {"id": str(manual.retracts.id), "kind": "identity", "state": "accepted"},
            session=session,
        )
        if (
            original is None
            or original.get("retracts") is not None
            or any(
                original[key] != record[key]
                for key in ("scope_key", "target_id", "public", "material_ids")
            )
            or not set(original["members"]).issubset(record["members"])
            or original["identity_sequence"] >= manual.sequence
            or type(record["before"].get("enabled")) is not bool
        ):
            raise inconsistent()
        if (manual.retracts.org_id, manual.retracts.kg_id) != (
            manual.curation.org_id,
            manual.curation.kg_id,
        ):
            raise inaccessible_reference()
        selected = await queue.database.curation_revisions.find_one(
            ns | {"id": record.get("rollback_of"), "kind": "identity", "state": "accepted"},
            session=session,
        )
        if selected is None or (
            selected["id"] != original["id"]
            and selected.get("retracts") != manual.retracts.model_dump(mode="json")
        ):
            raise inconsistent()
        desired = not selected["after"]["enabled"] if selected.get("retracts") else False
        if manual.enabled != desired:
            raise inconsistent()
