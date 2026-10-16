"""Import intent and source provenance join the material/job acceptance transaction."""

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.schema import schema_for_kg
from domain_kg.identity.models import AccessContext
from domain_kg.imports.legacy_models import LegacyGraph
from domain_kg.imports.models import ImportPlan
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.materials.models import BlobRef
from domain_kg.models import SourceVersion

if TYPE_CHECKING:
    from domain_kg.materials.service import MaterialService


async def admit_import(
    materials: "MaterialService",
    access: AccessContext,
    plan: ImportPlan,
    blob: BlobRef,
    job_id: UUID,
    session: AsyncClientSession,
) -> ImportOrigin:
    source = SourceVersion(**{field: getattr(blob, field) for field in SourceVersion.model_fields})
    if (plan.org_id, plan.kg_id, plan.source.kg_id) != (access.org_id, source.kg_id, source.kg_id):
        raise inaccessible_reference()
    record = await materials.database.import_plans.find_one(
        {
            "org_id": str(access.org_id),
            "kg_id": str(source.kg_id),
            "id": str(plan.id),
            "issuer": access.principal.issuer,
            "subject": access.principal.subject,
        },
        session=session,
    )
    if record is None:
        raise inaccessible_reference()
    plan_hash = hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
    if record["plan"] != plan.model_dump(mode="json") or record["plan_hash"] != plan_hash:
        raise DomainError(
            ErrorCode.EVIDENCE_VALIDATION_FAILED, "The import plan differs from its seal."
        )
    if (
        record["state"] != "validated"
        or plan.expires_at is None
        or plan.expires_at <= datetime.now(UTC)
    ):
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The import plan is no longer executable.")
    binding = record["binding"]
    schema = await schema_for_kg(materials.policy, access, source.kg_id, session=session)
    kg = await materials.database.kgs.find_one(
        {"org_id": str(access.org_id), "id": str(source.kg_id)}, session=session
    )
    if (
        kg is None
        or binding["policy_revision"] != access.policy_revision
        or binding["principal_kind"] != access.principal.kind
        or binding["schema"] != schema.model_dump(mode="json")
        or binding["blob"] != blob.model_dump(mode="json")
        or any(
            kg.get(field, default) != binding[name]
            for field, name, default in (
                ("published_revision", "published_revision", 0),
                ("revision", "kg_revision", 0),
                ("visibility_revision", "visibility_revision", 0),
                ("generation", "generation", None),
                ("published_manifest_id", "manifest_id", None),
                ("configuration_epoch", "configuration_epoch", 0),
            )
        )
    ):
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The validated import scope changed.")
    graph = LegacyGraph.model_validate(record["graph"])
    if (
        hashlib.sha256(graph.model_dump_json().encode()).hexdigest() != record["graph_hash"]
        or (graph.namespace.org_id, graph.namespace.kg_id) != (source.org_id, source.kg_id)
        or graph.raw_sha256 != blob.sha256
        or graph.origin != (plan.source.origin or blob.sha256)
        or (len(graph.entities), len(graph.relations)) != (plan.entities, plan.assertions)
    ):
        raise DomainError(
            ErrorCode.EVIDENCE_VALIDATION_FAILED, "The normalized import records differ."
        )
    origin = ImportOrigin(
        **source.model_dump(),
        plan_id=plan.id,
        plan_hash=plan_hash,
        graph_hash=record["graph_hash"],
        origin=graph.origin,
    )
    await materials.database.import_source_origins.insert_one(
        origin.model_dump(mode="json"), session=session
    )
    updated = await materials.database.import_plans.update_one(
        {"_id": record["_id"], "state": "validated"},
        {
            "$set": {
                "state": "admitted",
                "job_id": str(job_id),
                "source": source.model_dump(mode="json"),
            }
        },
        session=session,
    )
    if updated.modified_count != 1:
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The import plan admission changed.")
    return origin


def record_matches_plan(record: dict[str, Any], plan: ImportPlan) -> bool:
    try:
        graph = LegacyGraph.model_validate(record["graph"])
        return (
            record.get("plan") == plan.model_dump(mode="json")
            and record.get("plan_hash")
            == hashlib.sha256(plan.model_dump_json().encode()).hexdigest()
            and record.get("graph_hash")
            == hashlib.sha256(graph.model_dump_json().encode()).hexdigest()
            and (graph.namespace.org_id, graph.namespace.kg_id) == (plan.org_id, plan.kg_id)
            and graph.raw_sha256 == plan.content_hash
            and graph.origin == (plan.source.origin or plan.content_hash)
            and (len(graph.entities), len(graph.relations)) == (plan.entities, plan.assertions)
        )
    except (KeyError, TypeError, ValidationError):
        return False
