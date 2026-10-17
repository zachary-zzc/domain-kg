"""Fresh embeddings for an approved inactive target under its real restore lease."""

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.jobs.models import LeasedJob
from domain_kg.jobs.queue import JobQueue
from domain_kg.operations.restore_stores import RestoreBinding, RestoreStores
from domain_kg.providers.embeddings import EmbeddingClient
from domain_kg.providers.extraction import Usage
from domain_kg.providers.service import ProviderService


class RestoreEmbeddings:
    def __init__(self, queue: JobQueue, providers: ProviderService) -> None:
        self.queue, self.providers = queue, providers
        self.bindings = RestoreStores(queue, providers.registry)

    async def embed(
        self, job: LeasedJob, binding: RestoreBinding, texts: list[str]
    ) -> tuple[tuple[float, ...], ...]:
        expected = binding.target, binding.embedding
        if await self.bindings.approved(job) != expected:
            raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore embedding approval changed.")
        profile, connection = self.providers.registry.resolve_embedding(
            binding.embedding.id, binding.target.target, restricted=True
        )

        async def authorize() -> None:
            if await self.bindings.approved(job) != expected or (
                self.providers.registry.resolve_embedding(
                    binding.embedding.id, binding.target.target, restricted=True
                )
                != (profile, connection)
            ):
                raise DomainError(ErrorCode.VERSION_CONFLICT, "Restore embedding approval changed.")

        async def usage(value: Usage) -> None:
            if (value.org_id, value.kg_id, value.profile_id) != (
                binding.embedding.org_id,
                binding.embedding.kg_id,
                binding.embedding.id,
            ):
                raise DomainError(ErrorCode.PROJECTION_INCONSISTENT, "Restore usage scope differs.")
            await self.queue.database.provider_usage.update_one(
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

        client = EmbeddingClient(
            profile,
            connection,
            authorize=authorize,
            record_usage=usage,
            transport=self.providers.transport,
        )
        try:
            vectors = await client.embed(texts, binding.embedding)
            await authorize()
            return tuple(tuple(vector) for vector in vectors)
        finally:
            await client.close()
