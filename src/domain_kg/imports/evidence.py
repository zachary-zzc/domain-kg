"""A visible source's accepted marker, never its text, determines import provenance."""

from typing import Literal

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.models import SourceVersion
from domain_kg.revisions.models import ReadScope


def visible_status(value: str) -> Literal["verified", "manual", "imported_unverified"]:
    match value:
        case "verified":
            return "verified"
        case "manual":
            return "manual"
        case "imported_unverified":
            return "imported_unverified"
        case _:
            raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Evidence status is not readable.")


def source_origin(scope: ReadScope, source: SourceVersion | None) -> ImportOrigin | None:
    if source is None:
        return None
    if source not in scope.visible_sources:
        raise inaccessible_reference()
    bindings = [binding for binding in scope.projections if binding.source == source]
    if len(bindings) != 1:
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Visible source ownership differs.")
    origin = bindings[0].import_origin
    if origin is not None and not origin.matches(source):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Visible import ownership differs.")
    return origin
