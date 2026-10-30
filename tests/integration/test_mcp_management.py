"""Agents exercise management through the real MCP wire and scoped stores."""
# ruff: noqa: F811

import base64
import hashlib
from contextlib import asynccontextmanager
from uuid import uuid4

import httpx
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from test_identity import issuer_fixture as issuer_fixture
from test_provider_checks import checked_provider as checked_provider
from test_publication import prepare, publishing, ready  # noqa: F401
from test_transport_authorization import settings_for

from domain_kg.app import create_app

pytestmark = pytest.mark.storage


@asynccontextmanager
async def management_client(providers, catalog, team, issuer_fixture):
    identities, _, principals, _, org, _ = team
    _, state, transport, sign = issuer_fixture
    state["issuer"] = principals["owner"].issuer
    owner = sign({"sub": "owner", "scope": "domain_kg:read domain_kg:write domain_kg:admin"})
    async with httpx.AsyncClient(transport=transport) as oidc:
        app = create_app(
            settings_for(state["issuer"]),
            identity_repository=identities,
            oidc_http_client=oidc,
            catalog_service=catalog[0],
            providers=providers,
        )
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://localhost:8000",
                headers={"Authorization": "Bearer " + owner},
            ) as wire:
                async with Client(
                    streamable_http_client("http://localhost:8000/mcp", http_client=wire)
                ) as client:

                    async def call(name, **args):
                        return await client.call_tool(
                            "domain_kg_" + name, {"org_id": str(org), **args}
                        )

                    yield call, wire, sign, app.state.services


@pytest.fixture
def agent(publishing, catalog, team, issuer_fixture):
    return lambda: management_client(publishing[3].providers, catalog, team, issuer_fixture)


async def test_agent_discovers_creates_renames_deletes_and_restores_kg(agent):
    async with agent() as (call, _, _, _):
        placements = await call("list_storage_profiles")
        schemas = await call("list_domain_profiles")
        assert not placements.is_error and placements.structured_content["items"]
        assert not schemas.is_error and len(schemas.structured_content["items"]) >= 3
        created = await call(
            "create_kg",
            command={
                "name": "Agent KG",
                "placement_id": placements.structured_content["items"][0]["id"],
                "domain_profile_id": schemas.structured_content["items"][0]["id"],
                "idempotency_key": "agent-create",
            },
        )
        assert not created.is_error
        kg = created.structured_content["kg_id"]
        rename = await call(
            "update_kg",
            kg_id=kg,
            command={
                "name": "Renamed by Agent",
                "expected_revision": created.structured_content["revision"],
                "idempotency_key": "agent-rename",
            },
        )
        assert not rename.is_error
        assert (await call("get_kg", kg_id=kg)).structured_content["name"] == "Renamed by Agent"
        deleted = await call(
            "delete_kg",
            kg_id=kg,
            command={
                "expected_revision": rename.structured_content["revision"],
                "idempotency_key": "agent-delete",
            },
        )
        assert not deleted.is_error and deleted.structured_content["state"] == "deleted"
        assert (await call("get_graph", kg_id=kg)).is_error
        restored = await call(
            "restore_kg",
            kg_id=kg,
            command={
                "expected_revision": deleted.structured_content["revision"],
                "idempotency_key": "agent-restore",
            },
        )
        assert not restored.is_error and restored.structured_content["state"] == "ready"


async def test_agent_upload_folder_partial_failure_replay_and_batch_controls(agent, publishing):
    async with agent() as (call, _, _, _):
        kg = str(publishing[1].kg_ids[0])
        content = b"Alpha uses Beta."
        uploaded = await call(
            "upload_file",
            command={
                "kg_id": kg,
                "media_type": "text/plain",
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
                "idempotency_key": "agent-upload",
            },
            content_base64=base64.b64encode(content).decode(),
        )
        assert not uploaded.is_error
        command = {
            "idempotency_key": "agent-folder",
            "items": [
                {"path": "example/a.txt", "upload_id": uploaded.structured_content["upload_id"]},
                {"path": "example/b.txt", "upload_id": str(uuid4())},
                {"path": "example/c.txt", "inline_text": "Gamma uses Delta."},
            ],
        }
        result = await call("add_folder", kg_id=kg, command=command)
        assert not result.is_error
        items = result.structured_content["items"]
        assert [item["outcome"] for item in items] == ["submitted", "failed", "submitted"]
        replay = await call("add_folder", kg_id=kg, command=command)
        assert replay.structured_content == result.structured_content
        changed = await call(
            "add_folder", kg_id=kg, command={**command, "items": command["items"][:1]}
        )
        assert changed.is_error and changed.structured_content["code"] == "IDEMPOTENCY_CONFLICT"
        assert len((await call("list_jobs", kg_id=kg)).structured_content["items"]) == 2
        batch = await call(
            "batch",
            kg_id=kg,
            command={
                "operation": "cancel",
                "items": [{"id": item["receipt"]["job_id"]} for item in (items[0], items[2])],
                "idempotency_key": "agent-cancel",
            },
        )
        assert not batch.is_error
        assert [i["outcome"] for i in batch.structured_content["items"]] == [
            "completed",
            "completed",
        ]
        retried = await call(
            "retry_job",
            kg_id=kg,
            job_id=items[0]["receipt"]["job_id"],
            idempotency_key="agent-retry",
        )
        assert not retried.is_error and retried.structured_content["stage"] == "queued"
        invalid = await call(
            "add_folder",
            kg_id=kg,
            command={
                "idempotency_key": "traversal",
                "items": [{"path": "../secret.txt", "inline_text": "private"}],
            },
        )
        assert invalid.is_error and invalid.structured_content["code"] == "VALIDATION_ERROR"


