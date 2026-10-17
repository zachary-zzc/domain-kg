# Implementation progress

Updated 17 October 2026. Execution is native in this chat, following the current NoSQL design and the task dependency order. Each completed task requires a laptop smoke test; expensive OCR, model training, load tests, and large-corpus benchmarks are outside this run. The user separately approved a bounded native small-LLM smoke; its measured memory is separate from the Docker database ceiling.

| Task | Status | Verification |
| --- | --- | --- |
| 1: LightRAG and NoSQL compatibility spike | Complete | 17 tests pass against the real local stores; lint and mypy pass. Kernel RAM/swap limits verified. |
| 2: Domain contracts and namespace integrity | Complete | 47 smoke tests pass; full regression 59 tests; lint and strict mypy pass. |
| 3: OIDC identity and organization accounts | Local checks pass; external gate pending | 19 smoke tests pass, including signed issuer fixtures and real MongoDB transactions. A real development IdP login is still required. |
| 4: Shared policy and scoped repositories | Complete locally | 18 smoke tests pass; full suite 81 tests; real MongoDB, Neo4j and Qdrant scope checks. |
| 5: Catalog and registered storage profiles | Complete locally | 14 smoke tests pass, including three KGs across independent physical graph/vector deployments run serially. |
| 6: Original files and material submission | Complete locally | 15 smoke tests pass; full regular suite 100 passed, one physical drill skipped; lint/mypy pass. |
| 7: Durable MongoDB jobs and recovery | Complete locally | 18 smoke checks pass; full suite 112 passed, one physical skip; lint/mypy pass. |
| 8: Structured parsing and chunking | Complete for laptop profile | 20 smoke checks pass; full suite 127 passed, one physical skip; native Docling PDF and bounded text/XML decoders. OCR quality remains outside this run. |
| 9: Schema-driven LightRAG extraction | Local checks pass; extraction quality gate open | 20 mock-provider checks; approved 0.8B model failed both full grounding cases without publication. The separately approved 0.6B embedding model passed its real native smoke; extraction quality remains required. |
| 10: Staging and publication | Complete locally | 33 smoke checks; full regular suite 170 passed, one physical skip; durable publication/repair verified against real stores. |
| 11: Scoped graph inspection and traversal | Complete locally | 13 smoke checks; regular regression 176 passed, one physical skip; two additional reproduced integrity failures fixed and verified. |
| 12: Filtered vector and lexical retrieval | Complete locally | 22 smoke checks; regular regression 184 passed, one physical skip; current metadata/support filters, scoped native adapter and embedding-space fences. |
| 13: Federated fast/deep search | Complete locally | 34 smoke checks; regular regression 202 passed, one physical skip; native mix, two-KG fusion, partial failure rules and model-specific source windows verified. |
| 14: Persistent edits and material changes | Local verification passed | Required combined regression: 17 tests in 618.31 seconds. Dedicated reversible identity regression: 11 tests in 521.58 seconds; 128 unit checks. Docker database cap remains 1.476 GB. |
| 15: Shared MCP and HTTP surfaces | Verified initial transport milestone; remaining gates open | 24 signed-identity/transport/owner-operation integration checks in 112.15 seconds; 139 unit/protocol checks in 3.99 seconds; Ruff and strict mypy (124 source files). Current/retained source chunks and suppression: 3 additional checks in 46.74 seconds. Later operation routes remain. |
| 16: Legacy import and upstream compatibility | Complete locally | Final combined laptop smoke: 59 passed in 395.61 seconds. Worker, HTTP admission, recovery, trust, stable identities and purge verified with synthetic data. No real corpus migration performed. |
| 17: Team console | Complete locally | Final console smoke: 43 passed in 354.84 seconds; representative browser flows observed. Optional file-picker walkthrough remains unverified; external OIDC remains separate. |
| 18: Consistent backup, repair and deployment | In progress | Owner-operation/audit, retained object storage, capture pins and sealed packages verified. Focused backup/capture/owner-job gate: 14 passed in 213.73 seconds; 202 unit/protocol checks passed in 4.04 seconds. Owner console/HTTP backup controls and a model-free backup worker passed four focused regressions in 55.68 seconds. Scoped JSONL/CSV/GraphML exports, trust/withdrawal gates and owner downloads are also verified. Restore, generation migration, budgets, physical expiry cleanup and deployment artifacts remain. |
| 19: Evaluation and release candidate | Not started | Held-out evaluation, final release checks and team pilot follow Task 18. No production readiness claim. |

