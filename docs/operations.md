# Operations implementation status

Owner-operation admission and internally sealed backup packages have passed their
local gates. Public owner backup/export controls are implemented; restore,
generation migration and deployment acceptance continue under
[the operations plan](superpowers/plans/2026-10-17-operations.md).
The complete restore drill and release readiness remain unverified.

Backup/export intent has a typed, organization/KG-scoped operation record and a
real durable job. The record fixes the verified submitting actor and an accepted
publication/generation/visibility pointer. Admission, the wake-up event and a
redacted audit event commit together in MongoDB. It does not manufacture a
material version or imply a transaction with Neo4j, Qdrant or object storage.

Job inspection/control rechecks current owner authority and validates the complete
operation/job reference. Concurrent idempotent submission returns one job and one
admission audit event; a key already used by another job produces an intent
conflict. Cancel/retry records are atomic and idempotent. Current actor revocation
and stale worker leases stop execution.

Default ingestion workers do not claim owner-operation kinds. An explicit operation
executor is required; missing configuration produces a recoverable
`REVISION_NOT_READY` state before any source pipeline runs. A job cannot finish
without a verified package. Public backup/export transport controls are added
only alongside their concrete package handlers.

`AuditTrail` returns only typed operation/job/actor identifiers, kind, outcome and
time to current KG owners. Its opaque paging cursors bind the actor, org/KG,
policy revision and page size. Audit output omits command filters, material text,
provider payloads, tokens, endpoints and connection configuration. Existing policy
audit and original-storage quota ledgers remain authoritative for their domains.

The foundation gate passed 17 real-store checks in 32.26 seconds and 183
unit/protocol checks in 4.27 seconds. Ruff and strict mypy passed. These are tiny
synthetic laptop fixtures using the four capped Docker databases.

Retained package objects now use the separate `retained-backups` prefix. Typed
page/object references bind each object to its organization, KG, operation and
attempt, and preserve original-file ownership, length and SHA-256 exactly.
Reads verify the full checksum before yielding bytes. Immutable replay verifies
existing bytes rather than overwriting them; spooling and multipart chunks keep
memory bounded. Two package-contract checks passed in 0.04 seconds and a tiny real
S3 integrity/replay/corruption check passed in 3.28 seconds. The broader
unit/protocol suite passed 185 checks in 4.04 seconds. These were the retained
object foundation checks; complete package verification is recorded below.

Live capture now pins the accepted manifest/generation/visibility boundary against
source cleanup. The pin is scoped to one real backup job attempt and expires with
its lease; heartbeats renew live pins and cannot revive expired ones. Cleanup also
rechecks pins inside the detach transaction. A separate internal backup permit
can read only an accepted source bound to that pinned manifest under an explicit
current owner backup context. Ordinary reads of excluded sources remain denied.
Visibility changes, cancellation, policy changes or a missing pin stop capture.
The scope/heartbeat/purge/owner-job regression passed ten real-store checks in
66.85 seconds; 185 unit/protocol checks passed in 3.42 seconds.

The backup worker now copies originals, sealed preparation/projection artifacts,
the effective KG schema, canonical evidence and accepted human/import history
into bounded retained pages. Before a Mongo seal it reads the entire package back,
checks hashes, owners and root counts, and compares metadata, configuration,
visibility, publication and policy fences. A changed boundary leaves no usable
result. Historical jobs contain no reusable worker/publication leases; no private
provider connections, membership collections or credentials are included.

The focused backup/capture/owner-job regression passed 14 checks in 213.73 seconds,
including source withdrawal, owner revocation, damaged retained objects, missing
originals, mismatched accepted attempts and lost-response replay. Human history
and a tombstoned source were preserved without model calls. Four additional
metadata mismatch tests passed after reproducing the failure. The unit/protocol
suite passed 202 checks in 4.04 seconds; Ruff and strict mypy passed.
All are tiny synthetic laptop fixtures; no full restore is implied.

Current owners can queue a backup from the console's operations page or
`POST /api/v1/kgs/{kg_id}/backups`. Run a native
`uv run python -m domain_kg.worker --backup-only` worker; it needs no model/provider
configuration and leaves ingestion jobs queued. Normal ingestion workers do not
claim backups. Completed job pages link to the backup manifest and bounded part
pages. HTTP exposes `/backups/jobs/{job_id}`, `/backups/{backup_id}/pages/{index}`
and that page's `/objects/{object_id}` under the same KG prefix. Downloads verify
the selected object before sending success headers, recheck current owner
authority during streaming, use attachment headers and disable response caching.
They never accept an object-store key or server file path.

