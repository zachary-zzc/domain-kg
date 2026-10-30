# Domain KG MCP and HTTP contracts

## Shared conventions

The service name is `domain_kg_mcp`; the Python package is `domain_kg`. MCP and HTTP operations use the same policy and domain services. UUIDs identify organizations, KGs, materials, versions, entities, assertions, jobs, and backends. A storage profile binds approved MongoDB, Neo4j, Qdrant, and object-store references; each KG uses one profile. MongoDB is authoritative for publication and permissions. Every operation belongs to the authenticated organization. Neither `user_id` nor an asserted role in a request establishes identity.

HTTP routes are under `/api/v1`; MCP is at `/mcp`. Use HTTPS in a shared deployment. Authenticate MCP with resource-bound OAuth access tokens. The administration console is under `/admin` and uses server-side OIDC sessions. Health routes expose only coarse status without internal URLs or credentials.

Trusted platform provisioning and revocable user delegation use the versioned
[identity bridge contract](identity-bridge.md) and generated
[OpenAPI](api/identity-bridge.openapi.json). The canonical OIDC issuer still signs
tokens. A registered exchange client's access token also requires a durable,
current token-hash/connection binding; missing or revoked bindings never fall
back to ordinary token authentication. Optional `Principal.delegation` is omitted
for legacy principals and persists unchanged in durable job actors. Shared Policy
intersects current user permissions, integration limits and connection limits on
every reauthorization, including before publication. `Mcp-Session-Id` is never an
authentication credential.

Unknown request fields are rejected. List operations accept `limit` from 1 to 100, default 25, and an opaque cursor bound to the query, principal, policy/visibility revision, and published KG view. A changed revision returns `CURSOR_EXPIRED`. Mutating operations accept an `idempotency_key` and optimistic version where relevant. Reusing a key with different input returns `IDEMPOTENCY_CONFLICT`.

MCP and public JSON endpoints accept no arbitrary filesystem paths, SQL, Cypher or
DSNs. Clients upload bytes, not server paths. MCP `upload_file` handles up to
512 KiB of strict base64; larger files retain the authenticated streaming HTTP
upload (50 MiB). A completed upload ID is then used by MCP ingestion.

Advanced model connection setup is available through both the CSRF-protected
console and bearer-authenticated MCP. Both require current KG owner plus human
platform-operator authority. Endpoints and newly supplied credentials are validated
by the existing ModelSetup boundary and saved only to its preconfigured private
provider file. Secrets never enter MongoDB, tool results or validation errors.
Ordinary owners can discover and select only approved profiles. Backend registration
accepts approved connection reference IDs, never DSNs or raw backend credentials.

## Permission matrix

These are minimum roles; a material's restricted ACL must also permit access. KG owners can manage all material ACLs in their KG.

| Operation | Reader | Editor | Owner | Organization admin | Platform operator |
| --- | --- | --- | --- | --- | --- |
| Search, inspect visible nodes and materials | Yes | Yes | Yes | Only with KG grant | Only with KG grant |
| Add, replace, exclude, delete visible material | No | Yes | Yes | Only with KG grant | Only with KG grant |
| Curate visible entities and supports | No | Yes | Yes | Only with KG grant | Only with KG grant |
| Change KG schema, quotas within org allowance, or material grants | No | No | Yes | Via audited owner appointment | No implicit access |
| Manage KG memberships, archive, export, backup, restore, purge | No | No | Yes | Via audited owner appointment | No implicit access |
| Manage organization members and groups, create KG | No | No | No implicit org role | Yes | No implicit org role |
| Register backend and change placement allowance | No | No | No | No | Yes |

An owner also needs organization membership. Refuse removal of the last active organization administrator or last active KG owner. Service identities receive explicit KG grants and scopes, cannot perform interactive account administration, and are audited separately from human users.

## MCP tools