The main plan has 19 tasks: 14 have completed their local task gates; Tasks 3,
9 and 15 retain external identity/model/client acceptance gates; Tasks 18 and 19
remain development work. Its 57 coarse checklist items contain 44 verified and
13 unchecked items. These counts are checklist status, not a percentage of work
or an estimate of time remaining. See [the current review](status-review.md).

The active laptop budget is [config/laptop-smoke.json](../config/laptop-smoke.json).
Ordinary checks use deterministic synthetic providers and make zero live model
calls. Use `python3 scripts/smoke.py --quick --offline` for changed budget/window
checks; a quick smoke does not replace a task's integration acceptance gate.

Implemented the locked Python environment, protected mounted MCP transport, source-preserving LightRAG extraction boundary, explicit scoped projection probe, real-store test fixtures, resource-limited Compose stack, private development environment generator, and reproducible smoke runner. The default MCP verifier denies tokens until external OIDC is implemented.

A live loopback smoke also verified `/healthz` returns 200 and unauthenticated `/mcp` returns 401 with a Bearer challenge. The development server shut down cleanly afterward.

Run the available checks from the repository root:

```bash
uv sync --locked
python3 scripts/smoke.py --offline
```

Docker storage was emptied at the user's explicit request: images, containers, volumes, and build cache all reached zero, leaving about 57 GiB free in the Docker data filesystem. Only this project's pinned smoke images are being downloaded again. Run the complete Task 1 smoke:

```bash
python3 scripts/smoke.py --task 1
```

The four services have hard limits of MongoDB 512 MiB, Neo4j 512 MiB, Qdrant 192 MiB, and S3 192 MiB: 1,408 MiB (1.476 GB) combined, below the user's 1.5 GB RAM cap. Swap is disabled. The runner rejects unbounded RAM, swap allowance, or an over-budget total, and verifies the running Docker limits before tests. CPU limits total 2.5 cores; image pulls run serially.

The runner stops services it started after the test; `--keep-services` keeps them running for inspection. It retains volumes and never prunes images during ordinary smoke runs. Existing running services are left running. Credentials live in ignored `.env`, generated with restrictive file permissions.

Task 1's final real-store smoke passed all 17 tests in 7.86 seconds. Three Qdrant SDK warnings identify intentional HTTP use on loopback; shared deployment TLS remains a later release requirement. The kernel reported the exact configured limits, zero swap allowance, no OOM kills, and no container restarts.

The retry fixed an unsupported Neo4j Community metrics setting, macOS system-proxy routing of local S3 traffic, and a SeaweedFS disk-volume-slot limit. The S3 probe bypasses system proxies for loopback while retaining the SDK's default checksum validation. Four bounded 64 MiB volume slots permit the internal volume and bucket collections without increasing RAM. Source decisions and verification limits are in [decision 0001](decisions/0001-runtime-and-lightrag.md).

Task 2 adds transport-independent typed contracts, immutable accepted/read scopes, honest source/manual provenance, and exact-attempt publication receipt validation. Scoped canonical inserts check every reference against its organization and KG. Mongo validators and compound indexes enforce shape and uniqueness; reconciliation checks canonical references and graph/vector mappings in both directions. These are integrity primitives; current policy enforcement follows in Task 4 and publication coordination in Task 10.

Reproduce the Task 2 laptop gate with `python3 scripts/smoke.py --task 2`. Its fixtures contain only small synthetic records and three-dimensional vectors. Source text preserves whitespace so citation offsets remain exact. A publication cannot be inferred from a staging write.

Task 3 implements external JWT verification and JWKS refresh, protected-resource discovery, exact issuer/subject identity, admission/suspension, transactional last-admin protection, groups, bounded service identities, and browser sessions with PKCE/nonce/state/CSRF checks. Identity cursors bind the actor, organization and policy revision. The official MCP client successfully used the real verifier with an isolated signed fixture; suspending that member immediately denied the same token.

