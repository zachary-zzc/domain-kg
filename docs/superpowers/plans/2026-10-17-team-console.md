# Team administration console implementation plan

> **For agentic workers:** Use `executing-plans` to implement this plan task by task in the current chat. The user has explicitly requested native continuous execution, laptop smoke tests and local Git milestones; no further handoff approval or delegation is required.

**Goal:** Give the shared team's existing authorized services a usable browser console.

**Architecture:** Server-rendered FastAPI routes call `ServiceRuntime`, identity and catalog services directly. Jinja2 autoescapes source-derived content; a small local script and vendored HTMX enhance ordinary accessible forms. Session identity and the current organization come from the existing OIDC session, never form fields.

**Tech stack:** Existing Python/FastAPI/MongoDB stack, Jinja2 3.1.6, local CSS/JavaScript and vendored HTMX 2.0.11 assets. No frontend build service or additional Docker process.

**Spec:** `docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`, Task 17 in `docs/superpowers/plans/2026-10-16-domain-kg.md`.

## Global constraints

- Keep domain logic independent of MCP, HTTP, and the PhageAgent application.
- Namespace every material, graph object, vector, job, cache entry, and citation by organization and KG.
- Verify access before retrieval and again before returning results.
- Use external OIDC identity management. Derive identity from verified credentials; request arguments never establish the caller or their permissions.
- Keep Docker databases below 1,500,000,000 bytes combined, without swap. Use tiny fixtures and serial storage tests; no native model startup or large benchmark.
- Preserve default verified/manual search and explicit unverified opt-in. Human corrections retain manual provenance.
- Commit each verified milestone locally; no push, deployment or real corpus migration.

## Review focus

- A revoked or suspended browser session cannot read a cached page or submit a form.
- An alternate KG/material/target hidden field cannot broaden the actor's current scope.
- Missing, duplicate or cross-origin CSRF input cannot invoke a service mutation.
- HTML and script-like source text remains escaped in errors, search, evidence and revision diffs.
- Hidden actions are also denied server-side; operators gain infrastructure visibility without KG evidence access.

### Step 1: Session shell and safe rendering

Files: create `console/routes.py`, `console/rendering.py`, `console/templates/base.html`, `console/templates/catalog.html`, `console/templates/error.html`, `console/static/console.css`; mount before the MCP root in `app.py`.

Interfaces: `console_router(runtime: ServiceRuntime) -> APIRouter`; a rendering helper accepts the current verified principal/member and optional KG `AccessContext`, revalidates before rendering, returns an HTML response with `Cache-Control: no-store` and a restrictive same-origin CSP.

- [x] Add `tests/integration/test_console.py` cases: `/admin` without a session displays login; an active member sees only its KG catalog; suspended/revoked sessions fail; `<script>` labels render escaped.
- [x] Observe the missing route failure, implement the smallest shell using `IdentityRuntime.actor`, `CatalogService.list_kgs` and local templates.
- [x] Render a skip link, headings, labeled navigation, visible keyboard focus and a logout form. Disable HTMX history caching, script processing and eval; source text never becomes active HTML. Reload persisted browser history pages before showing revoked data. Form CSRF uses the existing session token hash and exact origin check; no local password or alternate identity path.
- [x] Verify Ruff, strict mypy and the new serial console tests; commit the shell and dependency/lock changes.

### Step 2: Search, materials and durable work

Files: add `console/workspace.py`, `console/templates/kg.html`, `materials.html`, `material.html`, `search.html`, `job.html`, `console/static/console.js`; create `jobs/inspection.py` and typed job paging models only where the existing queue lacks a list operation.

Interfaces: routes under `/admin/kgs/{kg_id}` call `runtime.inspection`, `runtime.search`, `runtime.citations`, `runtime.queue` and `runtime.lifecycle`. `JobInspection.list(access: AccessContext, kg: UUID, page: PageRequest) -> JobPage` uses opaque principal/policy/visibility/view-bound cursors and verifies each job through the shared queue before disclosing it.

- [x] Add tests for reader search/citations, inaccessible sources, pending job visibility and expired cursors before implementation.
- [x] Implement KG selector, material list/detail with accepted/retained state, honest unverified labels, bounded fast/deep search and evidence links. Use safe allowlisted modes/filters; never display object keys or provider credentials.
- [x] Add editor forms for inline text or uploaded files, replacement, metadata, exclude/delete and restore. File transfer reuses the existing checksum-verified raw upload API; expected revisions and idempotency keys are hidden typed command fields. Redirect accepted mutations to their real job/receipt view.
- [x] Verify the focused serial console tests and commit this usable read/lifecycle milestone.

### Step 3: Team and permission management

Files: add `console/team.py`, `console/templates/team.html`, `grants.html`, `material_acl.html`.

Interfaces: identity repository admission/group methods, `GrantManagement.list/set`, `MaterialGrantManagement.get/set`; no form establishes the current actor or organization.

- [x] Add tests: organization admin admits a member; owner grants a group; non-admin/non-owner forged form posts are denied; concurrent revision and last-owner protections remain enforced.
- [x] Implement membership/group/service-identity screens and KG/material grant forms. OIDC issuer/subject fields identify the member being admitted, not the caller. Only show actions the resolved current role allows.
- [x] Run the focused serial tests, existing grant regressions, Ruff and mypy; commit.

### Step 4: Curation, history and operations

Files: add `console/curation.py`, `console/operations.py`, templates `node.html`, `relation.html`, `history.html`, `operations.html`.

Interfaces: `RevisionService.update_node/update_relation/curate_node/get_revisions/rollback`; `ProjectionManagement.status/repair`; catalog registry inventory/capabilities. Owner backup/export controls are added when Task 18 services exist, without placeholder success.

- [ ] Add tests for escaped before/after diffs, pending/published curation, stale revisions, rollback under current permissions and imported target labels.
- [ ] Implement node/relation edits, merge/split/suppression, explicit source/public scope, history and compensating rollback review using the existing typed commands.
- [ ] Add owner projection/readiness/repair screens and a separate operator registered-backend health view. An operator without KG membership cannot view materials, graph names, counts or search results.
- [ ] Verify the focused serial console and curation/operations permission tests; commit.

### Step 5: Browser and local acceptance

Files: `tests/evaluation/test_console_flows.py`, `docs/console.md`; extend `scripts/smoke.py` with Task 17.

- [ ] Add repeatable tiny session-backed end-to-end cases for admin, owner, editor, reader, operator and revoked-session flows. Include CSRF, namespace substitution, HTML escaping, expected revision and pending job cases.
- [ ] Run `uv run pytest tests/integration/test_console.py tests/evaluation/test_console_flows.py -q`, Ruff and strict mypy; inspect representative rendered pages and keyboard/focus behavior in the laptop browser.
- [ ] Record measured results and external OIDC acceptance still outstanding, update only verified plan boxes, and commit. Continue to Task 18 without a new permission prompt.

Dependency research: the [Jinja2 release page](https://pypi.org/project/Jinja2/)
identifies 3.1.6; the [official HTMX installation guide](https://htmx.org/docs/)
provides 2.0.11 and its SHA-384 integrity value. Vendor that exact browser asset
and its upstream license, verify the published integrity, serve it locally and
record the checksum. No runtime CDN dependency, JavaScript bundler or browser
automation package installation is required.