Use descriptive `domain_kg_` names and typed input/output schemas. Return concise structured evidence plus a short text rendering. Tool annotations describe effects; they do not replace authorization. Large file transfer uses streaming HTTP; infrastructure tools accept only preconfigured connection references. See [the Agent guide](mcp-agent-guide.md) for complete workflows and examples.

| Tool | Inputs beyond common auth context | Result | Minimum access |
| --- | --- | --- | --- |
| `domain_kg_list_kgs` | Cursor, limit, lifecycle filter | Authorized catalog summaries | Organization member |
| `domain_kg_get_kg` | `kg_id` | Configuration, visible statistics, capabilities | Reader; sensitive fields owner only |
| `domain_kg_create_kg` | Name, domain profile, approved placement ID, idempotency key | KG and provisioning job | Organization admin |
| `domain_kg_list_materials` | `kg_id`, material filters, cursor, limit | Visible material summaries | Reader |
| `domain_kg_get_material` | `kg_id`, `material_id`, optional version | Metadata, versions, permitted source blocks | Reader |
| `domain_kg_add_material` | `kg_id`, completed `upload_id` or inline text, metadata, idempotency key | Material ID, version ID, job ID | Editor |
| `domain_kg_update_material` | IDs, new input, expected active version, idempotency key | Staged version and job | Editor |
| `domain_kg_remove_material` | IDs, action `exclude` or `delete`, expected revision, dry run, idempotency key | Impact or committed removal receipt | Editor |
| `domain_kg_restore_material` | IDs, expected revision, idempotency key | Restored visibility or reprocessing job | Editor |
| `domain_kg_get_job` | `kg_id`, `job_id` | Stage, progress, warnings, actionable failure | Current material access; owner for KG jobs |
| `domain_kg_search` | Search request below | Evidence, paths, citations, diagnostics | Reader in every requested KG |
| `domain_kg_get_node` | `kg_id`, entity ID, filters | Visible observations, facts, support counts | Reader |
| `domain_kg_get_subgraph` | `kg_id`, seed IDs, hops, filters, node/edge caps | Bounded visible subgraph and evidence links | Reader |
| `domain_kg_update_node` | `kg_id`, `entity_id`, expected revision, name/type/description/aliases/tags changes, curation scope, reason, idempotency key | Revision and projection job receipt | Editor |
| `domain_kg_update_relation` | `kg_id`, `assertion_id`, expected revision, typed changes, reason, idempotency key | Revision and projection job receipt | Editor |
| `domain_kg_curate_node` | IDs, action `suppress`, `restore`, `merge`, or `split`, expected revision, reason, dry run, idempotency key | Curation receipt or impact | Editor |
| `domain_kg_get_revisions` | `kg_id`, visible target ID, cursor, limit | Authorized before/after history and publication state | Reader |
| `domain_kg_rollback_revision` | `kg_id`, revision ID, expected current revision, reason, idempotency key | New compensating revision receipt | Owner |
| `domain_kg_update_kg` | `kg_id`, expected revision, mutable configuration, idempotency key | Updated configuration or reindex job | Owner |
| `domain_kg_set_provider` | `kg_id`, approved `profile_id`, expected provider-selection revision, idempotency key | Selected profile ID and selection revision | Owner |
| `domain_kg_list_members` | `kg_id`, cursor, limit | Current grants | Owner |
| `domain_kg_set_member_role` | `kg_id`, member or group ID, role or revoke, expected revision, idempotency key | New policy revision | Owner |
| `domain_kg_get_health` | `kg_id` | Redacted availability, index freshness, job summary | Reader; detailed diagnosis owner only |

### Console parity tools

Additional typed tools expose the existing console services:

| Area | Tools (prefix `domain_kg_`) |
| --- | --- |
| Catalog discovery and lifecycle | `list_storage_profiles`, `list_domain_profiles`, `get_schema`, `delete_kg`, `restore_kg` |
| File/folder and source management | `upload_file`, `add_folder`, `list_managed_materials`, `update_material_metadata`, `purge_material`, `get_material_acl`, `set_material_acl`, `list_images`, `read_source_file` |
| Jobs and batches | `list_jobs`, `retry_job`, `cancel_job`, `batch`, `get_batch` |
| Graph and evidence | `get_graph`, `get_edit_context`, `get_citation` |
| Imports and owner operations | `plan_import`, `execute_import`, `submit_operation`, `get_backup`, `get_backup_page`, `read_backup_file`, `get_export`, `read_export_file`, `get_restore`, `get_projections`, `repair_material` |
| Owner diagnostics | `get_usage`, `get_usage_history`, `get_audit`, `get_operation_metrics` |
| Model settings | `get_model_settings`, `configure_models`, `approve_model`, `check_models`, `discover_models`, `set_default_model` |
| Team management | `list_team`, `admit_member`, `admit_service`, `change_member`, `create_group`, `set_group_member` |
| Approved infrastructure | `list_backends`, `list_approved_backend_references`, `register_backend`, `validate_backend`, `register_storage_profile` |

`update_kg` accepts optional `name` and lifecycle `state` (at least one), preserving
existing revision/idempotency semantics. `delete_kg` and `restore_kg` require the
same owner-only expected KG revision and idempotency key. Deletion sets `deleted`,
increments visibility/policy revisions and fences reads and workers; original data
and grants are retained. Default catalog pages omit deleted KGs. An explicit
`lifecycle=deleted` lists them only to current owners; restoration retains the
previous lifecycle state and active-owner invariant. This is not physical erasure.

`add_folder` accepts 1–100 distinct safe relative paths with completed uploads or
inline text, at most 1 MiB per manifest. Replacements explicitly bind material/active
version IDs. The organization/KG/actor/key binds an immutable manifest digest, and
per-file child commands reuse normal material idempotency. Each file returns its
own result, including failures; replay resumes accepted work without duplicates.
No paths are resolved against the server filesystem. Batch controls reuse the same
BatchOperations service as the console.

`submit_operation` uses the discriminated OwnerCommand for backup, export, restore,
generation, maintenance, retention and restore_cleanup. It uses RestoreManagement
for restoration and OwnerOperations for other commands, with unchanged snapshots,
retention and publication checks. `get_edit_context` exposes the console's scoped
curation revision lookup without exposing its internal ReadScope.

`configure_models` tests synthetic Hello and embedding before saving by default;
a failed check returns `ready=false`, component diagnostics and no saved revision.
`check_models` and `discover_models` may call providers; all three conservatively
advertise `idempotentHint=false` and `openWorldHint=true`. No source text is sent
by these tests. Approved profile selection makes no model calls. Secrets are never
returned by model inspection; endpoint/key-presence fields require owner/operator.
Stale selection retains its revision and safe warnings so an authorized Agent can
repair configuration.

Read operations use `readOnlyHint=true`, `destructiveHint=false`, and `idempotentHint=true`. Add and update operations use `readOnlyHint=false`; their idempotency hint is true only because a required idempotency key is enforced. Removal, node/relation edits, curation, rollback, grant changes, and configuration updates use `destructiveHint=true` conservatively. Provider-backed ingestion and search use `openWorldHint=true`; the other tools use false. Inspect installed SDK support for each annotation in Task 1.

Optional MCP resources expose authorized schema and material metadata, with opaque URIs such as `domain-kg://<kg_id>/materials/<material_id>`. Resource reads run the same current policy checks as tools. Do not advertise a resource unless the selected SDK and target clients support it.

## HTTP routes