Task 3's complete suite passed 73 tests; Ruff and strict mypy passed. [Development identity instructions](identity-development.md) describe the remaining real-provider login gate. No development issuer/client has been supplied, so Task 3 is not marked complete. At the user's request, implementation continues using the locally verified identity boundary; the real login gate still blocks release acceptance. The test-only issuer is never enabled in the application runtime.

Task 4's complete suite passed 81 tests in 21.09 seconds, including interleaved principals querying real graph/vector stores, empty eligible scopes, restricted ACL intersection, action scopes, service revocation, concurrent last-owner removal, and revocation between policy reads. Its laptop smoke passed 18 tests. Current policy and visibility revisions are rechecked before results return. Organization-admin and operator flags do not grant KG data access. Five loopback Qdrant warnings remain a documented development limitation.

The smoke stack remains capped at 1.476 GB with swap disabled. Repeated disposable schema tests reached MongoDB's inherited file-descriptor limit; its volume recovered after applying the official 64,000 open-file setting. No RAM limit was raised. See [resource notes](development-resources.md). Smoke containers remain up during continued implementation and are stopped when work pauses.

Task 5 adds approved connection references, operator registration, bounded driver pools, namespace provisioning, explicit owner appointment, permission-filtered KG lists, lifecycle controls, redacted health, and mounted catalog HTTP routes. Concurrent KG creation replays one accepted reservation; profile/backend detachment is serialized with placement claims. Inventory and catalog cursors enforce limits and bind current policy.

The regular full suite passed 89 tests in 45.87 seconds with the physical drill intentionally skipped. The final Task 5 smoke explicitly ran that drill: all 14 checks passed in 83.90 seconds, and the primary stack was restored. Its 17 warnings identify loopback HTTP and the intentionally offline primary compatibility check. All operations kept four containers totalling 1.476 GB. [Storage-profile details](decisions/0003-storage-profiles.md) record the server-approved replica-set databases and independent graph/vector endpoints. No real operator invitation was issued.

Task 6 preserves immutable original bytes with bounded streaming/multipart S3,
SHA-256 and MIME/length validation, exact uploader ownership, quota reservations,
24-hour expiration, and current-authorized attachment downloads. Material/source
versions, ACLs, jobs, outbox, quota conversion and idempotent receipts commit in
one Mongo replica-set transaction, including across selected logical databases.
Replacement stages a new version without changing the accepted source. The
original-file receipt does not claim graph publication. See [material API and
verification](materials.md).

Task 6 smoke passed 15 checks in 31.21 seconds. The final regular suite passed
100 tests in 57.29 seconds, with the physical drill skipped; Ruff and strict mypy
passed 38 source files. Reproduced failures led to sequential multipart uploads,
upload leases, stable millisecond receipts, fixture-owned projection cleanup,
and direct approved Qdrant connections that ignore ambient proxies. A targeted
proxy regression passed after failing with an unreachable proxy. The total RAM
cap is unchanged: Mongo now has 576 MiB and Neo4j 512 MiB (heap128/pagecache32),
Qdrant192 and S3128. Mongo primary readiness is checked from host Python; its
periodic Node shell health process was removed. Earlier OOM failures and limits
are recorded in [resource notes](development-resources.md); the final smoke and
regular suite pass with the corrected configuration.

Task 7 implements durable atomic claims, expiring leases, fresh attempt IDs,
immutable checksum-backed S3 checkpoints, bounded retries, explicit cancellation,
current actor/source checks, and a separate per-KG publication fence. Preparation
can run concurrently; stale workers cannot update checkpoints or retain a
publication slot. A native single-job runner heartbeats and resumes compatible
preparation artifacts. An unconfigured/partial pipeline never claims graph
readiness; parsing/extraction/publication factories follow in Tasks 8–10.

The final regular suite passed 112 tests in 55.03 seconds with one physical skip;
the recorded Task 7 smoke passed 18 tests in 19.18 seconds. Ruff and strict mypy
pass 43 source files. Real byte corruption, missing artifact references, actor
revocation, source tombstones, competing publishers and interrupted requests are
covered. The native worker entry point refuses an incomplete pipeline before
claiming work. See [job recovery details](jobs.md).