The owner/reader console, download, existing operations/jobs and worker regression
passed four checks in 55.68 seconds. It covered checksum equality, corrupt bytes
rejected before HTTP success, CSRF denial, reader denial and backup-only claiming.
An API schema regression from eager service creation was reproduced and corrected
with request-time construction; all 202 unit/protocol checks then passed in
4.16 seconds. Ruff and strict mypy passed 167 source files. These controls do not
establish restore acceptance or physically remove retained backup objects at
expiry; reads already reject expired manifests.

Scoped exports now have their own typed operation, immutable `retained-exports`
file and atomic ready/audit seal. They use the same current `ReadScope`, graph
views, filters, identity mapping and suppression as inspection. Default exports
exclude imported-unverified content; explicit opt-in preserves that status.
Formats are JSONL, CSV and GraphML. CSV formula cells are escaped; XML labels are
escaped and unrepresentable control characters are rejected. Export metadata
allowlists only org/KG, even if the caller passes a richer context model.

Run `uv run python -m domain_kg.worker --export-only`; this worker reads existing
evidence without extraction, embedding or query-model calls. Owners use the
operations form or `POST /api/v1/kgs/{kg_id}/exports`, then inspect
`/exports/jobs/{job_id}` and download its `/file` beneath the same KG prefix.
Files expire logically after 24 hours. A policy, visibility, metadata,
configuration or publication change invalidates the file before subsequent
downloads. Attachment downloads verify checksums before HTTP success. Physical
expiry cleanup remains part of the unfinished maintenance work.

Exports fail explicitly beyond 1,000 nodes, 5,000 supports, 50 MiB or a truncated
node view; they never silently omit eligible evidence. Narrow the source filters
for a larger graph. These are bounded laptop/service limits, not a large-corpus
benchmark. Three real-store cases verified filtering/withdrawal, owner revocation
and explicit import-trust selection. Four focused export/backup/owner transport
checks then passed in 42.33 seconds. All 210 unit/protocol checks passed in 4.30
seconds, with Ruff and strict mypy (172 sources). Two further backup expiry and
concurrent-publication cases passed in 44.08 seconds. No live model was called.

For a laptop serving both controls, run
`uv run python -m domain_kg.worker --operations-only`. It claims backup and export
jobs in one serial loop, without loading provider configuration. Ingestion jobs
remain queued for their own worker. The combined mode passed both real-store
public flows in 32.29 seconds, plus 211 unit/protocol checks in 4.23 seconds;
Ruff and strict mypy (173 source files) passed.

Restore implementation now has a pure, bounded typed identity remapper. It
recomputes identifier-based native entities and semantic assertions for a fresh
KG, preserves source-local version/observation IDs and evidence locations, and
maps legacy identities to the same IDs a fresh parse would produce. Human
node/relation edits and merge/split history retain their independent provenance.
Unknown references and changed observations are rejected. This is a foundation,
not an available restore endpoint or a completed restore drill. Projection bodies
are rebuilt without copying vectors: changed text must obtain a valid embedding
through a currently approved target profile.

Internal restore admission now reserves a server-generated inactive target under
a real source-KG owner job. A completed, unexpired, same-KG backup and an approved
placement are required. The reservation creates no catalog entry, grant or
provider selection; even the source owner cannot read the target. Cancellation
abandons the reservation and retry retains its target identity. Current owner,
lease, source boundary and placement approval are rechecked before work. Profile
detach serializes with allocation and refuses a referenced profile. Ordinary
workers leave restore jobs queued, and an unverified restore cannot finish.
The target assembly/activation handler and public restore controls remain pending.

Private target provisioning now uses the same bounded backend pools as catalog
provisioning. Only a live restore lease can select its recorded target and
placement. The target's provider profile requires a separate namespace approval
and permission for restricted content; restoring data starts owner-only. The
selected embedding dimensions determine the new collection. Approval and job
authority are checked again after provisioning. No target grant or fabricated
access context is used, and no embedding request is needed to create the empty
stores. The real target/provider test passed in 4.53 seconds; 12 restore/catalog
regressions passed in 13.91 seconds, seven namespace/storage checks in 1.51 seconds,
and all 220 unit/protocol checks in 4.23 seconds. Ruff and strict mypy passed.

