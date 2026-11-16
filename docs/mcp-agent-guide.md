# Use Domain KG from an Agent

Connect a Streamable HTTP MCP client to `http://127.0.0.1:8000/mcp` in the local workspace, or your deployed HTTPS `/mcp` URL. Authenticate with an OIDC access token whose audience is that exact MCP resource. Run `tools/list` after connecting; every tool has typed inputs, outputs and effect annotations. All KG tools start with `domain_kg_`.

The same current permissions apply in the console, HTTP and MCP. Agent arguments cannot establish identity or grant a role. Include `org_id` when your identity belongs to more than one organization. Use your authorized user identity for owner/admin workflows. The local `python -m scripts.local_workspace token` command issues the existing **editor service identity** token: it can ingest and edit permitted sources in its explicitly assigned KG, but cannot create KGs, manage people or change model connections. Do not elevate that identity to bypass those boundaries.

## Workflow map

Names below omit `domain_kg_`.

| Console workflow | MCP tools | Authority |
| --- | --- | --- |
| Discover and create KG | `list_kgs`, `get_kg`, `list_storage_profiles`, `list_domain_profiles`, `create_kg` | Organization member; admin to create |
| Rename, read-only, archive, delete and restore | `update_kg`, `delete_kg`, `restore_kg` | KG owner |
| Text, file and folder ingestion | `add_material`, `upload_file`, `add_folder`, `update_material` | Editor + source access |
| Materials, metadata, retained versions and originals | `list_materials`, `get_material`, `list_managed_materials`, `update_material_metadata`, `read_source_file` | Reader/editor as in console |
| Remove, restore or permanently purge a source | `remove_material`, `restore_material`, `purge_material` | Editor; owner for purge |
| Batch delete/process/restore/retry/cancel | `batch`, `get_batch` | Existing per-target authority |
| Durable progress and diagnostics | `list_jobs`, `get_job`, `retry_job`, `cancel_job` | Source access; owner for KG jobs |
| Shared graph and retrieval | `get_graph`, `get_schema`, `get_node`, `get_subgraph`, `search`, `get_citation`, `list_images` | Reader with evidence visibility |
| Edit graph and inspect/undo history | `get_edit_context`, `update_node`, `update_relation`, `curate_node`, `get_revisions`, `rollback_revision` | Editor; owner for public changes/rollback |
| Source permissions and KG roles | `get_material_acl`, `set_material_acl`, `list_members`, `set_member_role` | Owner |
| Model choices and Advanced settings | `get_model_settings`, `set_provider`, `approve_model`, `configure_models`, `check_models`, `discover_models`, `set_default_model` | Owner; owner + platform operator for connections/tests/approval/default |
| Server-wide document parsing | `get_parser_settings`, `configure_parser` | Owner to read; human owner + platform operator to save |
| Legacy graph import | `plan_import`, `execute_import` | Editor |
| Backup/export/restore/reindex/repair/retention | `submit_operation`, `get_backup`, `get_backup_page`, `read_backup_file`, `get_export`, `read_export_file`, `get_restore`, `get_projections`, `repair_material` | Owner |
| Usage, audit and health | `get_usage`, `get_usage_history`, `get_audit`, `get_operation_metrics`, `get_health` | Owner; reader for coarse health |
| Team and service identities | `list_team`, `admit_member`, `admit_service`, `change_member`, `create_group`, `set_group_member` | Human organization admin |
| Approved infrastructure | `list_backends`, `list_approved_backend_references`, `register_backend`, `validate_backend`, `register_storage_profile` | Platform operator |

`get_graph` discovers nodes without seeds and pages the **shared graph for the whole KG**. Files have independent provenance and permissions inside this graph. Before editing a node/relation, call `get_edit_context` to obtain current `source_ids` and `expected_revision`; use the returned scope in the mutation. `get_material_acl` returns `policy_revision`, which becomes `expected_revision` in `set_material_acl`.

## Create and ingest

