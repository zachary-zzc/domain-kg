"""An immutable source-qualified marker for explicitly unverified legacy evidence."""

from typing import Any, Literal
from uuid import UUID

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.imports.legacy_models import LegacyID
from domain_kg.models import SHA256, SourceVersion


class ImportOrigin(SourceVersion):
    plan_id: UUID
    plan_hash: SHA256
    graph_hash: SHA256
    origin: LegacyID
    support_status: Literal["imported_unverified"] = "imported_unverified"

    def matches(self, source: SourceVersion) -> bool:
        return all(
            getattr(self, field) == getattr(source, field) for field in SourceVersion.model_fields
        )


async def check_import_origin(
    database: AsyncDatabase[dict[str, Any]],
    source: SourceVersion,
    origin: ImportOrigin | None,
    *,
    session: AsyncClientSession | None = None,
) -> None:
    row = await database.import_source_origins.find_one(
        source.model_dump(mode="json"), session=session
    )
    try:
        expected = (
            ImportOrigin.model_validate({key: value for key, value in row.items() if key != "_id"})
            if row is not None
            else None
        )
    except ValidationError:
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Accepted import provenance is inconsistent."
        ) from None
    if expected != origin or origin is not None and not origin.matches(source):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Accepted import provenance is inconsistent."
        )
