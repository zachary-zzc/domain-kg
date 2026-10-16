"""Typed scoped reads with current access checks before and after storage I/O."""

from typing import Any
from uuid import UUID

from pymongo.asynchronous.database import AsyncDatabase

from domain_kg.errors import inaccessible_reference
from domain_kg.identity.models import AccessContext
from domain_kg.identity.policy import Policy, uuid_value
from domain_kg.storage.models import MaterialRecord


class ScopedMongoRepository:
    def __init__(
        self,
        database: AsyncDatabase[dict[str, Any]],
        policy: Policy,
        access: AccessContext,
        kg_id: UUID,
    ) -> None:
        self._database, self._policy, self._access = database, policy, access
        self.kg_id = uuid_value(kg_id)
        if kg_id not in access.kg_ids:
            raise inaccessible_reference()

    async def get_material(self, material_id: UUID) -> MaterialRecord:
        material_id = uuid_value(material_id)
        if not await self._policy.material_visible(self._access, self.kg_id, material_id):
            raise inaccessible_reference()
        raw = await self._database.materials.find_one(
            {"org_id": str(self._access.org_id), "kg_id": str(self.kg_id), "id": str(material_id)}
        )
        if raw is None:
            raise inaccessible_reference()
        record = MaterialRecord.model_validate(
            {key: value for key, value in raw.items() if key != "_id"}
        )
        await self._policy.revalidate(self._access)
        if not await self._policy.material_visible(self._access, self.kg_id, material_id):
            raise inaccessible_reference()
        return record
