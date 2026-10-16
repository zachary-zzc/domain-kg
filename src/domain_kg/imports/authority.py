"""Current worker authority and exact immutable provenance for admitted imports."""

from dataclasses import dataclass
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.models import DomainSchema
from domain_kg.graph.schema import schema_for_kg
from domain_kg.identity.models import AccessContext
from domain_kg.imports.admission import record_matches_plan
from domain_kg.imports.legacy_models import LegacyGraph
from domain_kg.imports.models import ImportPlan
from domain_kg.imports.provenance import ImportOrigin, check_import_origin
from domain_kg.jobs.models import JobCommand
from domain_kg.models import SourceVersion

if TYPE_CHECKING:
    from domain_kg.jobs.queue import JobQueue


@dataclass(frozen=True)
class AdmittedImport:
    origin: ImportOrigin
    graph: LegacyGraph
    domain_schema: DomainSchema


async def load_admitted(
    queue: "JobQueue",
    access: AccessContext,
    command: JobCommand,
    material: UUID,
    session: AsyncClientSession | None = None,
) -> AdmittedImport:
    if (
        command.kind != "import"
        or command.org_id != access.org_id
        or command.kg_id not in access.kg_ids
    ):
        raise inaccessible_reference()
    source = SourceVersion(
        org_id=command.org_id,
        kg_id=command.kg_id,
        material_id=material,
        version_id=command.target_id,
    )
    raw = await queue.database.jobs.find_one(
        {
            "org_id": str(command.org_id),
            "kg_id": str(command.kg_id),
            "id": str(command.id),
            "kind": "import",
            "target_id": str(command.target_id),
            "material_id": str(material),
        },
        session=session,
    )
    if raw is None or any(
        raw.get("actor", {}).get(field) != getattr(access.principal, field)
        for field in ("issuer", "subject", "kind")
    ):
        raise inaccessible_reference()
    try:
        identifier = UUID(raw["import_plan_id"])
        record = await queue.database.import_plans.find_one(
            {
                "org_id": str(source.org_id),
                "kg_id": str(source.kg_id),
                "id": str(identifier),
                "issuer": access.principal.issuer,
                "subject": access.principal.subject,
                "state": "admitted",
                "job_id": str(command.id),
                "source": source.model_dump(mode="json"),
            },
            session=session,
        )
        if record is None:
            raise inaccessible_reference()
        plan = ImportPlan.model_validate(record["plan"])
        if not record_matches_plan(record, plan) or raw["import_plan_hash"] != record["plan_hash"]:
            raise DomainError(
                ErrorCode.EVIDENCE_VALIDATION_FAILED, "The admitted import seal differs."
            )
        graph = LegacyGraph.model_validate(record["graph"])
        schema = DomainSchema.model_validate(record["binding"]["schema"])
        origin = ImportOrigin(
            **source.model_dump(),
            plan_id=plan.id,
            plan_hash=record["plan_hash"],
            graph_hash=record["graph_hash"],
            origin=graph.origin,
        )
        if (schema.org_id, schema.kg_id, schema.id, schema.revision) != (
            source.org_id,
            source.kg_id,
            plan.schema_id,
            plan.schema_revision,
        ):
            raise inaccessible_reference()
    except (KeyError, ValueError, TypeError, ValidationError):
        raise DomainError(
            ErrorCode.EVIDENCE_VALIDATION_FAILED, "The admitted import metadata is invalid."
        ) from None
    await check_import_origin(queue.database, source, origin, session=session)
    if await schema_for_kg(queue.policy, access, command.kg_id, session=session) != schema:
        raise DomainError(ErrorCode.VERSION_CONFLICT, "The import domain schema changed.")
    return AdmittedImport(origin, graph, schema)
