"""Resource templates are scoped and direct calls require verified credentials."""

from uuid import uuid4

import pytest
from mcp.server.mcpserver.exceptions import ResourceError

from domain_kg.transport.errors import SafeMCPServer
from domain_kg.transport.resources import register_resources


async def test_resource_templates_and_direct_credential_boundary():
    server = SafeMCPServer("resource-fixture")
    register_resources(server, None)
    templates = await server.list_resource_templates()
    assert {str(item.uri_template) for item in templates} == {
        "domain-kg://{org_id}/{kg_id}/materials/{material_id}",
        "domain-kg://{org_id}/{kg_id}/schema",
    }
    with pytest.raises(ResourceError) as denied:
        await server.read_resource(f"domain-kg://{uuid4()}/{uuid4()}/materials/{uuid4()}")
    assert "UNAUTHENTICATED" in str(denied.value)