Typed source transforms now preserve raw blob hashes, physical locations and
historical extraction profile metadata while rebinding parsed/extracted/resolved
documents. A selected preparation chain can be resealed with new dependency
hashes and explicit original/restored artifact provenance; active leases are not
part of that representation. Native source and human projection documents reuse
vectors only when the embedding space, dimensions, model and text agree. Changed
text or spaces require fresh validated vectors. These are pure assembly helpers;
alternative history chains and full activation
remain unfinished. Their focused tests and the full 229 unit/protocol checks
passed in 4.19 seconds, with Ruff and strict mypy (178 sources). No model ran.

Legacy import preparation now has a separate typed path. It checks original
bytes against the old sealed graph, parses those same bytes for the target
namespace, verifies canonical identity mappings, and reseals plan/graph/origin
hashes. The namespace-dependent normalized hash and record ordering are rebuilt;
raw content hashes and origin text stay exact. Prepared and embedded rows keep
`imported_unverified` status without fabricated extraction provenance or spans.
Four focused cases passed, and all 233 unit/protocol checks passed in 4.27 seconds;
Ruff and strict mypy (179 sources) passed. This still does not activate a KG.

The first real sealed-package preparation flow now copies exact original bytes
into an inactive target and writes the five resealed preparation stages. Complete
package verification precedes target provisioning. Metadata, parts, rows and
staged document bytes have explicit bounds; originals stream through the existing
one-writer spool. Replays reuse only fully checksum-verified immutable objects,
including after a lost write response. Corrupt retained originals fail before
target copying/provisioning and leave live originals intact. The initial real
flow passed in 25.00 seconds; replay and corruption cases passed in 48.18 seconds.
All 233 unit/protocol checks passed in 4.37 seconds, with Ruff and strict mypy
(181 sources). No model was called. Target authority, projection publication and
activation remain pending. The Docker configured/running hard limits were also
read back at 1.476 GB combined with swap disabled.

Canonical target materials, versions, entities, curations, assertions and supports
can now be staged through scoped repositories in dependency order. Batches fence
the current owner/lease, source snapshot, retained header and approved placement;
replays compare complete records rather than overwrite them. Reconciliation of
the real prepared target passed, including a repeated staging call, in 30.10
seconds. The target remained absent from the catalog and unreadable. All 235
unit/protocol checks passed in 4.42 seconds; Ruff and strict mypy (183 sources)
passed. Control metadata, source ledgers and final projection activation are next.

Native source control staging now restores private material access and metadata,
version snapshots without upload authority, effective schemas, sanitized ready
historical jobs, resealed artifact/checkpoint rows, observations and source
blocks/chunks. Rebuilt block/chunk records must match the captured ledger.
Technical engine names are regenerated for the target namespace. Immutable
metadata replays compare normalized BSON dates and complete values. The expanded
real flow passed in 63.04 seconds, including repeated control staging; all 237
unit/protocol checks passed in 4.29 seconds, with Ruff and strict mypy (185 sources).
Import admission and curation history still require dedicated control transforms;
the current stage rejects unsupported import history explicitly. No target
publication, new grant or full restore acceptance is implied.

Native restored source projections now use new attempt/manifest keys in the target
generation. Rewritten embedding artifacts are stored and hash-read back; Neo4j,
Qdrant and lexical read-back checks produce exact receipts. The real flow passed
in 71.81 seconds. Vector failure after graph writes and owner revocation after
graph writes both left the target inactive and source publication intact (two
checks in 56.48 seconds). A corrupted reservation pointing at an existing KG was
reproduced and rejected before binding and inside staging transactions; three
target/store regressions passed in 11.65 seconds. All 237 unit/protocol checks
passed in 4.30 seconds, with Ruff and strict mypy (186 sources). Atomic activation
is still pending; projection receipts alone establish no application access.

The private native activation path now commits the new ready catalog entry,
approved provider selection, accepted manifests/bindings, a sole current human
owner grant, restore result, audit and parent-job completion in one Mongo
transaction. It rechecks files/projections and current source/lease/placement
before committing. Current and captured material withdrawal flags combine
monotonically. A native end-to-end graph read, old reader/editor denials and
lost-response replay passed in 30.15 seconds. Source changes, vector loss and an
interrupted activation transaction all left no target grant/catalog/accepted
publication (three checks in 70.59 seconds). After the fence fix, five activation/
target/owner regressions passed in 33.87 seconds; 239 unit/protocol checks passed
in 4.32 seconds, with Ruff and strict mypy (189 sources). This prototype still
rejects human/identity/visibility/import history pending its dedicated transforms;
no public restore control or full Task 18 acceptance is claimed.

