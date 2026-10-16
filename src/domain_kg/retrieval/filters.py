"""One support-filter implementation shared by graph, vector and text reads."""

from domain_kg.engine.scope import EligibilityResolver as EligibilityResolver
from domain_kg.retrieval.models import SearchFilters
from domain_kg.revisions.projections import ProjectionBody


def matches_support(
    row: ProjectionBody, filters: SearchFilters, *, endpoint_types: tuple[str, ...] | None = None
) -> bool:
    if not row.claim:
        return False
    fact, support = row.claim.assertion, row.claim.support
    endpoints = {fact.subject.id, fact.target.id}
    if endpoints.intersection(filters.exclude_entity_ids):
        return False
    if filters.entity_ids and not endpoints.intersection(filters.entity_ids):
        return False
    types = (
        endpoint_types
        if endpoint_types is not None
        else tuple(
            item.entity_type for item in (row.subject_observation, row.target_observation) if item
        )
    )
    if filters.entity_types and not set(types).intersection(filters.entity_types):
        return False
    return (
        support.status in filters.support_statuses
        and (not filters.predicates or fact.predicate in filters.predicates)
        and (
            filters.min_confidence is None
            or (support.confidence is not None and support.confidence >= filters.min_confidence)
        )
    )


def matches_observation(
    row: ProjectionBody, filters: SearchFilters, *, entity_type: str | None = None
) -> bool:
    item = row.observation
    return bool(
        item
        and item.entity.id not in filters.exclude_entity_ids
        and (not filters.entity_types or (entity_type or item.entity_type) in filters.entity_types)
    )