Console batch management uses `POST /admin/kgs/{kg_id}/batch` and
`GET /admin/kgs/{kg_id}/batch/{batch_id}`. A command contains `operation`
(`delete`, `restore`, `process`, `retry`, `cancel`), an idempotency key and 1–100
distinct typed targets. Source targets require their expected revision; processing
accepted sources also binds the selected active version. Each item passes its
existing service authorization and lifecycle checks independently. Batch command
identity, submission leases and item receipts are scoped by organization, KG and
verified initiating actor. Only that actor can inspect its current-authority result.
Batch commands cannot select a target in a different KG, even when the actor has
access to both. Permanent purge is not a batch operation.

`ReplaceMaterial.expected_revision` is an optional additional metadata revision
guard. When absent, existing replacement command fingerprints remain unchanged.
Reprocessing uses this guard alongside `expected_active_version`, and creates a
new version through the normal publication protocol.

| Routes | Purpose |
| --- | --- |
| `GET /me`, `GET /organizations/current` | Current identity and organization context |
| `GET/POST /members`, `PATCH /members/{id}` | Admit or suspend organization members; never create passwords |
| `GET/POST /groups`, `PUT/DELETE /groups/{id}/members/{member_id}` | Local group management |
| `GET/POST /service-identities`, `POST /service-identities/{id}/revoke` | Scoped automation identities |
| `GET/POST /kgs`, `GET/PATCH/DELETE /kgs/{kg_id}`, `POST /kgs/{kg_id}/restore` | Catalog, creation, configuration, recoverable deletion/restoration |
| `PUT /kgs/{kg_id}/provider` | Owner selection of an operator-approved model profile; same revision and idempotency checks as MCP |
| `GET/PUT/DELETE /kgs/{kg_id}/members/{subject_id}` | Per-KG grant management |
| `GET/PUT /kgs/{kg_id}/materials/{material_id}/acl` | Restricted-material grant management |
| `POST /kgs/{kg_id}/uploads` | Stream bytes into service-owned staging; return completed upload ID |
| `GET/POST /kgs/{kg_id}/materials` | List and ingest |
| `GET/PATCH /kgs/{kg_id}/materials/{material_id}` | Inspect or update metadata |
| `POST /kgs/{kg_id}/materials/{material_id}/versions` | Submit replacement with expected active version |
| `POST /kgs/{kg_id}/materials/{material_id}/remove` | Exclude or tombstone; supports dry run |
| `POST /kgs/{kg_id}/materials/{material_id}/restore` | Reverse exclusion or recover within retention |
| `POST /kgs/{kg_id}/materials/{material_id}/purge` | Owner-only retained-data purge job |
| `GET /kgs/{kg_id}/jobs/{job_id}`, `POST .../cancel`, `POST .../retry` | Durable job management |
| `POST /search`, `GET /kgs/{kg_id}/nodes/{entity_id}`, `POST /kgs/{kg_id}/subgraph` | Retrieval and graph inspection |
| `PATCH /kgs/{kg_id}/nodes/{entity_id}`, `PATCH /kgs/{kg_id}/relations/{assertion_id}` | Revision-aware node and relationship changes |
| `POST /kgs/{kg_id}/nodes/{entity_id}/curate` | Audited suppression, restore, merge, and split |
| `GET /kgs/{kg_id}/revisions`, `POST /kgs/{kg_id}/revisions/{revision_id}/rollback` | Scoped history and compensating rollback |
| `POST /kgs/{kg_id}/imports`, `POST .../exports` | Validated imports and scoped export jobs |
| `POST /kgs/{kg_id}/backups`, `POST .../restores`, `PATCH /kgs/{kg_id}` (archive) | Owner maintenance workflows |
| `POST /kgs/{kg_id}/reindex`, `GET .../statistics`, `GET .../audit` | Index generation, scoped counts, and audit |
| `GET/POST /backends`, `POST /backends/{id}/validate`, `PATCH /backends/{id}` | Operator-only registered backend management |
| `GET/POST /storage-profiles`, `PATCH /storage-profiles/{id}` | Operator-managed MongoDB/Neo4j/Qdrant/object-store bundles |
| `GET /kgs/{kg_id}/projections`, `POST /kgs/{kg_id}/repair` | Owner projection readiness and repair jobs |
| `GET /kgs/{kg_id}/usage` | Current-owner KG daily provider counters, reservations, limits and UTC reset; no organization aggregate |
| `POST /kgs/{kg_id}/repairs/curations` | Current-owner repair of one accepted human revision using sealed vectors; expected publication and idempotency key required |
| `POST /kgs/{kg_id}/retention` | Current-owner cleanup of explicitly selected expired packages, settled incomplete backups, or safely retired index generations |
| `GET /citations/{citation_id}` | Resolve source location or stream authorized original |

