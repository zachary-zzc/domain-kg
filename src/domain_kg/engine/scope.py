"""Immutable eligible-source resolution and per-call backend predicates."""

import hashlib
import json
from typing import Any, Protocol
from uuid import UUID

from qdrant_client import models

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.materials.models import MaterialMetadata
from domain_kg.retrieval.models import SearchRequest
from domain_kg.revisions.models import PublishedView, ReadScope


class AcceptedViews(Protocol):
    async def load(self, access: AccessContext, kg_id: UUID) -> PublishedView: ...


class EligibilityResolver:
    def __init__(self, policy: Policy, views: AcceptedViews) -> None:
        self.policy, self.views = policy, views

    async def resolve(
        self, access: AccessContext, request: SearchRequest, kg_id: UUID
    ) -> ReadScope:
        request = SearchRequest.model_validate(request)
        current = await self.policy.revalidate(access, action=Action.SEARCH)
        if kg_id not in request.kg_ids or kg_id not in current.kg_ids:
            raise inaccessible_reference()
        view = await self.views.load(current, kg_id)
        if (view.org_id, view.kg_id) != (current.org_id, kg_id):
            raise DomainError(
                ErrorCode.PROJECTION_INCONSISTENT, "The accepted view namespace is inconsistent."
            )
        kg = await self.policy.database.kgs.find_one(
            {"org_id": str(current.org_id), "id": str(kg_id)}
        )
        if kg is None:
            raise inaccessible_reference()
        visible = []
        for source in view.sources:
            if not await self.policy.material_visible(current, kg_id, source.material_id):
                continue
            record = await self.policy.database.material_access.find_one(
                {"org_id": str(current.org_id), "kg_id": str(kg_id), "id": str(source.material_id)}
            )
            if record is None:
                raise DomainError(ErrorCode.POLICY_CHANGED, "Source visibility changed.")
            metadata = MaterialMetadata.model_validate(record["metadata"])
            if metadata.material_id != source.material_id:
                raise DomainError(
                    ErrorCode.PROJECTION_INCONSISTENT, "Source ownership is inconsistent."
                )
            if request.filters.version_id is not None:
                if source.version_id != request.filters.version_id:
                    continue
            elif record.get("active_version_id") != str(source.version_id):
                raise DomainError(ErrorCode.POLICY_CHANGED, "The active source view changed.")
            metadata = metadata.model_copy(update={"version_id": source.version_id})
            if request.filters.matches_material(metadata):
                visible.append(source)
        curations = []
        visible_materials = {str(source.material_id) for source in visible}
        for curation in view.curations:
            row = await self.policy.database.curation_access.find_one(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(kg_id),
                    "id": str(curation.id),
                    "suppressed": False,
                }
            )
            if row is None:
                continue
            targets = set(row.get("material_ids", []))
            if targets and targets.issubset(visible_materials):
                curations.append(curation)
            elif (
                not targets
                and row.get("public", False)
                and request.filters.allows_sourceless_manual
            ):
                curations.append(curation)
        scope = ReadScope(
            access=current,
            view=view,
            visible_sources=tuple(visible),
            visible_curations=tuple(curations),
            filters=request.filters,
            visibility_revision=kg["visibility_revision"],
        )
        await self.revalidate(scope)
        return scope

    async def revalidate(self, scope: ReadScope) -> None:
        await self.policy.revalidate(scope.access, action=Action.SEARCH)
        kg = await self.policy.database.kgs.find_one(
            {
                "org_id": str(scope.view.org_id),
                "id": str(scope.view.kg_id),
                "visibility_revision": scope.visibility_revision,
            }
        )
        if kg is None:
            raise DomainError(ErrorCode.POLICY_CHANGED, "Visibility changed during the request.")
        view = await self.views.load(scope.access, scope.view.kg_id)
        if (
            (view.revision, view.generation, view.manifest_id)
            != (scope.view.revision, scope.view.generation, scope.view.manifest_id)
            or frozenset(view.projections) != frozenset(scope.view.projections)
            or frozenset(view.receipts) != frozenset(scope.view.receipts)
            or frozenset(view.sources) != frozenset(scope.view.sources)
            or frozenset(view.curations) != frozenset(scope.view.curations)
        ):
            raise DomainError(
                ErrorCode.POLICY_CHANGED, "The accepted publication changed during the request."
            )


class ScopePredicates:
    def __init__(self, scope: ReadScope) -> None:
        self.scope = scope

    @property
    def graph_parameters(self) -> dict[str, Any]:
        return {
            "org_id": str(self.scope.view.org_id),
            "kg_id": str(self.scope.view.kg_id),
            "generation": str(self.scope.view.generation),
            "version_ids": [str(source.version_id) for source in self.scope.visible_sources],
            "source_pairs": [
                {"material_id": str(source.material_id), "version_id": str(source.version_id)}
                for source in self.scope.visible_sources
            ],
            "curation_ids": [str(curation.id) for curation in self.scope.visible_curations],
            "accepted_projections": [
                {
                    "material_id": str(binding.source.material_id) if binding.source else None,
                    "source_version_id": str(binding.source.version_id) if binding.source else None,
                    "curation_revision_id": str(binding.curation.id) if binding.curation else None,
                    "attempt_id": str(binding.attempt_id),
                    "manifest_id": str(binding.manifest_id),
                    "manifest_hash": binding.content_hash,
                }
                for binding in self.scope.view.projections
                if (
                    binding.source in self.scope.visible_sources
                    if binding.source
                    else binding.curation in self.scope.visible_curations
                )
            ],
        }

    @property
    def vector_filter(self) -> models.Filter:
        def equal(key: str, value: UUID | str) -> models.FieldCondition:
            return models.FieldCondition(key=key, match=models.MatchValue(value=str(value)))

        alternatives = []
        for binding in self.scope.view.projections:
            ownership = []
            if binding.source is not None:
                if binding.source not in self.scope.visible_sources:
                    continue
                ownership.extend(
                    [
                        equal("material_id", binding.source.material_id),
                        equal("source_version_id", binding.source.version_id),
                    ]
                )
            if binding.curation is not None:
                if binding.curation not in self.scope.visible_curations:
                    continue
                ownership.append(equal("curation_revision_id", binding.curation.id))
            alternatives.append(
                models.Filter(
                    must=[
                        *ownership,
                        equal("attempt_id", binding.attempt_id),
                        equal("manifest_id", binding.manifest_id),
                        equal("manifest_hash", binding.content_hash),
                    ]
                )
            )
        # An impossible UUID payload predicate implements an empty eligible set;
        # omitting an empty should-clause could otherwise broaden the query.
        eligible = (
            models.Filter(should=[*alternatives])
            if alternatives
            else models.Filter(
                must=[models.FieldCondition(key="source_version_id", match=models.MatchAny(any=[]))]
            )
        )
        return models.Filter(
            must=[
                equal("org_id", self.scope.view.org_id),
                equal("kg_id", self.scope.view.kg_id),
                equal("generation", self.scope.view.generation),
                eligible,
            ]
        )

    @property
    def cache_key(self) -> str:
        value = self.scope.model_dump(mode="json")
        value["access"]["principal"]["scopes"] = sorted(self.scope.access.principal.scopes)
        value["access"]["groups"] = sorted(str(group) for group in self.scope.access.groups)
        return hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