For the fixed 1.476 GB service budget, fixtures now reuse schema/index definitions
while clearing every synthetic document between cases. This avoids repeated
indexed-database churn; real Mongo, graph/vector deployments, transactions and
namespace checks are retained. A documented allocator release rate returns
unused Mongo heap sooner. Qdrant compatibility and data requests now share the
same direct approved transport, including explicit rejection of incompatible
versions. [Resource notes](development-resources.md) record earlier warm-service
failures and the limits of these local correctness checks.

Task 8 preserves immutable decoded blocks and exact span offsets, physical PDF
pages, DOCX paragraphs/table rows, CSV records and JSON pointers. The local
profile uses model-free Docling native PDF parsing and deterministic decoders,
with 700-token/100-token-overlap limits and stable chunk IDs. Scans emit explicit
OCR warnings; model-based OCR/layout quality was not exercised under the user's
resource constraint. The parser runs offline in a bounded subprocess; the Mac
kernel sandbox's network denial, runtime and memory enforcement were verified
using tiny inputs. No Torch/OCR models were installed or downloaded.

The final regular suite passed 127 tests in 84.75 seconds with one physical skip.
Task 8 smoke passed 20 checks in 28.69 seconds; Ruff and strict mypy pass 48 source
files. Parsed/chunk snapshots have scoped Mongo/S3 ownership and checksums, and
resume without duplicate ledger records. Three preparation stages remain
explicitly pending graph publication. [Parser profile and verification](parsing.md)
describe limits, source locations, public fixtures, and the OCR boundary.

## Extraction and publication

Task 9's local extraction boundary passed 20 smoke checks and the regular suite
passed 142 tests at that step. Approved structured-provider configuration,
source/schema validation and real LightRAG 1.5.7 mapping preserve per-source
observations and independent supports. Actual approved-provider quality smoke
remains pending; no model is selected or paid request made by default.

Task 10 passed 33 local smoke checks and the regular suite passed 170 tests, with
one separately invoked physical drill skipped. It adds immutable graph/vector
attempts, read-back receipts, atomic Mongo acceptance and job completion,
concurrent replacement fences, idempotent acknowledgement recovery, retained
originals and provider-free repair. Accepted queries bind exact attempts rather
than source versions alone. The native nine-stage worker and repair-only worker
are verified. Manual curation production/read integration follows in Task 14.

The current resource allocation is Mongo512/Neo512/Qdrant192/S3192 MiB, totaling
1,408 MiB (1.476 GB), with swap disabled. Bounded S3 reader buffers, small filer
chunks and a shared writer budget passed the 50 MiB streaming boundary and warm
regression. The user explicitly approved the three previously named abandoned
fixture buckets; only those three were deleted and their absence verified.

Task 13 uses source-bound immutable scopes for all retrieval, explicit branch coverage, and rank fusion across approved embedding spaces. Optional query-model output can select literal terms or reorder at most 50 existing candidates; it cannot rewrite evidence. Full chunks, citations, annotations and graph steps are authenticated against accepted artifacts before reranking. The final smoke passed 34 checks in 226.14 seconds, and the regular regression passed 202 tests with one separately run physical drill skipped. Ruff and strict mypy pass 79 files. The approved native embedding model passed six tiny real requests and four bilingual comparisons at sampled peak RSS 965,410,816 bytes; it stopped after the smoke and the chat background model was restored. The private profile remains disabled for corpus access, and the chat model has not passed extraction quality. See [federation](federated-search.md), [source windows](model-windows.md), and [model configuration](model-proposal.md).

Task 14 has verified immediate source withdrawal, readiness-gated restoration and
provider-free restoration jobs; metadata edits avoid model/derived writes. Source
replacement reuses authenticated same-material windows and exact compatible vectors
while rebinding evidence to the new version. A 17-check lifecycle/race regression
passed in 261.58 seconds. Retained-version retrieval passed eight further checks in
208.81 seconds, including all search modes and current grant/removal fences. See
[incremental changes](incremental-changes.md).