Accepted node/relation history now has typed target, scope and source-version
mapping, preserving actor/reason and raw field text. Source-scoped human renames
restore with original observations; a subsequent target edit passed after fixing
the target provider selection to include model and dimensions. Restored accepted
attempts use a typed durable restore origin and accepted root proof rather than
copied worker leases; a later backup verifies them. Suppression history, receipts
and current masks are mapped together, and changed live masks reject activation.
A restored hidden node stayed hidden until a new owner-authorized reversal,
continuing its revision counter. Suppression/re-backup checks passed in 75.53
seconds; the rename/follow-up edit passed in its preceding focused run. These
remain internal restore helpers; identity/import/full restore and public controls
are unfinished. No real provider was called.

Identity history now remaps merge/split partitions, every shared node counter and
explicit retraction reference, while preserving its sequence and provenance.
Backup metadata captures the identity counter, and activation preserves it with
structural curation ownership. A real restored merge retained both original
observations and alias resolution; a new owner could append a retraction and
recover the separate original identities. The focused check passed in 72.79
seconds with synthetic providers. Four pure mapping/corruption cases passed in
0.24 seconds; the full 250 unit/protocol checks passed in 5.13 seconds, and Ruff
and strict mypy (195 sources) passed. The full import/physical-placement drill
and public restore path remain pending.

Private restore preparation now requests fresh embeddings when mapped text or
embedding compatibility changes. Its client checks the actual restore lease,
current source owner and explicit restricted target approval before and after each
batch; usage belongs to the target namespace. It never invents target grants or
reruns extraction. A changed manual relation used exactly one mock embedding
request and restored its graph semantics/provenance; revoking approval during the
request left no active target. Both cases passed in 73.02 seconds. The unchanged
native preparation/replay regression also passed with provider HTTP forbidden.
Pre-call durable provider budgets and target storage quota admission still need
completion before this helper is exposed as a public restore handler.

The mixed native/legacy restore now stages resealed admitted import plans,
original blobs/schema bindings, historical import-job seals, origins and typed
source-qualified ID mappings. It validates mapping completeness and keeps the
native and legacy preparation formats distinct. Imported observation/support IDs
now match a fresh target reprocessing result, preventing duplicate identities.
The real mixed-source flow passed in 55.01 seconds: native retrieval worked,
legacy results remained excluded by default, explicit unverified retrieval and
citation retained target-scoped provenance without invented spans. Mapping/seal/
owner regressions and all 256 unit/protocol checks passed. A separate regression
found and fixed inherited whitespace stripping in backup authority envelopes;
ledger text, nested quotes and reasons now survive byte-faithful round trips.
Previously inconsistent packages continue to fail integrity checks. Full retention,
second physical placement, repair/generation work and public restore acceptance
remain open.

The integrated native restore handler now runs preparation, canonical/control/
human history staging, immutable projections and verified activation through one
serial `JobRunner`. `python -m domain_kg.worker --kg-restore-only --once` claims
only KG backup restore jobs and requires configured approved target providers.
The existing `--restore-only` still handles material restoration; `--operations-only`
continues to handle only model-free backups/exports. The actual KG worker restored
an accessible target, finished its source job, and replayed a lost response in
33.91 seconds with all provider HTTP forbidden. All 257 unit/protocol checks
passed in 4.58 seconds; Ruff and strict mypy (198 sources) passed. Public restore
admission/console controls still await storage/provider budgets and the remaining
full restore gates.

Restore preparation now reserves exact original-byte storage against both the
organization and fresh target KG before provisioning/copying. Reservations bind
the real current restore job, target, sealed package and source boundary. Two
concurrent restores cannot claim the same remaining organization allowance;
replays reserve once. Activation converts reserved bytes to committed bytes in
its catalog/grant/publication transaction. Interrupted activation rolls back that
conversion; failed inactive copies keep their reservation until actual cleanup.
Four serial quota/console checks passed in 82.42 seconds, including rejection
before provisioning, the real quota race, successful activation, owner-only
restored-KG links and existing job controls. The interrupted-transaction check
passed in 30.37 seconds. All 259 unit/protocol checks passed in 4.17 seconds,
with Ruff and strict mypy (199 sources). No real provider was called.