The `...` abbreviations in this table preserve the preceding KG or job prefix; actual OpenAPI paths must be fully expanded. `POST /kgs/{kg_id}/restores` restores a backup into a newly allocated KG with a new ID. Uploads require current editor access and reserve quota; completion verifies hash and length. Unclaimed uploads expire after 24 hours. Do not expose object-store credentials or accept a client-selected object key.

Retention requires `expected_published_revision`, an idempotency key and an explicit
`resources` selection: `backups`, `exports`, `both`, `incomplete_backups` or
`generations`. The service selects and freezes its own exact namespace-bound
inventory under a grace policy. It accepts no client paths, store names, backend
queries or deletion cutoffs. Pending/uncertain writes and retained reads prevent
collection; a timed-out lease alone does not prove that remote I/O has stopped.

## Search request

```json
{
  "kg_ids": ["11111111-1111-4111-8111-111111111111"],
  "query": "Which project decisions cite the storage evaluation?",
  "mode": "fast",
  "filters": {
    "material_ids": [],
    "exclude_material_ids": [],
    "media_types": ["application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
    "tags_all": ["architecture"],
    "tags_any": [],
    "authors": [],
    "sources": [],
    "languages": ["en"],
    "entity_ids": [],
    "exclude_entity_ids": [],
    "entity_types": ["Project", "Decision"],
    "predicates": ["SUPPORTED_BY"],
    "min_confidence": 0.7,
    "support_statuses": ["verified", "manual"],
    "created_from": null,
    "created_before": null,
    "updated_from": null,
    "updated_before": null,
    "version_id": null
  },
  "top_k": 10,
  "max_hops": 2,
  "max_nodes": 200,
  "max_edges": 400,
  "max_context_chars": 24000,
  "rerank": false,
  "generate_keywords": false,
  "allow_partial": false
}
```

Modes are `fast` (default), `deep`, `vector`, `lexical`, and `graph`. Fast uses vector/text fusion with no graph expansion, generative keyword/query LLM, or reranking; embedding calls are permitted. Deep uses scoped LightRAG graph/vector orchestration plus lexical recall. `rerank` and `generate_keywords` may be true only in deep mode. `max_hops` applies to deep/graph modes and is ignored, never expanded, in fast/vector/lexical modes. Unsupported mode/filter combinations return `UNSUPPORTED_FILTER`.

Require 1–10 explicit KG IDs and query length 1–8,000 characters after trimming. `top_k` is 1–50. `max_hops` is 0–2; `max_nodes` is 1–200 and `max_edges` is 0–400 per KG. `max_context_chars` is 1,000–48,000 and applies to the final merged evidence text. These hard limits also constrain graph-only inspection. A request with more seed nodes than its cap fails validation.

Empty lists impose no restriction. Values inside a filter list are alternatives, except `tags_all`; different populated fields are conjunctive. Source timestamps are distinct from ingestion timestamps; the fields above refer to material ingestion metadata. Time ranges are UTC and half-open. Confidence is between 0 and 1 and restricts supported assertion candidates. Without assertion filters, text-only chunks remain eligible. With `entity_ids`, `entity_types`, `predicates`, or `min_confidence`, every branch must return only chunks with qualifying visible assertion support. Explicit `version_id` requires one material in one KG and an authorized retained version; the default is active versions only.

