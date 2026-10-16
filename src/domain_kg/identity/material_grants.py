"""Typed owner-only material ACL management over the shared policy authority."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, model_validator
from pymongo.asynchronous.client_session import AsyncClientSession

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference
from domain_kg.identity.grant_models import PolicyReceipt
from domain_kg.identity.models import AccessContext, Action
from domain_kg.identity.policy import Policy
from domain_kg.models import FrozenModel, IdempotencyKey, NonNegative


class MaterialACLView(FrozenModel):
    kg_id: UUID
    material_id: UUID
    acl_mode: Literal["kg", "restricted"]
    member_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    group_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    policy_revision: NonNegative


class MaterialACLChange(FrozenModel):
    kg_id: UUID
    material_id: UUID
    acl_mode: Literal["kg", "restricted"]
    member_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    group_ids: Annotated[tuple[UUID, ...], Field(max_length=100)] = ()
    expected_revision: NonNegative
    idempotency_key: IdempotencyKey

    @model_validator(mode="after")
    def unique_grantees(self) -> Self:
        if len(set(self.member_ids)) != len(self.member_ids) or len(set(self.group_ids)) != len(
            self.group_ids
        ):
            raise ValueError("material ACL grantees must be unique")
        return self


class MaterialGrantManagement:
    def __init__(self, policy: Policy) -> None:
        self.policy, self.database = policy, policy.database

    async def get(self, access: AccessContext, kg: UUID, material: UUID) -> MaterialACLView:
        current = await self.policy.revalidate(access, action=Action.MANAGE_GRANTS)
        row = await self.policy._material_acl_record(current, kg, material)
        if row is None:
            raise inaccessible_reference()
        result = MaterialACLView(
            kg_id=kg,
            material_id=material,
            acl_mode=row["acl_mode"],
            member_ids=tuple(UUID(value) for value in row.get("member_ids", [])),
            group_ids=tuple(UUID(value) for value in row.get("group_ids", [])),
            policy_revision=current.policy_revision,
        )
        # Validate every subject before exposing even an opaque foreign reference.
        for identifiers, collection in (
            (result.member_ids, self.database.members),
            (result.group_ids, self.database.groups),
        ):
            for identifier in identifiers:
                if (
                    await collection.find_one(
                        {"org_id": str(current.org_id), "id": str(identifier)}
                    )
                    is None
                ):
                    raise inaccessible_reference()
        await self.policy.revalidate(current, action=Action.MANAGE_GRANTS)
        return result

    async def set(self, access: AccessContext, command: MaterialACLChange) -> PolicyReceipt:
        command = MaterialACLChange.model_validate(command)
        current = await self.policy.revalidate(access, action=Action.MANAGE_GRANTS)

        async def change(session: AsyncClientSession) -> None:
            if current.policy_revision != command.expected_revision:
                raise DomainError(ErrorCode.VERSION_CONFLICT, "The material ACL revision changed.")
            if (
                await self.policy._material_acl_record(
                    current, command.kg_id, command.material_id, session=session
                )
                is None
            ):
                raise inaccessible_reference()
            for identifiers, collection in (
                (command.member_ids, self.database.members),
                (command.group_ids, self.database.groups),
            ):
                for identifier in identifiers:
                    if (
                        await collection.find_one(
                            {"org_id": str(current.org_id), "id": str(identifier)}, session=session
                        )
                        is None
                    ):
                        raise inaccessible_reference()
            await self.database.material_access.update_one(
                {
                    "org_id": str(current.org_id),
                    "kg_id": str(command.kg_id),
                    "id": str(command.material_id),
                },
                {
                    "$set": {
                        "acl_mode": command.acl_mode,
                        "member_ids": [str(value) for value in command.member_ids],
                        "group_ids": [str(value) for value in command.group_ids],
                    }
                },
                session=session,
            )

        await self.policy._mutation(
            current,
            command.kg_id,
            command.idempotency_key,
            "material_acl_command",
            command.model_dump(mode="json", exclude={"kg_id", "idempotency_key"}),
            change,
        )
        await self.policy.resolve_access(
            current.principal, [command.kg_id], Action.MANAGE_GRANTS, org_id=current.org_id
        )
        return PolicyReceipt(policy_revision=command.expected_revision + 1)