A completed restore job now links to its verified target search page. The owner
link requires the sealed restore result; unfinished targets have no link. Ready
backup/export/restore links are inside the status fragment so status refreshes
show them. Reader access to the owner restore job and its target ID is denied.
The public restore submission form remains pending provider pre-call budgets and
the remaining retention/physical-placement acceptance checks.

Provider HTTP now has durable daily organization and KG reservations before any
embedding or extraction request, including private restore reembedding. Current
source/target authority is checked after reservation and before sending data.
Validated usage settles once and returns proven unused capacity; an unattempted
denial refunds its hold, while uncertain attempts charge the configured bound.
Crashed pending holds remain conservative. The counters and request records store
scoped identifiers/counts only. Limits are typed root `budgets` fields in the
approved provider file; examples document the laptop and webserver policies.
The five real budget/permission/race checks passed in 10.96 seconds. Normal source
extraction, restored manual vectors, activation and console permissions passed in
two focused regressions (85.36 seconds). All 267 unit/protocol checks passed in
4.54 seconds; Ruff and strict mypy (202 sources) passed. Both example configs
validate with corpus approval disabled. No real model ran. Budget-history cleanup,
inactive-target/retained-package cleanup and the remaining full restore/operations
gates remain unfinished.

Restored node/relation bindings now use the same target-and-scope owner key as
ordinary curation publication. A real follow-up edit previously left two active
bindings; the regression reproduced that failure and now verifies one current
binding. A subsequent owner rollback of the restored rename returns the original
observed name without resurrecting the superseded override. The complete focused
flow passed in 74.85 seconds. Identity bindings retain their established identity
owner key. Curation repair/generation work is detailed in the maintenance subplan.

Owner curation repair now has a typed maintenance admission record. Only the
current owner can submit it, against the exact accepted publication, curation
and stable owner key. Admission verifies the parent accepted attempt seal;
ordinary source workers skip it, and unfinished maintenance cannot be reported
as ready. Seven real-store admission, owner-control and audit regressions passed
in 27.09 seconds; two command checks passed earlier. This is admission only:
projection reconstruction, publication and public controls remain unfinished.

Curation repair now reads the authenticated sealed document without requiring
healthy derived projections, reuses its vectors, writes isolated graph/vector/
lexical records and verifies read-back before a fenced Mongo publication. The
real current owner job is distinct from the original curator. A bounded typed
origin points to the original artifact even after repeat repairs. Curation history,
actors, reasons, target counters and scope keys remain unchanged. Two sequential
repairs and lost-response replay passed, along with vector failure, snapshot
change, interrupted commit and current-owner revocation: five real-store checks
passed in 159.91 seconds. Preparation passed separately in 21.31 seconds; all
269 existing unit/protocol checks passed in 8.64 seconds. Ruff and strict mypy
(207 sources) passed. Backup/restore compatibility and public controls are the
next verification gate, so the complete maintenance group remains open.

The owner Operations screen and `POST /kgs/{kg_id}/repairs/curations` now share
typed curation-repair admission. CSRF, idempotent API/console replay, cancellation,
retry, actual serial worker completion and reader denial passed. The dedicated
`--maintenance-only` mode skips private provider configuration entirely. Backup
authenticates repair provenance, captures the original artifact job as historical
data and retains the repair origin without any active lease. A real repaired-KG
backup/restore and new-owner rollback passed. The two focused end-to-end cases
passed in 422.90 seconds under the existing Docker limits; three worker checks
passed in 1.65 seconds. These are synthetic model-free fixtures, not an external
provider quality or full release acceptance result. No model ran.

Owner daily usage is available at `GET /kgs/{kg_id}/usage` and on the Operations
page. It reports only that KG's UTC-day settled and held call/token counters,
effective KG limits, remaining KG allowance and next UTC reset. Shared organization
limits still apply; organization aggregate usage and other KG counts are omitted.
Current owner policy is checked before and after the read; revoked actors,
editors/readers and foreign KG references fail. Pending reservations, settlement,
foreign org/KG isolation and console/API permissions passed in two focused
checks (74.45 seconds). Ruff and strict mypy (208 sources) passed. These counters
are conservative consumption bounds, not monetary billing.

