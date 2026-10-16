"""Real authority checks; roles and request claims never substitute for grants."""

import asyncio
from uuid import uuid4

import pytest

from domain_kg.errors import DomainError
from domain_kg.identity.models import Action, Principal

pytestmark = pytest.mark.storage


async def test_revocation_between_member_and_policy_reads_is_denied(team, monkeypatch) -> None:
    from pymongo.asynchronous.collection import AsyncCollection

    identities, policy, principals, members, org, kg = team
    original = AsyncCollection.find_one
    raced = False

    async def interleaved(collection, *args, **kwargs):
        nonlocal raced
        if collection.name == "organizations" and not raced:
            raced = True
            await identities.change_member(
                members["owner"],
                members["reader"].id,
                expected_revision=0,
                suspended=True,
                idempotency_key="race",
            )
        return await original(collection, *args, **kwargs)

    monkeypatch.setattr(AsyncCollection, "find_one", interleaved)
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["reader"], [kg], Action.READ)


async def test_roles_admin_operator_scopes_and_organization_crossover(team, mongo_db) -> None:
    identities, policy, principals, members, org, kg = team
    for name in ("owner", "editor", "reader"):
        access = await policy.resolve_access(principals[name], [kg], Action.READ)
        assert access.org_id == org and access.kg_ids == (kg,)
    for name in ("owner", "editor"):
        assert (
            await policy.resolve_access(principals[name], [kg], Action.INGEST)
        ).action == Action.INGEST
    for name, action in [
        ("reader", Action.INGEST),
        ("editor", Action.MANAGE_GRANTS),
        ("denied", Action.READ),
    ]:
        with pytest.raises(DomainError):
            await policy.resolve_access(principals[name], [kg], action)
    # Organization admin and platform operator do not inherit KG data access.
    await identities.change_member(
        members["owner"],
        members["denied"].id,
        expected_revision=0,
        role="admin",
        idempotency_key="make-admin",
    )
    await mongo_db.platform_operators.insert_one(
        {
            "issuer": principals["denied"].issuer,
            "subject": principals["denied"].subject,
            "active": True,
        }
    )
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["denied"], [kg], Action.READ)
    assert (
        await policy.resolve_access(principals["denied"], [], Action.REGISTER_BACKEND)
    ).platform_operator
    foreign_org, _ = await identities.bootstrap_organization(
        Principal(issuer="https://idp.example", subject="foreign"), "Other"
    )
    foreign_kg = uuid4()
    await mongo_db.kgs.insert_one(
        {"id": str(foreign_kg), "org_id": str(foreign_org), "state": "ready"}
    )
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["owner"], [kg, foreign_kg], Action.READ)
    read_only = principals["owner"].model_copy(update={"scopes": frozenset({"domain_kg:read"})})
    with pytest.raises(DomainError):
        await policy.resolve_access(read_only, [kg], Action.INGEST)


async def test_group_grants_service_revocation_and_last_owner_races(team, mongo_db) -> None:
    identities, policy, principals, members, org, kg = team
    group = await identities.create_group(members["owner"], "Readers", idempotency_key="group")
    await identities.set_group_member(
        members["owner"], group.id, members["denied"].id, present=True, idempotency_key="join"
    )
    owner_access = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_grant(
        owner_access, kg, "group", group.id, "reader", idempotency_key="group-grant"
    )
    assert (
        await policy.resolve_access(principals["denied"], [kg], Action.READ)
    ).groups == frozenset({group.id})
    await identities.set_group_member(
        members["owner"], group.id, members["denied"].id, present=False, idempotency_key="leave"
    )
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["denied"], [kg], Action.READ)
    service = await identities.admit(
        members["owner"],
        Principal(issuer="https://idp.example", subject="service"),
        role="member",
        kind="service",
        kg_ids=(kg,),
        scopes=("domain_kg:read",),
        idempotency_key="service",
    )
    owner_access = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_grant(
        owner_access, kg, "member", service.id, "reader", idempotency_key="service-grant"
    )
    service_principal = Principal(
        issuer=service.issuer, subject=service.subject, scopes={"domain_kg:read", "domain_kg:write"}
    )
    assert (
        await policy.resolve_access(service_principal, [kg], Action.READ)
    ).principal.kind == "service"
    await identities.change_member(
        members["owner"],
        service.id,
        expected_revision=0,
        suspended=True,
        idempotency_key="revoke-service",
    )
    with pytest.raises(DomainError):
        await policy.resolve_access(service_principal, [kg], Action.READ)
    owner_access = await policy.resolve_access(principals["owner"], [kg], Action.MANAGE_GRANTS)
    await policy.set_grant(
        owner_access, kg, "member", members["editor"].id, "owner", idempotency_key="second-owner"
    )
    owners = [
        await policy.resolve_access(principals[name], [kg], Action.MANAGE_GRANTS)
        for name in ("owner", "editor")
    ]
    results = await asyncio.gather(
        *[
            policy.set_grant(
                access, kg, "member", members[name].id, None, idempotency_key=str(uuid4())
            )
            for access, name in zip(owners, ("owner", "editor"), strict=True)
        ],
        return_exceptions=True,
    )
    assert sum(isinstance(result, DomainError) for result in results) == 1
    assert await mongo_db.kg_grants.count_documents({"kg_id": str(kg), "role": "owner"}) == 1


async def test_policy_recheck_and_authority_failure_fail_closed(team, mongo_db) -> None:
    identities, policy, principals, members, org, kg = team
    access = await policy.resolve_access(principals["reader"], [kg], Action.READ)
    await identities.change_member(
        members["owner"],
        members["reader"].id,
        expected_revision=0,
        suspended=True,
        idempotency_key="suspend",
    )
    with pytest.raises(DomainError):
        await policy.revalidate(access)
    await mongo_db.organizations.delete_one({"_id": str(org)})
    with pytest.raises(DomainError):
        await policy.resolve_access(principals["owner"], [kg], Action.READ)


async def test_local_suspension_cannot_remove_last_kg_owner(team) -> None:
    identities, policy, principals, members, org, kg = team
    admin = await identities.change_member(
        members["owner"],
        members["denied"].id,
        expected_revision=0,
        role="admin",
        idempotency_key="admin",
    )
    with pytest.raises(DomainError):
        await identities.change_member(
            admin,
            members["owner"].id,
            expected_revision=0,
            suspended=True,
            idempotency_key="suspend-last-owner",
        )
    assert (await policy.resolve_access(principals["owner"], [kg], Action.READ)).grants[
        0
    ].role == "owner"


async def test_interactive_session_scopes_still_require_current_roles(team, mongo_db) -> None:
    from domain_kg.identity.sessions import SessionStore

    identities, policy, principals, members, org, kg = team
    sessions = SessionStore(mongo_db, identities)
    owner = await sessions.create(principals["owner"], org)
    reader = await sessions.create(principals["reader"], org)
    authenticated = await sessions.authenticate_session(owner.session_id)
    assert (await policy.resolve_access(authenticated, [], Action.CREATE_KG)).organization_admin
    with pytest.raises(DomainError):
        await policy.resolve_access(
            await sessions.authenticate_session(reader.session_id), [kg], Action.INGEST
        )
