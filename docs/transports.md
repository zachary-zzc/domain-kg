# HTTP and MCP transports

Run the app with the configured external OIDC issuer and client ID. The protected
MCP endpoint is `/mcp`; the same application services serve `/api/v1`. The app
registers the 22 `domain_kg_*` application tools in `contracts.md`, plus the
compatibility probe. Without configured identity, only the protected probe is
registered and its default verifier denies credentials.

Tools use typed inputs, output schemas, effect annotations, concise human text and
structured results. Domain failures have `isError=true` and a safe code, message,
retryable flag and request ID. HTTP uses the corresponding status and error body.
Unknown tool arguments and malformed HTTP bodies are rejected without echoing
submitted material. JSON envelopes are limited to 2 MiB before parsing, including
chunked requests. Larger originals use the streaming upload route and its existing
50 MiB length/checksum/quota controls. An oversized envelope returns HTTP 413;
application quota exhaustion returns 429.

A resource-bound verified bearer token establishes issuer and subject. An `org_id`
MCP argument or `x-domain-kg-organization` HTTP header only selects a current
membership. A caller with multiple organizations must select one; no argument can
supply identity, roles or grants. Each service rechecks current policy. Service
credential scopes are intersected with the selected membership's restrictions.
Direct MCP calls and stdio have no verified HTTP credential and deny application
reads. Browser sessions use the existing same-origin CSRF checks for mutations.

The official SDK client supports two protected metadata templates:

- `domain-kg://<org_id>/<kg_id>/materials/<material_id>`
- `domain-kg://<org_id>/<kg_id>/schema`

Material pages contain accepted, currently visible materials and current metadata;
version inspection lists accepted or retained originals. Opaque page cursors bind
the caller, organization/KG, filters, limit, policy, visibility and accepted view.
Material details expose no object keys, bucket names or connection configuration.
They include at most 25 permitted source chunks with original spans and an explicit
truncation flag. Current node suppression also hides their incident chunks when a
retained version is explicitly selected. Every listed retained version is checked
against the accepted publication ledger.
Owner grant pages use a compound subject UUID/kind cursor, so independent member
and group UUIDs cannot cause skipped grants.

`GET /api/v1/citations/{id}?kg_id=<kg_id>` resolves current accepted source spans or
human curation provenance under current permissions. Supplying both `material_id`
and `version_id` explicitly selects an accepted retained source version. Source
citations include the authorized original-file route. Manual citations contain the
human actor/reason and curation revision, without invented chunks or spans.

Owners manage material ACLs through revisioned `GET/PUT .../materials/{id}/acl` and
KG roles through `GET/PUT/DELETE .../members/{id}`. Expected revisions use the
returned organization policy revision; commands require idempotency keys.
`GET .../projections` verifies accepted graph/vector receipts and returns coarse
readiness and safe warning codes. `POST .../repair` takes a material ID and key and
returns the existing durable repair job receipt. Workers keep their current
permission, lease and publication checks.

Import/export, backup/restore, generation maintenance, statistics and audit routes
are completed with their domain services in Tasks 16 and 18. Those gates remain
open; no placeholder successful jobs are exposed. A real external IdP login,
Inspector session and target remote-client acceptance also remain open. Local
signed issuer fixtures do not establish external deployment readiness.