`min_confidence` defaults to null. `support_statuses` defaults to `["verified", "manual"]` for assertion eligibility and does not by itself require a text-only chunk to have assertions. Here `verified` means its source span and schema were validated; it does not establish that the source's claim is true. `manual` means an immutable, authorized human curation record with actor and reason, not a document-verified claim. Other states are `imported_unverified` and `rejected`; rejected supports are visible only in authorized curation views and cannot be requested as search evidence.

## Search response and evidence contract

`SearchResponse` contains `request_id`, `mode`, `status` (`ok` or `partial`), `results`, `applied_filters`, `kg_revisions`, `policy_revision`, `branch_status`, `projection_generations`, `warnings`, and `truncation`. Each result has `kind` (`source` or `manual`), `kg_id`, text, citation IDs, visible entity and assertion IDs, a fused rank score, method ranks, accepted source/curation revision IDs, and supporting graph paths. Source results require `material_id`, `version_id`, and `chunk_id`; manual results require `curation_revision_id` and provenance, with nullable source fields when no source material exists. A citation includes its true source location or curation actor/reason and an authorized resolver route. Do not invent document spans for manual assertions. Every graph edge has a visible support ID.

Served graph steps also carry the accepted predicate and polarity; both are authenticated against their sealed support before return. Traversing an edge in reverse does not reverse its assertion or turn a negative/uncertain assertion into a positive one. A path can reference other visible supports outside the selected text; clients presenting a self-contained explanation must resolve all those supports. The offline path experiment instead omits any path whose source quotes are absent from its selected evidence.

Graph method ranks record recall provenance, not an extra independent vote for an existing vector/text hit. Full passages are preferred within the text budget, with later fitting candidates filling unused slots. If an excerpt is unavoidable, `truncation` is true and the excerpt carries no graph path. A validated source span proves source correspondence, not semantic correctness of the extracted predicate.

Source-scoped manual results inherit their target material's metadata and access restrictions. A source-less public curation record is eligible only without populated material-specific restrictions such as material IDs, media types, material tags, authors, or source/version filters. Entity/predicate filters and support-status restrictions still apply. The eligible-scope resolver must use these rules consistently across all branches.

`kg_revisions` maps each KG to its published revision. A read resolves one accepted `PublishedView` and current `ReadScope` per KG. Graph/vector candidate records must belong to the accepted version/curation sets and index generation. The [publication protocol](revisions-and-publication.md) defines readiness and recovery; there is no transaction across MongoDB, Neo4j, and Qdrant or global snapshot across KGs. Responses are request-bound; an expired cursor or superseded generation requires a new search. Hidden supports, raw provider payloads, database connection details, and global corpus counts never enter the response.

Per-branch status is `ok`, `empty`, `unavailable`, `unsupported`, or `skipped`; graph and reranking are skipped in fast mode. A partial result is allowed only when requested and at least one required retrieval branch and one requested KG produced a valid response. All requested scopes were authorized before fan-out. Authority and publication-integrity failures fail the affected KG, and are never converted to partial evidence inside it. A complete failure returns an error. An empty visible result has `status=ok` and `results=[]`.

## Typed domain interfaces

Define common models/protocols in `src/domain_kg/contracts.py`. All I/O interfaces are async. Authentication/policy constructs immutable `AccessContext`; `PublishedView` pins accepted source and curation mappings, and `ReadScope` adds current visibility and requested filters. Clients cannot construct any of them.