Human node and relationship corrections now publish isolated graph/vector attempts,
preserve original evidence, and return source-scoped manual provenance. Typed
history and compensating owner rollback preserve independent support and later
changes to other fields. Description-only relationship changes remain annotations
on their verified original claims. Retained human claims can use opaque endpoints
without recovering removed source labels.

Immediate node suppression now fences all retrieval modes and history paging,
survives re-ingestion, and restores only after original/projection readiness checks.
Its final real-store regression passed 21 checks in 711.76 seconds, including races,
current-permission rollback and source/public scope boundaries. Never-published
originals now restore through a fully authorized reconstruction job while remaining
hidden until verified publication. The combined restoration/replacement regression
passed 18 checks in 358.74 seconds. Retained cleanup passed five recovery checks in
87.58 seconds and two source/curation checks in 90.80 seconds. It preserves B's
support, retained reads and human corrections, and independently public claims.
Reversible merge/split now passes its dedicated 11-check real-store regression. The combined laptop smoke passed 60 checks and hit one transient Qdrant disconnect; that case passed a fresh 67.43-second rerun. No container OOM or restart occurred. The fresh final combined command passed all 17 tests in 618.31 seconds, completing Task 14’s local gate. See
[curation](curation.md), [material changes](incremental-changes.md), and
[retained cleanup](material-purge.md).

The identity milestone preserves original assertions/spans, applies source-qualified effective IDs consistently to all search modes, records typed human history, and appends owner-only compensating controls. Suppression retains original group members through compensation and re-ingestion. Ruff, strict mypy (105 source files), and 128 unit checks pass. See [identity curation](identity-curation.md).

Task 15 now exposes all 22 typed application tools plus the protected probe, current-policy material metadata/schema resources, retrieval/curation/lifecycle/job routes, revisioned owner grants and material ACLs, authorized source/manual citation resolution, and measured projection readiness with durable repair receipts. Signed multi-organization credentials select current memberships per operation. HTTP and MCP preserve the same nullable result fields, reject caller identity arguments, sanitize invalid private input and enforce a 2 MiB JSON envelope cap. No external IdP/Inspector/target remote client acceptance is claimed; import/export and maintenance routes follow their domain services. See [transports](transports.md).

Task 16 now has a dedicated fenced import worker, with embedding-only preparation
and immutable unverified provenance across retrieval, graph views, material
inspection and citations. Default retrieval excludes the whole imported source.
Explicit curator commands can suppress imported targets; all raw export context
is then fenced. Five serial real-store checks passed in 192.89 seconds and
159 unit/protocol checks passed. Signed HTTP plan/execute admission passed 13 checks. The recovery, stable-ID
mapping, independent support, curation and native helper comparison passed nine
checks in 279.27 seconds; exact-source import/native purge passed two more in
62.06 seconds. Native replacement performs fresh validated extraction; unpublished
imports reconstruct as unverified under the current restoring actor. The final
combined Task 16 laptop command passed all 59 checks in 395.61 seconds, completing
its local implementation gate. Running hard database limits remained 1.476 GB
with swap disabled and no OOM or restart.
See [legacy imports](legacy-imports.md) and [the compatibility decision](decisions/0002-lightrag-compatibility.md).

Task 17's local console gate passed all 43 checks in 354.84 seconds, including
five-role acceptance, source management, durable work, current grants, curation,
rollback and operator isolation. The unit/protocol suite passed 183 checks in
5.60 seconds, with Ruff and strict mypy passing. Streaming browser forms enforce
their 64 KiB budget before buffering; owner readiness detects projection loss in
accepted unverified imports without promoting their trust. Docker remained at
1.476 GB hard limits with swap disabled, no OOM and no restart.

The laptop browser verified keyboard focus/skip links, cited search, node/edit
scope, owner/admin screens, reader denial and logout/back-cache behavior. Its
optional file-picker walkthrough was interrupted; the original temporary fixture
timed out, and the corrected restart shut down cleanly. That browser file-picker
path remains unverified; real API upload/checksum/publication checks passed.
External OIDC acceptance is still separate. See [console](console.md).
Task 18 continues with [publication-aware operations](superpowers/plans/2026-10-17-operations.md);
its checkboxes are future work, and no backup/restore or release readiness is claimed.
