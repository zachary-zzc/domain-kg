"""Only manifest-bound retained objects can be streamed to a current KG owner."""

from collections.abc import AsyncGenerator
from uuid import UUID

from domain_kg.errors import inaccessible_reference
from domain_kg.identity.models import AccessContext, Action
from domain_kg.operations.backup import BackupService


class BackupDownloads:
    def __init__(self, service: BackupService) -> None:
        self.service = service

    async def stream(
        self, access: AccessContext, kg: UUID, backup: UUID, index: int, identifier: UUID
    ) -> AsyncGenerator[bytes, None]:
        current, _, store = await self.service._manifest(access, kg, backup, by_job=False)
        page = await self.service.parts(current, kg, backup, index)
        reference = next((part.object for part in page.parts if part.object.id == identifier), None)
        if reference is None:
            raise inaccessible_reference()
        async for chunk in store.open(reference):
            await self.service.queue.policy.revalidate(current, action=Action.BACKUP)
            yield chunk
        await self.service.queue.policy.revalidate(current, action=Action.BACKUP)