async def test_agent_management_respects_source_acl_and_owner_boundaries(agent, publishing, team):
    async with agent() as (call, wire, sign, runtime):
        _, editor, queue, coordinator, _, _ = publishing
        source, job, checkpoint = await prepare(publishing)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        kg, material = str(job.kg_id), str(source.material_id)
        graph = await call("get_graph", kg_id=kg)
        assert not graph.is_error and graph.structured_content["total_nodes"] > 0
        entity = graph.structured_content["view"]["nodes"][0]["id"]
        edit = await call("get_edit_context", kg_id=kg, target_id=entity, kind="node")
        assert not edit.is_error and edit.structured_content["source_ids"] == [material]
        detail = await call("get_material", kg_id=kg, material_id=material)
        updated = await call(
            "update_material_metadata",
            command={
                "kg_id": kg,
                "material_id": material,
                "expected_revision": detail.structured_content["material"]["revision"],
                "changes": {"tags": ["reviewed"]},
                "idempotency_key": "agent-tags",
            },
        )
        assert not updated.is_error
        original = await call(
            "read_source_file",
            kg_id=kg,
            material_id=material,
            version_id=str(source.version_id),
            offset=0,
            limit=5,
        )
        assert (
            not original.is_error
            and base64.b64decode(original.structured_content["content_base64"]) == b"Alpha"
        )
        members = team[3]
        acl = await call("get_material_acl", kg_id=kg, material_id=material)
        change = await call(
            "set_material_acl",
            command={
                "kg_id": kg,
                "material_id": material,
                "acl_mode": "restricted",
                "member_ids": [str(members["editor"].id)],
                "group_ids": [],
                "expected_revision": acl.structured_content["policy_revision"],
                "idempotency_key": "agent-acl",
            },
        )
        assert not change.is_error
        wire.headers["Authorization"] = "Bearer " + sign()
        assert (await call("get_material", kg_id=kg, material_id=material)).is_error
        assert (
            await call(
                "read_source_file",
                kg_id=kg,
                material_id=material,
                version_id=str(source.version_id),
            )
        ).is_error

        assert (await call("get_usage", kg_id=kg)).is_error
        assert (await call("get_model_settings", kg_id=kg)).is_error
        assert (
            await call(
                "delete_kg",
                kg_id=kg,
                command={"expected_revision": 0, "idempotency_key": "reader-delete"},
            )
        ).is_error


async def test_agent_owner_operations_export_and_backup_downloads(agent, publishing):
    from domain_kg.ingestion.pipeline import configured_pipeline
    from domain_kg.jobs.runner import JobRunner
    from domain_kg.operations.executor import OwnerJobExecutor

    async with agent() as (call, _, _, runtime):
        _, editor, queue, coordinator, _, _ = publishing
        source, job, checkpoint = await prepare(publishing)
        await coordinator.publish(editor, await ready(publishing, job, checkpoint))
        kg = str(job.kg_id)
        for kind in ("export", "backup"):
            result = await call(
                "submit_operation",
                command={
                    "kind": kind,
                    "kg_id": kg,
                    "expected_published_revision": 1,
                    "idempotency_key": "agent-" + kind,
                },
            )
            assert not result.is_error
            leased = await runtime.queue.claim("mcp-owner-test", kinds=(kind,))
            assert leased and str(leased.id) == result.structured_content["job_id"]
            runner = JobRunner(
                queue,
                configured_pipeline(queue, coordinator.providers),
                operations=OwnerJobExecutor(queue),
            )
            await runner.run_job(leased)
            manifest = await call("get_" + kind, kg_id=kg, job_id=str(leased.id))
            assert not manifest.is_error, manifest
            if kind == "export":
                content = await call("read_export_file", kg_id=kg, job_id=str(leased.id))
                assert not content.is_error
                body = base64.b64decode(content.structured_content["content_base64"])
                assert b"Alpha" in body and str(source.material_id).encode() in body
            else:
                backup_id = manifest.structured_content["backup_id"]
                page = await call("get_backup_page", kg_id=kg, backup_id=backup_id, index=0)
                assert not page.is_error and page.structured_content["parts"]
                part = page.structured_content["parts"][0]["object"]
                content = await call(
                    "read_backup_file", kg_id=kg, backup_id=backup_id, index=0, object_id=part["id"]
                )
                assert not content.is_error
                assert (
                    hashlib.sha256(
                        base64.b64decode(content.structured_content["content_base64"])
                    ).hexdigest()
                    == part["sha256"]
                )
        for name in (
            "get_usage",
            "get_usage_history",
            "get_audit",
            "get_operation_metrics",
            "get_projections",
            "get_schema",
        ):
            assert not (await call(name, kg_id=kg)).is_error