```python
class Policy:
    async def resolve_access(self, principal: Principal, kg_ids: list[UUID], action: Action) -> AccessContext: ...

class KGStoreRouter:
    async def for_kg(self, access: AccessContext, kg_id: UUID) -> KGStores: ...

class EligibilityResolver:
    async def resolve(self, access: AccessContext, request: SearchRequest, kg_id: UUID) -> ReadScope: ...

class MaterialService:
    async def add(self, access: AccessContext, command: AddMaterial) -> IngestReceipt: ...
    async def replace(self, access: AccessContext, command: ReplaceMaterial) -> IngestReceipt: ...
    async def remove(self, access: AccessContext, command: RemoveMaterial) -> RemovalReceipt: ...

class Parser:
    async def parse(self, blob: BlobRef, options: ParseOptions) -> ParsedDocument: ...

class Extractor:
    async def extract(self, chunk: ParsedChunk, schema: DomainSchema) -> ExtractionBatch: ...

class ProjectionCoordinator:
    async def stage(self, access: AccessContext, batch: RevisionBatch) -> ProjectionReceipt: ...
    async def verify(self, access: AccessContext, receipt: ProjectionReceipt) -> ReadinessReport: ...
    async def publish(self, access: AccessContext, command: PublishRevision) -> RevisionReceipt: ...

class RevisionService:
    async def update_node(self, access: AccessContext, command: NodeUpdate) -> RevisionReceipt: ...
    async def update_relation(self, access: AccessContext, command: RelationUpdate) -> RevisionReceipt: ...
    async def rollback(self, access: AccessContext, command: RollbackRevision) -> RevisionReceipt: ...

class RetrievalBranch:
    async def retrieve(self, scope: ReadScope, request: SearchRequest) -> list[EvidenceCandidate]: ...

class RetrievalService:
    async def search(self, access: AccessContext, request: SearchRequest) -> SearchResponse: ...

class Importer:
    async def plan(self, access: AccessContext, source: ImportSource) -> ImportPlan: ...
    async def execute(self, access: AccessContext, plan: ImportPlan) -> JobReceipt: ...
```

`KGStores` bundles scoped MongoDB authority, Neo4j/Qdrant projection clients, and a blob client; it does not offer a cross-store transaction. `RevisionBatch` references immutable canonical records, attempt ID, generation, and required projections. `ProjectionReceipt` binds backend acknowledgements to the attempt/content manifest. `PublishRevision` includes those receipts, lease token, and expected revisions; the MongoDB compare-and-set is the acceptance point.

`NodeUpdate` and `RelationUpdate` include stable target IDs, allowed typed changes, expected target revision, curation scope, reason, and idempotency key. Descriptive edits refresh affected vectors; nonembedded metadata can publish without embedding work. Suppression uses an immediate authoritative fence. Edits that change a claim's meaning retain original evidence independently and label the changed claim as manual provenance. `RevisionReceipt` distinguishes pending projection work from an accepted published revision and returns a job ID when asynchronous.

LightRAG adapters implement extraction/retrieval with source IDs and the immutable read scope. Per-request mutable global workspace/filter state is forbidden. The implementation plan assigns every model to a task before downstream use.

## Errors and job states

Domain errors contain `code`, safe message, retryability, and `request_id`. Use `UNAUTHENTICATED`, `FORBIDDEN`, `RESOURCE_NOT_ACCESSIBLE`, `VALIDATION_ERROR`, `UNSUPPORTED_FORMAT`, `UNSUPPORTED_FILTER`, `QUOTA_EXCEEDED`, `VERSION_CONFLICT`, `IDEMPOTENCY_CONFLICT`, `CURSOR_EXPIRED`, `BACKEND_UNAVAILABLE`, `PROVIDER_UNAVAILABLE`, `EVIDENCE_VALIDATION_FAILED`, `POLICY_CHANGED`, `REVISION_NOT_READY`, and `PROJECTION_INCONSISTENT`.

HTTP maps unauthenticated to 401, insufficient scopes to 403, inaccessible resource to 404 without resource details, validation to 422, version or cursor conflicts to 409, quota exhaustion to 429, and backend/provider unavailability and an unverifiable published projection to 503. `REVISION_NOT_READY` is 409 when explicitly requesting an unpublished revision; normal asynchronous edits return an accepted job receipt. MCP exposes domain failures as tool errors with the same safe code and structured details, using the selected SDK's supported error mechanism. Do not encode failure as successful empty evidence.

