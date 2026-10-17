"""Owner-selected approved profiles with current material policy at every call."""

from typing import TYPE_CHECKING, Any
from uuid import UUID

import httpx
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.graph.models import DomainSchema
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.ingestion.extraction import Extractor
from domain_kg.ingestion.models import ExtractionBatch, ParsedChunk
from domain_kg.materials.models import ReprocessReadPermit
from domain_kg.materials.repository import MaterialRepository
from domain_kg.models import IdempotencyKey, Namespace, NonNegative, SourceVersion
from domain_kg.providers.budget_store import ProviderBudgets
from domain_kg.providers.call_budget import CallBudget
from domain_kg.providers.extraction import ExtractionClient, Usage
from domain_kg.providers.profiles import ProviderRegistry, embedding_space_id
from domain_kg.providers.usage_budget import BudgetBounds
from domain_kg.storage.router import KGStoreRouter

if TYPE_CHECKING:
    from domain_kg.jobs.models import LeasedJob
    from domain_kg.providers.embeddings import EmbeddingProfile


class ProviderService:
    def __init__(
        self,
        policy: Policy,
        router: KGStoreRouter,
        registry: ProviderRegistry,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.policy, self.router, self.registry = policy, router, registry
        self.database, self.transactions = policy.database, MaterialRepository(policy)
        self.transport = transport
        self.budgets = ProviderBudgets(policy, registry.budgets)

    async def bind(
        self,
        access: AccessContext,
        kg: UUID,
        profile_id: UUID,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> int:
        from pydantic import TypeAdapter

        key = TypeAdapter(IdempotencyKey).validate_python(idempotency_key)
        TypeAdapter(NonNegative).validate_python(expected_revision)
        current = await self.policy.revalidate(access, action=Action.CONFIGURE)
        if kg not in current.kg_ids:
            raise inaccessible_reference()
        namespace = Namespace(org_id=current.org_id, kg_id=kg)
        profile, embedding_connection = self.registry.resolve_embedding(
            profile_id, namespace, restricted=False
        )
        space_id = embedding_space_id(profile, embedding_connection)
        if profile.embedding_dimensions != self.router.dimensions:
            raise DomainError(
                ErrorCode.VERSION_CONFLICT, "Embedding dimensions require a new generation."
            )
        query = {"org_id": str(current.org_id), "kg_id": str(kg)}
        await self.database.kg_provider_settings.create_index(
            [("org_id", 1), ("kg_id", 1)], unique=True
        )
        await self.database.provider_commands.create_index(
            [("org_id", 1), ("kg_id", 1), ("issuer", 1), ("subject", 1), ("key", 1)],
            unique=True,
        )
        command_key = self.transactions.key(current, kg, key)
        intent = {
            "profile_id": str(profile.id),
            "profile_revision": profile.revision,
            "expected_revision": expected_revision,
            "embedding_space_id": space_id,
        }

        async def commit(session: AsyncClientSession) -> int:
            await self.transactions.fence(current, Action.CONFIGURE, session)
            previous = await self.database.provider_commands.find_one(command_key, session=session)
            if previous:
                if previous["intent"] != intent:
                    raise DomainError(
                        ErrorCode.IDEMPOTENCY_CONFLICT, "Provider key has different input."
                    )
                return int(previous["revision"])
            old = await self.database.kg_provider_settings.find_one(query, session=session)
            revision = old["revision"] if old else 0
            if revision != expected_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Provider selection changed.")
            kg_row = await self.database.kgs.find_one(
                {"org_id": str(current.org_id), "id": str(kg)}, session=session
            )
            if kg_row is None:
                raise inaccessible_reference()
            if old and kg_row["published_revision"] and old.get("embedding_space_id") != space_id:
                raise DomainError(
                    ErrorCode.VERSION_CONFLICT, "Embedding changes require a new generation."
                )
            await self.database.kg_provider_settings.update_one(
                query,
                {
                    "$set": {
                        "profile_id": str(profile.id),
                        "profile_revision": profile.revision,
                        "embedding_model": profile.embedding_model,
                        "embedding_dimensions": profile.embedding_dimensions,
                        "embedding_space_id": space_id,
                        "revision": revision + 1,
                    }
                },
                upsert=True,
                session=session,
            )
            await self.database.kgs.update_one(
                {"org_id": str(current.org_id), "id": str(kg)},
                {"$inc": {"configuration_epoch": 1}},
                session=session,
            )
            await self.database.provider_commands.insert_one(
                command_key | {"intent": intent, "revision": revision + 1}, session=session
            )
            await self.database.policy_audit.insert_one(
                command_key
                | {
                    "action": "provider_selection",
                    "profile_id": str(profile.id),
                    "revision": revision + 1,
                },
                session=session,
            )
            return int(revision + 1)

        return await self.transactions.transaction(commit)

    async def selected(self, access: AccessContext, kg: UUID) -> dict[str, Any]:
        await self.policy.revalidate(access)
        if kg not in access.kg_ids:
            raise inaccessible_reference()
        selected = await self.database.kg_provider_settings.find_one(
            {"org_id": str(access.org_id), "kg_id": str(kg)}
        )
        if selected is None:
            raise DomainError(
                ErrorCode.PROVIDER_UNAVAILABLE, "Select an approved provider profile."
            )
        return dict(selected)

    async def embeddings(
        self,
        access: AccessContext,
        namespace: Namespace,
        texts: list[str],
        *,
        source: SourceVersion | None = None,
        curation: "LeasedJob | None" = None,
        restoration: ReprocessReadPermit | None = None,
    ) -> tuple["EmbeddingProfile", list[list[float]]]:
        from domain_kg.providers.embeddings import EmbeddingClient, EmbeddingProfile

        selected = await self.selected(access, namespace.kg_id)
        if (source is not None and curation is not None) or (
            restoration is not None and source is None
        ):
            raise inaccessible_reference()

        async def authorize() -> None:
            current = await self.policy.revalidate(access)
            if namespace.org_id != current.org_id or namespace.kg_id not in current.kg_ids:
                raise inaccessible_reference()
            if await self.selected(current, namespace.kg_id) != selected:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Provider selection changed.")
            restricted = False
            if curation is not None:
                from domain_kg.jobs.queue import JobQueue

                if curation.kind != "curate" or (curation.org_id, curation.kg_id) != (
                    namespace.org_id,
                    namespace.kg_id,
                ):
                    raise inaccessible_reference()
                queue = JobQueue(self.policy, self.router)
                actor = await queue.authorize(curation)
                if actor.principal != current.principal:
                    raise inaccessible_reference()
                record = await self.database.curation_revisions.find_one(
                    {
                        "org_id": str(namespace.org_id),
                        "kg_id": str(namespace.kg_id),
                        "id": str(curation.target_id),
                        "job_id": str(curation.id),
                    }
                )
                if record is None:
                    raise inaccessible_reference()
                for material_id in record["material_ids"]:
                    material = await self.database.material_access.find_one(
                        {
                            "org_id": str(namespace.org_id),
                            "kg_id": str(namespace.kg_id),
                            "id": material_id,
                        }
                    )
                    if material is None:
                        raise inaccessible_reference()
                    restricted |= material["acl_mode"] == "restricted"
            elif source is not None:
                if (source.org_id, source.kg_id) != (namespace.org_id, namespace.kg_id):
                    raise inaccessible_reference()
                await self._authorize_read(current, source, restoration)
                material = await self.database.material_access.find_one(
                    {
                        "org_id": str(source.org_id),
                        "kg_id": str(source.kg_id),
                        "id": str(source.material_id),
                    }
                )
                if material is None:
                    raise inaccessible_reference()
                restricted = material["acl_mode"] == "restricted"
            elif current.action not in (Action.READ, Action.SEARCH):
                raise inaccessible_reference()
            configured, embedding_connection = self.registry.resolve_embedding(
                UUID(selected["profile_id"]), namespace, restricted=restricted
            )
            if configured.revision != selected["profile_revision"] or embedding_space_id(
                configured, embedding_connection
            ) != selected.get("embedding_space_id"):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Provider configuration changed.")

        async def usage(value: Usage) -> None:
            await self.database.provider_usage.update_one(
                {
                    "_id": f"{value.org_id}:{value.kg_id}:{value.profile_id}",
                    "org_id": str(value.org_id),
                    "kg_id": str(value.kg_id),
                },
                {
                    "$inc": {
                        "input_tokens": value.input_tokens,
                        "embedding_input_tokens": value.input_tokens,
                        "output_tokens": 0,
                        "calls": 1,
                        "embedding_calls": 1,
                    }
                },
                upsert=True,
            )

        await authorize()
        configured, connection = self.registry.resolve_embedding(
            UUID(selected["profile_id"]), namespace, restricted=False
        )
        profile = EmbeddingProfile(
            **namespace.model_dump(),
            id=configured.id,
            revision=configured.revision,
            model=configured.embedding_model,
            dimensions=configured.embedding_dimensions,
            space_id=selected["embedding_space_id"],
        )
        client = EmbeddingClient(
            configured,
            connection,
            authorize=authorize,
            record_usage=usage,
            transport=self.transport,
            budget=CallBudget(
                self.budgets,
                namespace,
                profile.id,
                BudgetBounds(
                    input_tokens=min(
                        128000,
                        configured.embedding_context_tokens * configured.embedding_batch_size,
                    )
                ),
                authorize=authorize,
            ),
        )
        try:
            return profile, await client.embed(
                texts, profile, query=source is None and curation is None
            )
        finally:
            await client.close()

    async def _authorize_read(
        self,
        access: AccessContext,
        source: SourceVersion,
        restoration: ReprocessReadPermit | None,
    ) -> None:
        if restoration is not None:
            from domain_kg.jobs.queue import JobQueue
            from domain_kg.materials.restoration import authorize_restore_read

            await authorize_restore_read(
                JobQueue(self.policy, self.router), access, source, restoration
            )
        elif not await self.policy.material_visible(access, source.kg_id, source.material_id):
            raise inaccessible_reference()

    async def authorize_source(
        self,
        access: AccessContext,
        source: SourceVersion,
        *,
        restoration: ReprocessReadPermit | None = None,
    ) -> None:
        """Apply current provider clearance even when accepted artifacts avoid a call."""
        current = await self.policy.revalidate(access)
        if source.org_id != current.org_id or source.kg_id not in current.kg_ids:
            raise inaccessible_reference()
        await self._authorize_read(current, source, restoration)
        selected = await self.selected(current, source.kg_id)
        material = await self.database.material_access.find_one(
            {
                "org_id": str(source.org_id),
                "kg_id": str(source.kg_id),
                "id": str(source.material_id),
            }
        )
        if material is None:
            raise inaccessible_reference()
        configured, connection = self.registry.resolve_embedding(
            UUID(selected["profile_id"]),
            Namespace(org_id=source.org_id, kg_id=source.kg_id),
            restricted=material["acl_mode"] == "restricted",
        )
        if configured.revision != selected["profile_revision"] or embedding_space_id(
            configured, connection
        ) != selected.get("embedding_space_id"):
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Provider configuration changed.")

    async def extract(
        self,
        access: AccessContext,
        chunk: ParsedChunk,
        schema: DomainSchema,
        *,
        restoration: ReprocessReadPermit | None = None,
    ) -> ExtractionBatch:
        current = await self.policy.revalidate(access, action=Action.INGEST)
        selected = await self.selected(current, chunk.kg_id)
        namespace = Namespace(org_id=chunk.org_id, kg_id=chunk.kg_id)
        if chunk.org_id != current.org_id:
            raise inaccessible_reference()

        async def authorize(item: ParsedChunk) -> None:
            await self.policy.revalidate(current, action=Action.INGEST)
            await self._authorize_read(
                current,
                SourceVersion(**{key: getattr(item, key) for key in SourceVersion.model_fields}),
                restoration,
            )
            if await self.selected(current, item.kg_id) != selected:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Provider selection changed.")
            material = await self.database.material_access.find_one(
                {"org_id": str(item.org_id), "kg_id": str(item.kg_id), "id": str(item.material_id)}
            )
            if material is None:
                raise inaccessible_reference()
            profile, _ = self.registry.resolve(
                UUID(selected["profile_id"]),
                namespace,
                restricted=material["acl_mode"] == "restricted",
            )
            if profile.revision != selected["profile_revision"]:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Provider configuration changed.")

        async def usage(value: Usage) -> None:
            await self.database.provider_usage.update_one(
                {
                    "org_id": str(value.org_id),
                    "kg_id": str(value.kg_id),
                    "_id": f"{value.org_id}:{value.kg_id}:{value.profile_id}",
                },
                {
                    "$inc": {
                        "input_tokens": value.input_tokens,
                        "output_tokens": value.output_tokens,
                        "calls": 1,
                    }
                },
                upsert=True,
            )

        await authorize(chunk)
        profile, connection = self.registry.resolve(
            UUID(selected["profile_id"]), namespace, restricted=False
        )

        async def budget_authorize() -> None:
            await authorize(chunk)

        client = ExtractionClient(
            profile,
            connection,
            authorize=authorize,
            record_usage=usage,
            transport=self.transport,
            budget=CallBudget(
                self.budgets,
                namespace,
                profile.id,
                BudgetBounds(
                    input_tokens=profile.extraction_context_tokens,
                    output_tokens=profile.max_completion_tokens,
                ),
                authorize=budget_authorize,
            ),
        )
        try:
            return await Extractor(client).extract(chunk, schema)
        finally:
            await client.close()
