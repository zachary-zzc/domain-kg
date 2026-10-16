"""One support-filter implementation shared by graph, vector and text reads."""

from domain_kg.engine.scope import EligibilityResolver as EligibilityResolver
from domain_kg.retrieval.models import SearchFilters
from domain_kg.revisions.projections import ProjectionBody


def matches_support(row: ProjectionBody, filters: SearchFilters) -> bool:
    if not row.assertion or not row.subject_observation or not row.target_observation:
        return False
    fact, support = row.assertion.assertion, row.assertion.support
    endpoints = {fact.subject.id, fact.target.id}
    if endpoints.intersection(filters.exclude_entity_ids):
        return False
    if filters.entity_ids and not endpoints.intersection(filters.entity_ids):
        return False
    if filters.entity_types and not {
        row.subject_observation.entity_type,
        row.target_observation.entity_type,
    }.intersection(filters.entity_types):
        return False
    return (
        support.status in filters.support_statuses
        and (not filters.predicates or fact.predicate in filters.predicates)
        and (
            filters.min_confidence is None
            or (support.confidence is not None and support.confidence >= filters.min_confidence)
        )
    )


def matches_observation(row: ProjectionBody, filters: SearchFilters) -> bool:
    item = row.observation
    return bool(
        item
        and item.entity.id not in filters.exclude_entity_ids
        and (not filters.entity_types or item.entity_type in filters.entity_types)
    )
