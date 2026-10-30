# MCP console parity implementation plan

> Execute natively in this chat, preserving all existing workspace changes. Use lean-build, writing-plans, TDD and verification-before-completion. No commits or corpus migration.

**Goal:** Agents can discover prerequisites and perform the KG workflows available in the console through authenticated MCP tools.

**Architecture:** Thin typed tools call the same catalog, materials, batch, jobs, graph, identity, provider and owner-operation services. Add only missing domain behavior: recoverable KG deletion and bounded folder manifest submission. Preserve OIDC, current policy, namespaces, idempotency, expected revisions, evidence and atomic publication.

**Tech stack:** Python 3.12, official MCP SDK 2.3.0, Pydantic, MongoDB replica set, existing Neo4j/Qdrant/S3 adapters.

**Spec:** User request; docs/design.md, docs/contracts.md, docs/revisions-and-publication.md; implementation-plan Tasks 15/17.

## Review focus

- A deleted KG must fence existing reads and workers; restore must retain evidence and ownership.
- A folder failure must not block other files; replay must not create duplicates or allow changed manifests.
- Agents need placement/schema/model choices and current edit revisions before submitting commands.
- Restricted sources and cross-KG IDs must fail exactly as they do in HTTP.
- Provider secrets and backend exceptions must never enter results or validation errors; model calls in tests are mocked.

## Task 1: Catalog lifecycle

Files: catalog/models.py, repository.py, service.py, routes.py; identity/policy.py and invariants.py; console/setup.py and templates/settings.html; tests/integration/test_catalog.py.

- [x] Add failing real-store tests for deletion, replay, conflict, owner-only restoration and read fencing.
- [x] Extend existing catalog state commands for rename, deleted state and explicit restoration; preserve active-owner invariant. Add HTTP/console controls.
- [x] Run focused catalog tests.

## Task 2: Agent file and folder ingestion

Files: materials/folders.py; transport MCP modules; tests/integration/test_mcp_management.py.

- [x] Add failing MCP end-to-end tests for base64 upload, folder partial failure, replay and changed-manifest rejection.
- [x] Add bounded folder manifest service using existing add/replace services and durable command identity; expose completed-upload and inline-text entries. Small files use MCP base64 (512 KiB); larger files use the existing authenticated streaming HTTP upload (50 MiB).
- [x] Verify upload hashes, ACLs, cross-KG denial and per-file receipts.

## Task 3: Console workflows over MCP

Files: transport/mcp*.py and models.py; providers/inspection.py; tests/protocol/test_mcp_tools.py and tests/integration/test_mcp_management.py.

- [x] Expand required tool/schema tests and exercise missing workflows to see failures.
- [x] Share registration/error helpers; add discovery, metadata/ACL, managed sources, jobs/batch, graph overview/edit context, import, backup/export/restore/repair/retention, usage/audit, models and organization/infrastructure tools through existing services.
- [x] Keep model setup owner+operator only, never return credentials; mark provider checks as external and non-idempotent when forced.
- [x] Run protocol and transport authorization tests with real stores, mocked model HTTP.

## Task 4: Delivery and verification

Files: docs/contracts.md, docs/mcp-agent-guide.md, README.md and this record.

- [x] Document parity, lifecycle semantics, MCP connection and file/folder recipes, protected downloads and model settings authority.
- [x] Run Ruff, strict mypy, unit/protocol and affected storage integration tests.
- [x] Review changed boundaries; restart idle local services and check live discovery and health without mutating user KGs or making paid model requests.

## Evidence / decisions

- Official SDK README fetched from modelcontextprotocol/python-sdk; installed SDK confirms MCPServer.tool and snake_case ToolAnnotations. Retain SDK 2.3.0.
- User explicitly requests console capability parity, including prior advanced model configuration. Extend the old browser-only configuration contract to authenticated owner+operator MCP; secrets stay in its existing private configuration file and never appear in output. Large binary transfer retains the streaming HTTP boundary.
- Existing workspace is the active local application; implement in place per user instructions, with no delegation or automatic commits.


## Verification record

- RED: missing delete_kg; missing MCP discovery/management tools; console deletion controls absent; folder unexpected error stopped later files; restoring read_only returned ready; stale provider selection prevented inspection.
- GREEN: 748 unit/protocol tests, 30 affected real-store integration tests, 16 browser logic tests. The first native-parser run was restricted by the sandbox; its authorized local-process rerun passed.
- Ruff clean; strict mypy clean across 273 source files; git diff --check clean.
- Wheel/source distribution built and passed the repository package checker; new tools and lifecycle templates are included, private files excluded.
- Fixed a regression exposed by owner-operation replay: fresh admission returned microsecond timestamps while BSON replay returned milliseconds. JobQueue now returns the persisted precision consistently; existing failing regression passes.
- Local API and both workers restarted only after confirming zero active jobs. Launcher session: 59808. Live health 200, anonymous MCP 401, 78 tools discovered (77 KG tools plus protocol ping). Current authorized catalog/material job inspection succeeded; editor token denied owner-only model settings. No paid model requests or user-KG mutations were made.
- Live checks use the current catalog's returned IDs; the older hard-coded KG ID was inaccessible to the test identity and was not modified.
- Automatic approval rejected an unscoped test operator deletion. Replaced it with a single synthetic owner's active flag update in the fixture-owned random database; that test passed. No user permission changes occurred.
- MCP deliberately keeps large-file streaming over the existing authenticated HTTP upload. Recoverable KG deletion retains data; physical whole-KG erasure is not represented by that command.