To change parsing limits, first call `get_parser_settings` and retain its
`revision`. Submit `configure_parser` with the complete returned `options` (modify
the needed values) and `expected_revision`. The default `memory_limit_mib` is 1024
for every format. Changes apply to **all KGs on this server**, from the next job or
retry; running jobs keep their existing settings. No model call or retry is
triggered. The operator chooses the writable file on the server; clients cannot
provide paths. See [parsing configuration](parsing.md#parser-configuration).

Discover legal placement and domain IDs first. Do not invent IDs or backend URLs.

```json
{"name":"domain_kg_create_kg","arguments":{"org_id":"ORG_UUID","command":{"name":"Research","placement_id":"PLACEMENT_UUID","domain_profile_id":"DOMAIN_UUID","idempotency_key":"create-research-1"}}}
```

Select an approved provider after creation. An owner/operator can discover reusable same-organization profiles with `get_model_settings` and use `approve_model` with explicit `data_approval:"approved"` for the new KG. A regular owner can use `set_provider` only for an already approved profile.

```json
{"name":"domain_kg_add_material","arguments":{"org_id":"ORG_UUID","command":{"kg_id":"KG_UUID","title":"A note","inline_text":"Alpha uses Beta.","idempotency_key":"note-1"}}}
```

Admission returns `material_id`, `version_id`, and `job_id`. Poll `get_job` until `ready` or `ready_with_warnings`; only then is that source published. Inspect warning counts and explicit failure diagnostics. Failed replacement leaves the previous accepted version available.

## Upload files and folders

The **client** reads local files. MCP never receives a server filesystem path. `upload_file` accepts strict base64 content up to 512 KiB plus an `UploadCommand` declaring `kg_id`, `media_type`, SHA-256, `size_bytes`, `idempotency_key` and optional replacement `target_material_id`. It returns a completed `upload_id`; upload alone does not submit a parsing job. The normal file-format, parser/OCR, image interpretation, quota and source-evidence rules still apply.

For files larger than 512 KiB (up to 50 MiB), use the existing streaming HTTP upload with the **same bearer token**. The fields other than `kg_id` are query parameters, and `Content-Type` must equal `media_type`. This avoids putting large base64 payloads in an Agent's context or exceeding the 2 MiB JSON envelope limit. Then pass the returned upload ID through MCP.

```python
# httpx client is authenticated to your existing service; raw is client-read bytes.
upload = await http.post(
    f"/api/v1/kgs/{kg_id}/uploads",
    params={"media_type": media_type, "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw), "idempotency_key": upload_key},
    headers={"Content-Type": media_type}, content=raw,
)
upload.raise_for_status()
upload_id = upload.json()["upload_id"]
```

After uploading, submit a relative-path manifest. Each item supplies exactly one `upload_id` or `inline_text`:

```json
{"name":"domain_kg_add_folder","arguments":{"org_id":"ORG_UUID","kg_id":"KG_UUID","command":{"idempotency_key":"folder-import-1","items":[{"path":"notes/a.txt","inline_text":"Alpha uses Beta."},{"path":"reports/b.pdf","upload_id":"UPLOAD_UUID"}]}}}
```

A manifest has 1–100 distinct relative paths and at most 1 MiB of JSON. Divide larger folders into batches with stable keys. Invalid paths/duplicate paths reject the manifest before submission. Valid entries are submitted independently: one failed upload/source does not stop later files. The response includes a per-file `submitted`/`failed` outcome and each accepted job receipt. Replaying the **same manifest and key** resumes without duplicate materials; changing input under that key returns `IDEMPOTENCY_CONFLICT`. Use `list_jobs` after reconnecting.

To update an existing folder, inspect visible materials' `metadata.source` (`folder:<relative path>`) and their active version hash. Skip unchanged files; for changed files include `material_id`, `expected_active_version` and optionally `expected_revision` in that manifest entry. Its upload must bind `target_material_id`. Ambiguous source matches need an explicit selection. A new key with no replacement IDs deliberately adds new materials.

## Batch operations and deletion

`batch.command.operation` is `delete`, `restore`, `process`, `retry` or `cancel`. Each of 1–100 distinct items contains `id`. Material operations also require `revision`; processing an accepted material requires its current `version_id`. Retry/cancel IDs refer to jobs. Results are independent and only the verified initiating actor can read `get_batch`. A retry of the same batch command resumes submission; use a new key for a new attempt after changing failed targets.

`delete_kg` is **recoverable deletion**. It requires current owner access and `{expected_revision,idempotency_key}`, immediately fences reads/publication and hides the KG from the default catalog. Find retained deleted KGs with `list_kgs(lifecycle:"deleted")`, then use `restore_kg` with that row's revision. Restoration preserves the previous lifecycle state. Original files, assertions, manual history, grants and shared infrastructure remain retained. Entire-KG physical erasure is not exposed by this operation; source purge retains its separate retention checks.

## Models and owner maintenance

`get_model_settings` returns safe model choices, selection revision, contexts and image capability. Only an owner/operator sees endpoint and key-presence flags; keys themselves never return. Reuse saved connections through `credential_profile_id` and `embedding_profile_id` instead of putting secrets in model prompts. Newly supplied credentials are saved only in the existing private provider configuration file. Treat them as secret tool arguments in clients that record tool traces.

`configure_models` supports the same Alibaba/OpenAI-compatible/Claude/local chat and embedding fields as Advanced settings. By default it performs a synthetic Hello JSON test plus one embedding test; if either fails, nothing is saved. `check_models` tests a proposed or selected profile, returning safe billing, invalid-key, rate-limit and unavailable-model diagnostics. These tests and model discovery may make provider requests and incur fees; they are annotated as external/non-idempotent. They never send source materials. No universal account-balance API is implied.

`submit_operation.command.kind` is `backup`, `export`, `restore`, `generation`, `maintenance`, `retention` or `restore_cleanup`; the discriminator exposes the exact schema for each. Use `get_kg.published_revision` for `expected_published_revision`. Backup restoration allocates a **new KG** and retains separate provider approval/readiness checks. Poll `get_job`, then inspect the corresponding manifest/status. File reads return base64, `next_offset`, `total_bytes` and `eof`; prefer protected HTTP download URLs for large files. Every read checks current source or owner authority, including its final publication/visibility fence.