async def test_agent_models_hello_billing_failed_save_and_secret_redaction(
    checked_provider, catalog, team, issuer_fixture
):
    providers, _, kg_id, state, _ = checked_provider
    kg = str(kg_id)
    async with management_client(providers, catalog, team, issuer_fixture) as (call, _, _, _):
        settings = await call("get_model_settings", kg_id=kg)
        assert not settings.is_error and settings.structured_content["connection"]["has_key"]
        assert "private-probe-key" not in str(settings)
        selected = settings.structured_content["selected_profile_id"]
        command = {
            "name": "Agent models",
            "endpoint": "https://probe.example/v1",
            "credential_profile_id": selected,
            "embedding_profile_id": selected,
            "extraction_model": "chat-test",
            "embedding_model": "embed-test",
            "expected_revision": 1,
            "data_approval": "approved",
            "idempotency_key": "mcp-models",
        }
        names = await call("discover_models", kg_id=kg, component="chat", command=command)
        assert not names.is_error
        assert names.structured_content["models"] == ["chat-test", "embed-test"]
        check = await call("check_models", kg_id=kg)
        assert not check.is_error and check.structured_content["chat"]["state"] == "passed"
        state["billing"] = True
        denied = await call("configure_models", kg_id=kg, command=command)
        assert not denied.is_error and denied.structured_content["revision"] is None
        assert denied.structured_content["check"]["chat"]["code"] == "PROVIDER_BILLING_REQUIRED"
        assert "private" not in str(denied)
        assert (await providers.selected(checked_provider[1], kg_id))["revision"] == 1
        state["billing"] = False
        saved = await call("configure_models", kg_id=kg, command=command)
        assert not saved.is_error and saved.structured_content["revision"] == 2
        import json

        path = checked_provider[4]
        original_config = path.read_text()
        config = json.loads(original_config)
        selected = (await providers.selected(checked_provider[1], kg_id))["profile_id"]
        config["profiles"] = [p for p in config["profiles"] if p["id"] != selected]
        path.write_text(json.dumps(config))
        stale = await call("get_model_settings", kg_id=kg)
        assert not stale.is_error and stale.structured_content["selection_revision"] == 2
        assert stale.structured_content["connection"] is None
        assert stale.structured_content["warnings"] == ["PROVIDER_UNAVAILABLE"]
        path.write_text(original_config)
        await providers.database.platform_operators.update_one(
            {"issuer": team[2]["owner"].issuer, "subject": team[2]["owner"].subject},
            {"$set": {"active": False}},
        )
        safe = await call("get_model_settings", kg_id=kg)
        assert not safe.is_error and safe.structured_content["connection"] is None
        forbidden = await call("check_models", kg_id=kg, force=True)
        assert forbidden.is_error and forbidden.structured_content["code"] == "FORBIDDEN"


async def test_folder_continues_after_unexpected_file_failure_without_leaking_details(
    agent, publishing, monkeypatch
):
    async with agent() as (call, _, _, runtime):
        original = runtime.materials.add

        async def interrupted(access, command):
            if command.source == "folder:broken.txt":
                raise RuntimeError("private backend payload")
            return await original(access, command)

        monkeypatch.setattr(runtime.materials, "add", interrupted)
        result = await call(
            "add_folder",
            kg_id=str(publishing[1].kg_ids[0]),
            command={
                "idempotency_key": "partial-unexpected",
                "items": [
                    {"path": "broken.txt", "inline_text": "Alpha"},
                    {"path": "good.txt", "inline_text": "Beta"},
                ],
            },
        )
        assert not result.is_error
        assert [i["outcome"] for i in result.structured_content["items"]] == ["failed", "submitted"]
        assert "private backend payload" not in str(result)