Job stages are `queued`, `validating`, `parsing`, `chunking`, `extracting`, `resolving`, `embedding`, `projecting`, `verifying`, `publishing`, and terminal `ready`, `ready_with_warnings`, `failed`, or `cancelled`. A recoverable cross-store failure can enter nonterminal `repair_required` with a repair job reference. A receipt returns immediately with IDs; clients poll `domain_kg_get_job` or HTTP. MCP request cancellation stops polling or search, not an already accepted durable ingestion job. Job cancellation uses the explicit authorized job operation.

`ready_with_warnings` can include server-generated
`extraction_rejected_entities:N` and `extraction_rejected_assertions:N` counts.
After one corrective response, each retained fact still passes the complete
source/schema validator. Invalid relations are discarded whole, and a rejected
entity cannot remain an endpoint. Counts sum across source windows and output
splits; the original remains intact. These warnings are not accepted model fields
and do not grant access to rejected evidence or change publication readiness.

PDF `ParseOptions.ocr` defaults to true; false explicitly preserves the native-only
path with `OCR_REQUIRED` warnings. `SourceLocation.text_origin` is `native` or
`ocr`; only PDF can have OCR provenance. Omit the native default during
serialization so existing sealed source/projection payloads remain compatible.
OCR spans quote the immutable recognized text, retain physical PDF page numbers,
and still pass the canonical evidence validator. `OCR_APPLIED` warns that
recognition may be inaccurate; `OCR_NO_TEXT` reports a page without recognized
text. Missing/failed local OCR is an explicit actionable job failure. Ingestion
never downloads language models or sends pages to a cloud OCR service.

## Owner projection-work metrics

`GET /api/v1/kgs/{kg_id}/operations/metrics` returns a typed `ProjectionWork`
namespace, observation time, bounded sample count, pending/failed/repair-required
counts, overflow flag, and optional oldest pending admission time/whole-second age.
It requires current owner `configure` authorization before and after reading.
Counts describe at most the oldest 1,000 matching projection-bearing jobs; an
overflow means lower bounds. It exposes no job inputs or source content. Queue age
is independent of verified accepted graph/vector readiness. Unknown/corrupt selected
rows fail closed rather than becoming an empty healthy result. Responses are private.

### Daily usage without a local cap

`GET /kgs/{kg_id}/usage` retains owner-only scoped counters when the operator sets
`budgets.knowledge_base` to `null`. Its `limits` and `available` fields are then
`null`, meaning no KG daily cap, while finite configurations retain their existing
objects. An organization cap is independently configured; both policy scopes must
be null for entirely uncapped local daily usage. Authorization, reservations and
usage settlement remain mandatory.


## Image sources and interpretation provenance

Image-aware parsed documents retain an `images` manifest whose raw and preview
BlobRefs belong to the exact organization/KG/material/version. Binary image
artifacts use the normal source-owned job artifact lifecycle. No original bytes,
base64 data, object keys or credentials appear in material/citation responses.

`GET /api/v1/kgs/{kg}/materials/{material}/versions/{version}/images` returns
bounded metadata and protected URLs. Append `/{image_id}` for an original
attachment or `/{image_id}/preview` for its isolated PNG preview. IDs resolve only
inside the caller's current eligible accepted/retained parent version. Streaming
rechecks the current publication/visibility fence. Removal blocks these reads.

`SourceLocation.text_origin` is `native`, `ocr`, or `vision`; defaults remain
omitted to preserve old sealed payloads. Vision locations bind `image_id`, model
name, profile ID and revision. Supports/candidates/citations use
`model_interpreted` for generated visual observations. Default retrieval includes
these explicitly labeled observations; `support_statuses=["verified"]` excludes
them, including node labels/descriptions/counts. Final response checks recompute
status from accepted spans; a candidate cannot promote image text to verified.
