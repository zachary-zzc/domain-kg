"""Explicit browser fields become the same typed export command as HTTP."""

from uuid import UUID

from pydantic import ValidationError

from domain_kg.console.material_commands import invalid_form
from domain_kg.operations.models import ExportCommand
from domain_kg.retrieval.models import SearchFilters


def export_command(kg: UUID, fields: dict[str, str]) -> ExportCommand:
    required = {"expected_published_revision", "format", "idempotency_key"}
    if not required.issubset(fields) or set(fields) - required - {
        "material_ids",
        "include_unverified",
    }:
        raise invalid_form()
    if fields.get("include_unverified", "") not in ("", "on"):
        raise invalid_form()
    try:
        statuses = (
            ("verified", "model_interpreted", "manual", "imported_unverified")
            if fields.get("include_unverified")
            else ("verified", "model_interpreted", "manual")
        )
        filters = SearchFilters.model_validate(
            {
                "material_ids": tuple(
                    UUID(value.strip())
                    for value in fields.get("material_ids", "").split(",")
                    if value.strip()
                ),
                "support_statuses": statuses,
            }
        )
        return ExportCommand.model_validate(
            {
                "kg_id": kg,
                "expected_published_revision": int(fields["expected_published_revision"]),
                "format": fields["format"],
                "filters": filters,
                "idempotency_key": fields["idempotency_key"],
            }
        )
    except (ValueError, ValidationError):
        raise invalid_form() from None
