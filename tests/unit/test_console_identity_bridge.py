"""Browser provisioning commands preserve finite grants and reject ambiguous fields."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain_kg.console.identity_bridge import integration_form, revision_form
from domain_kg.console.rendering import html
from domain_kg.identity.bridge.models import ConnectionPage, ConnectionView
from domain_kg.identity.models import Action


def test_integration_form_builds_explicit_bounds_without_granting_owner():
    profile, reader, allowed, group = (uuid4() for _ in range(4))
    command = integration_form(
        {
            "name": "Research platform",
            "profile_id": str(profile),
            "idempotency_key": "new",
            f"kg_{reader}": "reader",
            f"kg_{allowed}": "allowed",
            "action_read": "on",
            "action_search": "on",
            f"group_{group}": "Researchers",
        }
    )
    assert command.allowed_kg_ids == (reader, allowed)
    assert command.allowed_actions == (Action.READ, Action.SEARCH)
    assert [(grant.kg_id, grant.role) for grant in command.default_grants] == [(reader, "reader")]
    assert command.group_mappings[0].group_id == group
    assert not command.auto_approve


@pytest.mark.parametrize(
    "extra",
    [
        {"user_id": "owner"},
        {"action_manage_organization": "on"},
        {"action_read": "false"},
        {"auto_approve": "true"},
        {"kg_" + str(uuid4()): "owner"},
    ],
)
def test_integration_form_rejects_forged_authority(extra):
    with pytest.raises((ValueError, ValidationError)):
        integration_form(
            {
                "name": "Test",
                "profile_id": str(uuid4()),
                "idempotency_key": "new",
                "action_read": "on",
                **extra,
            }
        )


def test_revision_form_does_not_ignore_extra_fields_or_negative_revision():
    for fields in (
        {"expected_revision": "-1", "idempotency_key": "change"},
        {"expected_revision": "0", "idempotency_key": "change", "org_id": str(uuid4())},
    ):
        with pytest.raises((ValueError, ValidationError)):
            revision_form(fields)


def test_connections_render_escaped_application_expiry_and_no_credential_fields():
    now = datetime.now(UTC)
    item = ConnectionView(
        id=uuid4(),
        org_id=uuid4(),
        integration_id=uuid4(),
        member_id=uuid4(),
        application="<script>platform</script>",
        kg_ids=(uuid4(),),
        actions=(Action.READ,),
        created_at=now - timedelta(hours=1),
        expires_at=now - timedelta(seconds=1),
        revoked=False,
        revision=0,
    )
    response = html(
        "identity_connections.html",
        {
            "title": "Agent connections",
            "directory": ConnectionPage(items=(item,)),
            "kind": "connections",
            "own": True,
            "integration_id": None,
            "limit": 25,
            "command_key": "test",
            "now": now,
        },
    )
    body = response.body.decode()
    assert "&lt;script&gt;platform&lt;/script&gt;" in body
    assert "Expired" in body and f"/admin/connections/{item.id}/revoke" in body
    assert "access_token" not in body and "client_secret" not in body
    assert response.headers["cache-control"] == "no-store"
