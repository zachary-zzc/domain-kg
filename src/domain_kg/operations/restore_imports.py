"""Source-qualified legacy history remains unverified across typed restore seals."""

import hashlib
from typing import Self

from pydantic import model_validator

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.imports.legacy import parse_legacy, stable
from domain_kg.imports.legacy_models import LegacyEntity, LegacyGraph, LegacyRelation
from domain_kg.imports.models import ImportPlan, ImportSource
from domain_kg.imports.provenance import ImportOrigin
from domain_kg.models import FrozenModel, ScopedRef
from domain_kg.operations.remapping import RestoreRemapper


def checksum(value: FrozenModel) -> str:
    return hashlib.sha256(value.model_dump_json().encode()).hexdigest()


class RestoredImport(FrozenModel):
    original: ImportOrigin
    plan: ImportPlan
    graph: LegacyGraph
    origin: ImportOrigin

    @model_validator(mode="after")
    def sealed_target(self) -> Self:
        if (
            self.original.org_id != self.origin.org_id
            or self.original.kg_id == self.origin.kg_id
            or self.origin.plan_hash != checksum(self.plan)
            or self.origin.graph_hash != checksum(self.graph)
            or self.origin.plan_id != self.plan.id
            or self.original.plan_id != self.plan.id
            or self.origin.origin != self.graph.origin
            or self.graph.origin != (self.plan.source.origin or self.plan.content_hash)
            or (self.origin.org_id, self.origin.kg_id) != (self.plan.org_id, self.plan.kg_id)
            or self.plan.source.kg_id != self.origin.kg_id
            or (self.graph.namespace.org_id, self.graph.namespace.kg_id)
            != (self.origin.org_id, self.origin.kg_id)
            or self.graph.raw_sha256 != self.plan.content_hash
            or (len(self.graph.entities), len(self.graph.relations))
            != (self.plan.entities, self.plan.assertions)
            or (self.original.material_id, self.original.version_id, self.original.origin)
            != (self.origin.material_id, self.origin.version_id, self.origin.origin)
        ):
            raise ValueError("restored import seals must retain their explicit source history")
        return self


def restore_import(
    mapping: RestoreRemapper, plan: ImportPlan, graph: LegacyGraph, origin: ImportOrigin, raw: bytes
) -> RestoredImport:
    source = mapping.source(origin)
    if (
        hashlib.sha256(raw).hexdigest() != plan.content_hash
        or parse_legacy(mapping.origin, plan.source.format, raw, origin=graph.origin) != graph
    ):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore import bytes differ.")
    if (
        (plan.org_id, plan.kg_id, plan.source.kg_id)
        != (mapping.origin.org_id, mapping.origin.kg_id, mapping.origin.kg_id)
        or graph.namespace != mapping.origin
        or origin.plan_id != plan.id
        or origin.plan_hash != checksum(plan)
        or origin.graph_hash != checksum(graph)
        or graph.raw_sha256 != plan.content_hash
        or graph.origin != (plan.source.origin or plan.content_hash)
        or origin.origin != graph.origin
        or (len(graph.entities), len(graph.relations)) != (plan.entities, plan.assertions)
    ):
        raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore import seals differ.")
    entities = tuple(
        LegacyEntity.model_validate(
            item.model_dump()
            | {
                "id": stable(
                    mapping.target, graph.origin, "observation", item.source_id, item.legacy_id
                ),
                "entity_id": mapping.entity_ref(
                    ScopedRef(**mapping.origin.model_dump(), id=item.entity_id)
                ).id,
            }
        )
        for item in graph.entities
    )
    relations = tuple(
        LegacyRelation.model_validate(
            item.model_dump()
            | {
                "id": stable(
                    mapping.target, graph.origin, "support", item.source_id, item.legacy_id
                ),
                "assertion_id": mapping.assertion_ref(
                    ScopedRef(**mapping.origin.model_dump(), id=item.assertion_id)
                ).id,
                "subject_id": mapping.entity_ref(
                    ScopedRef(**mapping.origin.model_dump(), id=item.subject_id)
                ).id,
                "target_id": mapping.entity_ref(
                    ScopedRef(**mapping.origin.model_dump(), id=item.target_id)
                ).id,
            }
        )
        for item in graph.relations
    )
    new_graph = parse_legacy(mapping.target, plan.source.format, raw, origin=graph.origin)
    if set(new_graph.entities) != set(entities) or set(new_graph.relations) != set(relations):
        raise DomainError(
            ErrorCode.PROJECTION_INCONSISTENT, "Restore legacy identity mapping differs."
        )
    new_plan = ImportPlan.model_validate(
        plan.model_dump()
        | mapping.target.model_dump()
        | {
            "source": ImportSource.model_validate(
                plan.source.model_dump() | {"kg_id": mapping.target.kg_id}
            )
        }
    )
    new_origin = ImportOrigin.model_validate(
        origin.model_dump()
        | source.model_dump()
        | {"plan_hash": checksum(new_plan), "graph_hash": checksum(new_graph)}
    )
    return RestoredImport(original=origin, plan=new_plan, graph=new_graph, origin=new_origin)
