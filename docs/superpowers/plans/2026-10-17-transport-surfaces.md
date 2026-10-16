# Task 15 transport execution steps

Continue the main implementation plan after Task 14's verified Git milestone.
Use native execution in this chat. Keep task boxes unchecked until their gates pass.
Read `docs/design.md`, `docs/contracts.md`, and
`docs/revisions-and-publication.md` with this plan.

1. Add missing shared read services for material lists/details and source/manual
   citations. Use current policy and accepted source versions, bounded pages and
   opaque cursors bound to principal, filters, visibility and publication. Test
   hidden-source fields, source removal, suppression and cursor expiry against the
   real stores. Return no raw storage keys or provider configuration.
2. Add a transport runtime that owns the existing catalog/material/lifecycle/job,
   graph/retrieval/revision and provider services. Derive the principal from verified
   credentials; organization arguments select only authorized memberships. Add
   multi-organization bearer tests and safe validation/domain-error rendering.
3. Register every MCP tool in `docs/contracts.md` with typed input/output schemas,
   effect annotations, concise text and structured results. Protected resources
   call the same read services and policy. Direct or stdio calls without a verified
   request credential fail closed. Check the installed `mcp==2.3.0` APIs and current
   official SDK documentation; do not use the older FastMCP examples.
4. Complete HTTP retrieval, edits, curation/history/rollback, lifecycle, jobs and
   grant routes as thin wrappers. Match body/path IDs, use current CSRF/session
   checks, enforce revision/idempotency contracts, and sanitize validation failures.
   Add owner projection readiness/repair-status routes using existing publication
   services. Later import/backup/export operations register here when their domain
   services are implemented in Tasks 16 and 18; keep their route gate outstanding.
5. Run `uv run pytest tests/protocol -q` and a serial signed-issuer real-store
   HTTP/MCP equivalence smoke on this laptop. Verify discovery, schemas, errors,
   reader/editor/owner boundaries, material ACLs, revocation, paging and citations.
   Run Ruff and strict mypy before the database smoke; commit verified changes.
   A real external IdP and target-client login remain explicit acceptance gates.

Use tiny synthetic fixtures and the existing four databases, whose combined hard
limits stay below 1.5 GB with swap disabled. Start no native model for these tests.
No deployment, external messages, actual corpus migration, or remote Git changes.
