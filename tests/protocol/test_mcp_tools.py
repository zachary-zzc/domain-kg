"""MCP schemas expose typed commands and never let arguments establish a caller."""

from uuid import uuid4

from domain_kg.transport.errors import SafeMCPServer
from domain_kg.transport.mcp import register_tools

REQUIRED = {
    "list_kgs",
    "get_kg",
    "create_kg",
    "list_materials",
    "get_material",
    "add_material",
    "update_material",
    "remove_material",
    "restore_material",
    "get_job",
    "search",
    "get_node",
    "get_subgraph",
    "update_node",
    "update_relation",
    "curate_node",
    "get_revisions",
    "rollback_revision",
    "update_kg",
    "set_provider",
    "list_members",
    "set_member_role",
    "get_health",
    "list_storage_profiles",
    "list_domain_profiles",
    "get_schema",
    "delete_kg",
    "restore_kg",
    "upload_file",
    "add_folder",
    "list_managed_materials",
    "update_material_metadata",
    "purge_material",
    "get_material_acl",
    "set_material_acl",
    "list_images",
    "read_source_file",
    "list_jobs",
    "retry_job",
    "cancel_job",
    "batch",
    "get_batch",
    "get_graph",
    "get_edit_context",
    "get_citation",
    "plan_import",
    "execute_import",
    "submit_operation",
    "get_backup",
    "get_backup_page",
    "read_backup_file",
    "get_export",
    "read_export_file",
    "get_restore",
    "get_projections",
    "repair_material",
    "get_usage",
    "get_usage_history",
    "get_audit",
    "get_operation_metrics",
    "get_model_settings",
    "configure_models",
    "approve_model",
    "check_models",
    "discover_models",
    "set_default_model",
    "list_team",
    "admit_member",
    "admit_service",
    "change_member",
    "create_group",
    "set_group_member",
    "list_backends",
    "validate_backend",
    "list_approved_backend_references",
    "register_backend",
    "register_storage_profile",
}


async def test_required_tool_schemas_annotations_and_unauthenticated_calls_fail_closed():
    server = SafeMCPServer("schema-fixture")
    register_tools(server, None)
    tools = {item.name: item for item in await server.list_tools()}
    assert set(tools) == {"domain_kg_" + name for name in REQUIRED}
    for tool in tools.values():
        assert tool.output_schema and tool.input_schema["type"] == "object"
        assert tool.input_schema["additionalProperties"] is False
        assert tool.annotations
        if not tool.annotations.idempotent_hint:
            assert tool.name in {
                "domain_kg_check_models",
                "domain_kg_discover_models",
                "domain_kg_configure_models",
            }
        assert "AccessContext" not in str(tool.input_schema)
        assert "principal" not in str(tool.input_schema)
    for name in ("search", "get_node", "get_subgraph", "get_kg", "get_health", "list_kgs"):
        assert tools["domain_kg_" + name].annotations.read_only_hint
    assert tools["domain_kg_search"].annotations.open_world_hint
    assert tools["domain_kg_curate_node"].annotations.destructive_hint
    for name in ("delete_kg", "batch", "set_material_acl", "submit_operation", "configure_models"):
        assert tools["domain_kg_" + name].annotations.destructive_hint
    result = await server.call_tool(
        "domain_kg_get_node",
        {
            "kg_id": str(uuid4()),
            "entity_id": str(uuid4()),
        },
    )
    assert result.is_error and result.structured_content["code"] == "UNAUTHENTICATED"
    assert len(result.content[0].text) < 400


async def test_argument_identity_or_unknown_fields_are_rejected_without_echoing_private_input():
    server = SafeMCPServer("schema-fixture")
    register_tools(server, None)
    secret = "private fixture material which must not be echoed"
    result = await server.call_tool(
        "domain_kg_search",
        {
            "command": {"kg_ids": [str(uuid4())], "query": secret, "user_id": "owner"},
        },
    )
    assert result.is_error and secret not in str(result.content)
    unknown = await server.call_tool(
        "domain_kg_get_node",
        {
            "kg_id": str(uuid4()),
            "entity_id": str(uuid4()),
            "user_id": "owner",
        },
    )
    assert unknown.is_error and unknown.structured_content["code"] == "VALIDATION_ERROR"
