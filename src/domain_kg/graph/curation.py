"""Current authorization of durable curation ownership, separate from model output."""

from typing import Any
from uuid import UUID

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.models import AssertionRecord, SupportRecord
from domain_kg.identity.models import AccessContext, Action
from domain_kg.jobs.queue import JobQueue
from domain_kg.models import ScopedRef


async def authorize_curation(
    queue: JobQueue,
    access: AccessContext,
    identifier: UUID,
    *,
    kg_id: UUID,
    write: bool = False,
    preparing: bool = False,
    session: AsyncClientSession | None = None,
) -> dict[str, Any]:
    current = await queue.policy.revalidate(
        access, action=Action.CURATE if write else None, session=session
    )
    if kg_id not in current.kg_ids:
        raise inaccessible_reference()
    raw = await queue.database.curation_revisions.find_one(
        {
            "org_id": str(current.org_id),
            "kg_id": str(kg_id),
            "id": str(identifier),
        },
        session=session,
    )
    if raw is None or raw.get("state") not in ("pending", "accepted"):
        raise inaccessible_reference()
    if raw.get("kind") not in ("node", "relation", "identity"):
        raise inaccessible_reference()
    mirror = await queue.database.curation_access.find_one(
        {"org_id": raw["org_id"], "kg_id": raw["kg_id"], "id": raw["id"]},
        session=session,
    )
    if mirror is None or any(mirror.get(key) != raw[key] for key in ("public", "material_ids")):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Curation visibility ownership differs."
        )
    if (mirror.get("kind") == "identity") != (raw["kind"] == "identity"):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Structural curation ownership differs."
        )
    origin = await queue.database.jobs.find_one(
        {
            "org_id": raw["org_id"],
            "kg_id": raw["kg_id"],
            "id": raw["job_id"],
            "kind": "curate",
            "target_id": raw["id"],
            "actor": raw["actor"],
        },
        session=session,
    )
    if origin is None:
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Curation origin job ownership differs."
        )
    kg = UUID(raw["kg_id"])
    role = next((grant.role for grant in current.grants if grant.kg_id == kg), None)
    if write:
        if role not in ("owner", "editor") or current.principal.kind != "human":
            raise inaccessible_reference()
        if (raw["public"] or raw.get("rollback_of") is not None) and role != "owner":
            raise inaccessible_reference()
        if any(
            raw["actor"].get(field) != getattr(current.principal, field)
            for field in ("issuer", "subject", "kind")
        ):
            raise inaccessible_reference()
    if raw["public"]:
        if raw["material_ids"]:
            raise inaccessible_reference()
    elif not raw["material_ids"]:
        raise inaccessible_reference()
    for material in raw["material_ids"]:
        if not await queue.policy.material_visible(current, kg, UUID(material), session=session):
            raise inaccessible_reference()
        target = await queue.database.material_access.find_one(
            {
                "org_id": raw["org_id"],
                "kg_id": raw["kg_id"],
                "id": material,
            },
            session=session,
        )
        if target is None:
            raise inaccessible_reference()
        if preparing and target.get("active_version_id") != raw["source_versions"].get(material):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "A curation source version changed.")
    if preparing:
        from domain_kg.graph.schema import schema_for_kg

        if (await schema_for_kg(queue.policy, current, kg, session=session)).model_dump(
            mode="json"
        ) != raw["domain_schema"]:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The curation schema changed.")
        target = await queue.database.curation_targets.find_one(
            {
                "org_id": raw["org_id"],
                "kg_id": raw["kg_id"],
                "id": raw["target_state_id"],
            },
            session=session,
        )
        if (
            raw["state"] != "pending"
            or target is None
            or target["target_id"] != raw["target_id"]
            or target["scope_key"] != raw["scope_key"]
            or target["revision"] != raw["expected_revision"]
        ):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "The curation target revision changed.")
    if session is None:
        stores = await queue.router.for_sources(current, kg)
        canonical = await stores.authority._require(
            "curations", ScopedRef(org_id=current.org_id, kg_id=kg, id=identifier)
        )
        if (canonical["actor"], canonical["reason"]) != (
            raw["provenance"]["actor"],
            raw["provenance"]["reason"],
        ):
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Curation provenance differs.")
        target_record = await stores.authority._require(
            "entities" if raw["kind"] in ("node", "identity") else "assertions",
            ScopedRef(org_id=current.org_id, kg_id=kg, id=raw["target_id"]),
        )
        if raw["kind"] == "relation":
            from domain_kg.graph.manual_relations import ManualRelation
            from domain_kg.revisions.curation_publication import directive

            target_fact = AssertionRecord.model_validate(
                {key: value for key, value in target_record.items() if key != "_id"}
            )
            if not raw["public"] and target_fact.model_dump(mode="json") != raw["base_fact"]:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Canonical relationship target differs."
                )
            manual = directive(raw)
            if not isinstance(manual, ManualRelation):
                raise inaccessible_reference()
            for claim, collection, model in (
                (manual.fact, "assertions", AssertionRecord),
                (manual.support, "supports", SupportRecord),
            ):
                if claim is None:
                    continue
                value = await stores.authority._require(collection, claim)
                if (
                    model.model_validate({key: item for key, item in value.items() if key != "_id"})
                    != claim
                ):
                    raise DomainError(
                        ErrorCode.PROJECTION_INCONSISTENT, "Canonical manual claim differs."
                    )
        for material in raw["material_ids"]:
            await stores.authority._require(
                "materials", ScopedRef(org_id=current.org_id, kg_id=kg, id=material)
            )
            if preparing:
                source = await stores.authority.get_material_version(
                    ScopedRef(org_id=current.org_id, kg_id=kg, id=raw["source_versions"][material])
                )
                if source is None or str(source.material.id) != material:
                    raise inaccessible_reference()
    if raw["kind"] == "identity":
        from domain_kg.graph.identity_records import validate_identity_record

        await validate_identity_record(
            queue, current, dict(raw), preparing=preparing, session=session
        )
    return dict(raw)
