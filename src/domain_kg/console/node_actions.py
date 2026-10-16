"""Explicit original identities, source ownership and revision guards in browser forms."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from domain_kg.console.material_commands import invalid_form
from domain_kg.console.team import identifiers
from domain_kg.graph.identity_models import IdentityGroup
from domain_kg.revisions.models import CurationCommand
from domain_kg.transport.runtime import ServiceRuntime

FIELDS = {
    "action",
    "phase",
    "expected_revision",
    "curation_scope",
    "source_ids",
    "reason",
    "idempotency_key",
    "member_ids",
    "partitions",
    "member_revisions",
}


def parse_action(
    fields: dict[str, str], kg: UUID, target: UUID
) -> tuple[CurationCommand, Literal["preview", "apply"]]:
    if set(fields) - FIELDS or fields.get("phase") not in ("preview", "apply"):
        raise invalid_form()
    try:
        partitions = tuple(
            IdentityGroup(canonical_id=UUID(canonical.strip()), member_ids=identifiers(members))
            for line in fields.get("partitions", "").splitlines()
            if line.strip()
            for canonical, members in [line.split("=", 1)]
        )
        revisions = tuple(
            (UUID(entity.strip()), int(revision))
            for part in fields.get("member_revisions", "").split(",")
            if part.strip()
            for entity, revision in [part.split(":", 1)]
        )
        phase: Literal["preview", "apply"] = "preview" if fields["phase"] == "preview" else "apply"
        command = CurationCommand.model_validate(
            {
                key: value
                for key, value in fields.items()
                if key not in {"phase", "partitions", "member_ids", "member_revisions"}
            }
            | {
                "kg_id": kg,
                "entity_id": target,
                "dry_run": phase == "preview",
                "expected_revision": int(fields.get("expected_revision", "")),
                "source_ids": identifiers(fields.get("source_ids", "")),
                "member_ids": identifiers(fields.get("member_ids", "")),
                "partitions": partitions,
                "member_revisions": revisions,
            }
        )
    except (ValueError, ValidationError):
        raise invalid_form() from None
    return command, phase


def node_action_router(runtime: "ServiceRuntime") -> "APIRouter":
    from uuid import uuid4

    from domain_kg.console.material_commands import command_context, redirect
    from domain_kg.console.rendering import page
    from domain_kg.console.workspace import filters_for
    from domain_kg.graph.identity_commands import IdentityImpact
    from domain_kg.identity.models import Action
    from domain_kg.retrieval.models import SearchRequest
    from domain_kg.revisions.models import CurationImpact

    router = APIRouter()

    @router.post("/kgs/{kg_id}/nodes/{target_id}/curate", response_model=None)
    async def curate(
        request: Request, kg_id: UUID, target_id: UUID
    ) -> HTMLResponse | RedirectResponse:
        fields, session, access = await command_context(runtime, request, kg_id, Action.CURATE)
        command, _ = parse_action(fields, kg_id, target_id)
        scope = await runtime.graph.resolver.resolve(
            access,
            SearchRequest(kg_ids=(kg_id,), query=str(target_id), filters=filters_for(True)),
            kg_id,
        )
        result = await runtime.revisions.curate_node(access, command)
        if isinstance(result, (CurationImpact, IdentityImpact)):
            normalized = command.model_copy(
                update={
                    "expected_revision": result.target_revision,
                    "member_revisions": tuple(
                        (entity, revision)
                        for entity, revision in result.member_revisions
                        if entity != target_id
                    )
                    if isinstance(result, IdentityImpact)
                    else (),
                    "member_ids": result.member_ids
                    if isinstance(result, IdentityImpact) and command.action == "merge"
                    else command.member_ids,
                }
            )
            inputs = {
                "action": normalized.action,
                "phase": "apply",
                "curation_scope": normalized.curation_scope,
                "source_ids": ",".join(map(str, normalized.source_ids)),
                "expected_revision": str(normalized.expected_revision),
                "reason": normalized.reason,
                "idempotency_key": normalized.idempotency_key,
                "member_ids": ",".join(map(str, normalized.member_ids)),
                "partitions": "\n".join(
                    f"{group.canonical_id}=" + ",".join(map(str, group.member_ids))
                    for group in normalized.partitions
                ),
                "member_revisions": ",".join(
                    f"{entity}:{revision}" for entity, revision in normalized.member_revisions
                ),
            }
            return await page(
                runtime,
                session,
                access,
                "curation_preview.html",
                {
                    "title": f"Review {command.action}",
                    "kg": await runtime.catalog.get_kg(access, kg_id),
                    "inputs": inputs,
                    "impact": result,
                    "target_id": target_id,
                    "preview_key": str(uuid4()),
                },
                scope=scope,
            )
        return redirect(
            f"/admin/kgs/{kg_id}/jobs/{result.job_id}"
            if result.job_id
            else f"/admin/kgs/{kg_id}/nodes/{target_id}/history"
            "?include_suppressed=true&include_unverified=true"
        )

    return router
