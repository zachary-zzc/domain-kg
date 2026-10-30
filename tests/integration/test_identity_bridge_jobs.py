"""Jobs retain original delegation beyond JWT expiry and cannot bypass revoked connections."""

# ruff: noqa: F811
from datetime import UTC, datetime, timedelta

import pytest
from test_identity import issuer_fixture as issuer_fixture
from test_identity_bridge import bridge as bridge
from test_publication import make_publishing, prepare, ready

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import Action, Principal
from domain_kg.revisions.journal import PublicationJournal

pytestmark = pytest.mark.storage


async def test_delegation_survives_token_expiry_and_revocation_fences_publication(
    bridge,
    material_service,
    team,
    mongo_db,
):
    async with bridge() as (http, app, boot, exchange, _, _, _, _):
        assert (await boot()).status_code == 200
        publishing = await make_publishing(material_service, team)
        token = await exchange(actions=["read", "search", "ingest"])
        assert token.status_code == 200, token.text
        identity = app.state.identity
        principal = await identity.authenticate_bearer(token.json()["access_token"])
        materials, _, queue, coordinator, steps, faults = publishing
        access = await queue.policy.resolve_access(principal, [material_service[1]], Action.INGEST)
        publishing = materials, access, queue, coordinator, steps, faults
        receipt, job, checkpoint = await prepare(publishing)
        raw = await mongo_db.jobs.find_one({"id": str(job.id)})
        assert Principal.model_validate(raw["actor"]).delegation == principal.delegation
        # Workers use the accepted connection, not the request token's TTL/binding row.
        await mongo_db.mcp_token_bindings.update_one(
            {"connection_id": token.json()["connection_id"]},
            {"$set": {"expires_at": datetime.now(UTC) - timedelta(minutes=1)}},
        )
        assert (await queue.authorize(job)).principal.delegation == principal.delegation
        command = await ready(publishing, job, checkpoint)
        owner = await team[1].resolve_access(team[2]["owner"], [job.kg_id], Action.CONFIGURE)
        revoked = await http.post(
            "/api/v1/identity-connections/" + token.json()["connection_id"] + "/revoke",
            json={"expected_revision": 0, "idempotency_key": "revoke-before-publish"},
        )
        assert revoked.status_code == 200, revoked.text
        owner = await team[1].resolve_access(team[2]["owner"], [job.kg_id], Action.CONFIGURE)
        with pytest.raises(DomainError) as revoked_actor:
            await queue.authorize(job)
        assert revoked_actor.value.code == ErrorCode.UNAUTHENTICATED
        with pytest.raises(DomainError) as failure:
            await coordinator.publish(access, command)
        assert failure.value.code in (
            ErrorCode.UNAUTHENTICATED,
            ErrorCode.FORBIDDEN,
            ErrorCode.POLICY_CHANGED,
        )
        assert (await PublicationJournal(queue).load(owner, job.kg_id)).revision == 0
        assert (await mongo_db.jobs.find_one({"id": str(receipt.job_id)}))["stage"] != "ready"


async def test_owner_retry_preserves_revoked_original_actor(
    bridge, material_service, team, mongo_db
):
    async with bridge() as (http, app, boot, exchange, _, _, _, _):
        assert (await boot()).status_code == 200
        token = await exchange(actions=["read", "search", "ingest"])
        principal = await app.state.identity.authenticate_bearer(token.json()["access_token"])
        services = app.state.services
        access = await services.access(principal, (material_service[1],), Action.INGEST)
        from domain_kg.materials.models import AddMaterial

        receipt = await services.materials.add(
            access,
            AddMaterial(
                kg_id=material_service[1],
                inline_text="Synthetic delegated source",
                title="Delegation retry",
                idempotency_key="delegated",
            ),
        )
        queue = services.queue
        job = await queue.claim("before-revocation")
        await queue.fail(job, DomainError(ErrorCode.VALIDATION_ERROR, "Synthetic failure"))
        revoked = await http.post(
            "/api/v1/identity-connections/" + token.json()["connection_id"] + "/revoke",
            json={"expected_revision": 0, "idempotency_key": "revoke-before-retry"},
        )
        assert revoked.status_code == 200
        owner = await team[1].resolve_access(team[2]["owner"], [job.kg_id], Action.INGEST)
        await queue.retry(owner, receipt.job_id, idempotency_key="owner-retry")
        retried = await queue.claim("after-revocation")
        raw = await mongo_db.jobs.find_one({"id": str(retried.id)})
        assert Principal.model_validate(raw["actor"]).delegation == principal.delegation
        with pytest.raises(DomainError) as denied:
            await queue.authorize(retried)
        assert denied.value.code == ErrorCode.UNAUTHENTICATED
