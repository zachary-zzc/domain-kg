"""Durable import planning; admission and publication remain separate operations."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid5

from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.schema import schema_for_kg
from domain_kg.identity.models import AccessContext, Action
from domain_kg.imports.legacy import MAX_BYTES, parse_legacy
from domain_kg.imports.models import ImportPlan, ImportSource
from domain_kg.materials.models import BlobRef
from domain_kg.materials.service import MaterialService
from domain_kg.models import Namespace
from domain_kg.storage.blobs import BlobStore


class Importer:
    def __init__(self, materials: MaterialService) -> None:
        self.materials, self.policy = materials, materials.policy
        self.database, self.transactions = materials.database, materials.repository

    async def initialize(self) -> None:
        await self.database.import_source_origins.create_index(
            [("org_id", 1), ("kg_id", 1), ("material_id", 1), ("version_id", 1)], unique=True
        )
        await self.database.import_plans.create_index(
            [("org_id", 1), ("kg_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)], unique=True
        )
        await self.database.import_plans.create_index(
            [("org_id", 1), ("kg_id", 1), ("id", 1)], unique=True
        )

    async def plan(self, access: AccessContext, source: ImportSource) -> ImportPlan:
        source = ImportSource.model_validate(source)
        current = await self.policy.revalidate(access, action=Action.INGEST)
        if source.kg_id not in current.kg_ids:
            raise inaccessible_reference()
        namespace = Namespace(org_id=current.org_id, kg_id=source.kg_id)
        ns = namespace.model_dump(mode="json")
        upload_query = ns | {
            "id": str(source.upload_id),
            "issuer": current.principal.issuer,
            "subject": current.principal.subject,
            "state": "complete",
            "target_material_id": None,
        }
        upload = await self.database.uploads.find_one(
            upload_query | {"expires_at": {"$gt": datetime.now(UTC)}}
        )
        if upload is None:
            raise inaccessible_reference()
        blob = BlobRef.model_validate(upload["blob"])
        if any(
            str(getattr(blob, key)) != upload[key]
            for key in ("org_id", "kg_id", "material_id", "version_id", "object_id")
        ) or any(
            getattr(blob, key) != upload[key] for key in ("sha256", "size_bytes", "media_type")
        ):
            raise inaccessible_reference()
        if blob.size_bytes > MAX_BYTES:
            raise DomainError(ErrorCode.QUOTA_EXCEEDED, "The import exceeds its byte budget.")
        stores = await self.materials.router.for_kg(current, source.kg_id)
        parts = []
        async for part in BlobStore(stores.blobs).open(blob):
            parts.append(part)
        graph = parse_legacy(
            namespace, source.format, b"".join(parts), origin=source.origin or blob.sha256
        )
        schema = await schema_for_kg(self.policy, current, source.kg_id)
        predicates = {predicate.name: predicate for predicate in schema.predicates}
        entities = {entity.entity_id: entity for entity in graph.entities}
        if any(entity.entity_type not in schema.entity_types for entity in graph.entities) or any(
            relation.predicate not in predicates
            or entities[relation.subject_id].entity_type
            not in predicates[relation.predicate].subject_types
            or entities[relation.target_id].entity_type
            not in predicates[relation.predicate].target_types
            for relation in graph.relations
        ):
            raise DomainError(
                ErrorCode.EVIDENCE_VALIDATION_FAILED,
                "Legacy records do not match the selected domain schema.",
            )
        kg = await self.database.kgs.find_one({"org_id": ns["org_id"], "id": ns["kg_id"]})
        if kg is None:
            raise inaccessible_reference()
        binding = {
            "policy_revision": current.policy_revision,
            "published_revision": kg["published_revision"],
            "kg_revision": kg["revision"],
            "visibility_revision": kg["visibility_revision"],
            "generation": kg["generation"],
            "manifest_id": kg.get("published_manifest_id"),
            "configuration_epoch": kg.get("configuration_epoch", 0),
            "schema": schema.model_dump(mode="json"),
            "blob": blob.model_dump(mode="json"),
            "principal_kind": current.principal.kind,
        }
        key = self.transactions.key(current, source.kg_id, source.idempotency_key)
        intent = hashlib.sha256(
            json.dumps(source.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest()
        plan_id = uuid5(current.org_id, "import-plan/" + json.dumps(key, sort_keys=True))
        expiry = min(
            upload["expires_at"].replace(tzinfo=UTC), datetime.now(UTC) + timedelta(hours=1)
        )
        expiry = expiry.replace(microsecond=expiry.microsecond // 1000 * 1000)
        result = ImportPlan(
            **namespace.model_dump(),
            id=plan_id,
            source=source,
            content_hash=blob.sha256,
            entities=len(graph.entities),
            assertions=len(graph.relations),
            warnings=graph.warnings,
            policy_revision=current.policy_revision,
            published_revision=kg["published_revision"],
            kg_revision=kg["revision"],
            visibility_revision=kg["visibility_revision"],
            schema_id=schema.id,
            schema_revision=schema.revision,
            expires_at=expiry,
        )

        async def seal(session: AsyncClientSession) -> ImportPlan:
            await self.transactions.fence(current, Action.INGEST, session)
            old = await self.database.import_plans.find_one(key, session=session)
            if old is not None:
                if old["intent"] != intent:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "The import key has different input."
                    )
            current_upload = await self.database.uploads.find_one(
                upload_query
                | {
                    "expires_at": {"$gt": datetime.now(UTC)},
                    "blob": upload["blob"],
                    "material_id": str(blob.material_id),
                    "version_id": str(blob.version_id),
                    "object_id": str(blob.object_id),
                    "sha256": blob.sha256,
                    "size_bytes": blob.size_bytes,
                    "media_type": blob.media_type,
                },
                session=session,
            )
            latest = await self.database.kgs.find_one(
                {"org_id": ns["org_id"], "id": ns["kg_id"]}, session=session
            )
            schema_now = await schema_for_kg(self.policy, current, source.kg_id, session=session)
            if (
                current_upload is None
                or latest is None
                or schema_now != schema
                or any(
                    latest.get(field, 0) != kg.get(field, 0)
                    for field in (
                        "published_revision",
                        "revision",
                        "visibility_revision",
                        "generation",
                        "published_manifest_id",
                        "configuration_epoch",
                    )
                )
            ):
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "The import scope changed during validation."
                )
            graph_hash = hashlib.sha256(graph.model_dump_json().encode()).hexdigest()
            if old is not None:
                try:
                    previous = ImportPlan.model_validate(old["plan"])
                except (ValidationError, KeyError, TypeError):
                    raise DomainError(
                        ErrorCode.EVIDENCE_VALIDATION_FAILED, "The sealed import plan differs."
                    ) from None
                if (
                    old.get("binding") != binding
                    or previous.expires_at is None
                    or previous.expires_at <= datetime.now(UTC)
                ):
                    raise DomainError(
                        ErrorCode.VERSION_CONFLICT, "The validated import scope changed."
                    )
                if (
                    old.get("graph") != graph.model_dump(mode="json")
                    or old.get("graph_hash") != graph_hash
                    or old.get("plan_hash")
                    != hashlib.sha256(previous.model_dump_json().encode()).hexdigest()
                    or previous.model_dump(exclude={"expires_at"})
                    != result.model_dump(exclude={"expires_at"})
                ):
                    raise DomainError(
                        ErrorCode.EVIDENCE_VALIDATION_FAILED, "The sealed import plan differs."
                    )
                return previous
            record: dict[str, Any] = key | {
                "id": str(plan_id),
                "intent": intent,
                "binding": binding,
                "plan": result.model_dump(mode="json"),
                "plan_hash": hashlib.sha256(result.model_dump_json().encode()).hexdigest(),
                "state": "validated",
                "graph": graph.model_dump(mode="json"),
                "graph_hash": graph_hash,
            }
            await self.database.import_plans.insert_one(record, session=session)
            return result

        sealed = await self.transactions.transaction(seal)
        await self.policy.revalidate(current, action=Action.INGEST)
        return sealed