Relation repair and zero-vector identity repair now have dedicated real-store
coverage. Both preserve accepted manual semantics, canonical ownership and target
counters; a current owner can append a compensating rollback afterward. Both
checks passed in 294.57 seconds, with embeddings forbidden after the initial
synthetic curation. These extend node repair coverage without starting a model.

The last focused regression combined 270 unit/protocol checks, a partial vector
write failure followed by retry under a new immutable attempt, and two scoped
usage checks including owner withdrawal during the database read: 273 checks
passed in 68.31 seconds. The abandoned partial attempt remained unaccepted.
Ruff and strict mypy (208 sources) passed before the storage checks.

The refreshed synthetic preview exercised the actual owner curation-repair worker
and retained the reviewed node; owner Operations/usage returned 200 and reader
Operations returned 404. Its complete timed fixture and cleanup passed (822.06
seconds including the configured 600-second viewing window). The preview is now
closed; it holds the exclusive smoke-store lock while running.

Private retained-object erasure now has a bounded, exact-key storage primitive
and a typed physical-grace policy (`config/retention.example.json`). It inventories
all versions/delete markers and unfinished uploads before deleting, checks current
authority before each I/O, waits for in-flight calls even under repeated
cancellation, and verifies absence. Prefix siblings and live originals remain
untouched. The pinned SeaweedFS 4.48 endpoint supplies an upload-ID-only cursor
and treats KeyMarker as equality; the implementation follows that cursor without
inventing a key marker. Missing/nonadvancing cursors fail before mutation.
The [pinned upstream implementation](https://github.com/seaweedfs/seaweedfs/blob/4.48/weed/s3api/filer_multipart.go#L1288)
was checked after the real regression exposed skipped uploads. Twelve pure/real-S3
checks passed in 24.49 seconds. The time policy defaults to 24-hour physical grace,
10 targets, 100 records/page, 20 pages and 1,000 inventory records. They now back an explicit current-owner retention job, with a frozen cutoff,
scoped target claims, durable verified object inventory and lease/permission
checks around destructive I/O. Ordinary workers skip it; the separate
`--retention-only` worker makes no provider calls. The HTTP/console controls
require an explicit resource selection and CSRF where applicable. No automatic
sweep is enabled; original-storage quota refunds and inactive restore-target
cleanup remain unfinished.

Deployment preparation now includes a digest-pinned Python runtime Dockerfile,
hashed runtime dependency export, non-root UID/GID 10001 and a read-only CI
workflow with pinned official action commits. No image was built or run and no
remote workflow/deployment was triggered. Offline wheel/source builds succeeded;
a new distribution checker prevents developer/private paths from entering the
source archive while requiring application and console assets in the wheel.
The original broad source archive reproduced a developer-log inclusion failure;
the allowlist fix and five artifact-boundary checks pass. At that packaging checkpoint the complete
unit/protocol suite had 286 passing checks (14.83 seconds), Ruff and strict mypy
cover 210 sources. The packaging-checkpoint wheel has 242 files (SHA-256
`b98633876e43df759da70f3ab6efb7df3e8516a27d8303c0898d1eb92c1c3a27`);
its source archive has 245 files. Deployment accounts, Linux image/parser startup
and external CI still need their real acceptance checks. See `docs/deployment.md`.

Expired-package cleanup is now locally verified. Seven focused intent/execution/
backup/console checks passed in 135.71 seconds. A further 293-check light/real-store
run passed in 88.58 seconds, including configuration-change inspection and corrupt
backup boundaries. The final 294-check regression passed in 49.56 seconds and
covers actual cancellation followed by a new lease/attempt, lost-response replay,
preflight byte rejection before content I/O, immutable plan scope and live-byte
preservation. Ruff and strict mypy pass for 214 source modules. The Docker budget
was rechecked at 1.476 GB configured/running hard limits, swap disabled. No native
or remote model request was made. Task 18 remains open for the separate recovery,
migration, retention and deployment gates.

Public fresh-KG restore controls now use the same owner admission service as
native execution. The backup page and typed HTTP POST accept approved profile IDs;
the job page and safe progress endpoint expose the allocated namespace, stage and
independent target provider approval. No connection, allowed-organization list or
old grant is returned. The UI never approves a provider scope. A tiny native
backup/restore fixture verified absent target catalog visibility before approval,
explicit synthetic target approval, activation/result navigation and reader/editor
denial: two checks passed in 74.85 seconds. This is local fixture verification,
not an external provider approval or the full multi-profile recovery drill.
