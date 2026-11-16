"""Versioned general/scientific profiles, instantiated inside each KG namespace."""

from typing import TYPE_CHECKING
from uuid import UUID, uuid5

from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.graph.models import DomainSchema, PredicateDefinition
from domain_kg.models import Namespace

if TYPE_CHECKING:
    from domain_kg.identity.models import AccessContext
    from domain_kg.identity.policy import Policy


def schema_for(namespace: Namespace, name: str) -> DomainSchema:
    types: tuple[str, ...]
    predicates: tuple[str, ...]
    if name == "general":
        types = ("concept", "person", "organization", "event", "artifact")
        predicates = ("related_to", "part_of", "causes", "uses", "supports", "contradicts")
    elif name == "medical":
        types = (
            "condition",
            "anatomy",
            "substance",
            "test",
            "measurement",
            "care_activity",
            "person",
            "organization",
            "concept",
        )
        predicates = (
            "is_a",
            "part_of",
            "produces",
            "screens_for",
            "measures",
            "uses",
            "supports",
            "associated_with",
            "related_to",
            "causes",
        )
    elif name == "scientific":
        types = ("gene", "protein", "compound", "disease", "process", "method", "result")
        predicates = (
            "interacts_with",
            "inhibits",
            "activates",
            "part_of",
            "associated_with",
            "supports",
            "contradicts",
        )
    else:
        raise ValueError("unknown approved domain profile")
    return DomainSchema(
        **namespace.model_dump(),
        id=uuid5(namespace.kg_id, f"schema/{name}/v2"),
        revision=2,
        vocabulary="open",
        entity_types=types,
        predicates=tuple(
            PredicateDefinition(name=value, subject_types=types, target_types=types)
            for value in predicates
        ),
    )


async def schema_for_kg(
    policy: "Policy",
    access: "AccessContext",
    kg_id: "UUID",
    *,
    session: AsyncClientSession | None = None,
) -> DomainSchema:
    from domain_kg.errors import inaccessible_reference
    from domain_kg.models import Namespace

    current = await policy.revalidate(access, session=session)
    if kg_id not in current.kg_ids:
        raise inaccessible_reference()
    stored = await policy.database.kg_schemas.find_one(
        {"org_id": str(current.org_id), "kg_id": str(kg_id)}, session=session
    )
    if stored:
        return DomainSchema.model_validate(
            {key: stored[key] for key in DomainSchema.model_fields if key in stored}
        )
    kg = await policy.database.kgs.find_one(
        {"org_id": str(current.org_id), "id": str(kg_id)}, session=session
    )
    profile = (
        await policy.database.domain_profiles.find_one(
            {"id": kg.get("domain_profile_id")}, session=session
        )
        if kg
        else None
    )
    if profile is None:
        raise inaccessible_reference()
    return schema_for(Namespace(org_id=current.org_id, kg_id=kg_id), profile["name"])
