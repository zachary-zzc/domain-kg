"""Current access and full scoped references for immutable visibility history."""

from typing import Any
from uuid import UUID, uuid5

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.node_actions import scope_identity
from domain_kg.graph.visibility_models import NodeFence
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import ScopedRef


async def authorize_visibility_record(
    queue: JobQueue, access: AccessContext, kg: UUID, identifier: UUID
) -> dict[str, Any]:
    current = await queue.policy.revalidate(access, action=Action.READ)
    if kg not in current.kg_ids:
        raise inaccessible_reference()
    ns = {"org_id": str(current.org_id), "kg_id": str(kg)}
    record = await queue.database.curation_revisions.find_one(
        ns
        | {
            "id": str(identifier),
            "kind": "visibility",
            "state": "accepted",
        }
    )
    if record is None:
        raise inaccessible_reference()
    if record.get("job_id") is not None or record["actor"]["kind"] != "human":
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Visibility history origin differs.")
    materials = tuple(UUID(value) for value in record["material_ids"])
    scope = NodeFence(
        org_id=current.org_id,
        kg_id=kg,
        entity_id=record["target_id"],
        curation=ScopedRef(org_id=current.org_id, kg_id=kg, id=identifier),
        revision=record["expected_revision"] + 1,
        public=record["public"],
        material_ids=materials,
    )
    key = scope_identity(scope.public, materials)
    if record["scope_key"] != key or record["target_state_id"] != str(
        uuid5(scope.entity_id, f"curation-scope/{key}")
    ):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Visibility history scope differs.")
    for material in materials:
        if not await queue.policy.material_visible(current, kg, material):
            raise inaccessible_reference()
    stores = await queue.router.for_sources(current, kg)
    canonical = await stores.authority._require("curations", scope.curation)
    if (canonical["actor"], canonical["reason"]) != (
        record["provenance"]["actor"],
        record["provenance"]["reason"],
    ):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Visibility history provenance differs."
        )
    await stores.authority._require(
        "entities",
        ScopedRef(
            org_id=current.org_id,
            kg_id=kg,
            id=scope.entity_id,
        ),
    )
    if set(record["source_versions"]) != set(map(str, materials)):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Visibility history source set differs."
        )
    for material in materials:
        await stores.authority._require(
            "materials",
            ScopedRef(
                org_id=current.org_id,
                kg_id=kg,
                id=material,
            ),
        )
        version = ScopedRef(
            org_id=current.org_id, kg_id=kg, id=record["source_versions"][str(material)]
        )
        await stores.authority.require_version(version)
        raw = await stores.authority._require("material_versions", version)
        if raw["material"]["id"] != str(material):
            raise inaccessible_reference()
    await queue.policy.revalidate(current)
    return dict(record)
