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

The updated controls also pass all 293 unit/protocol checks (11.10 seconds),
Ruff and strict mypy across 215 source modules. A fresh offline distribution
build/check passed: 247 wheel files, SHA-256
`4d7e097c20e1b2fa73e62ff02b442490787c5736d52ff0919bb806bd30959c08`;
250 source-archive files. This refresh is a local artifact check; the separate
Linux image, remote CI and release acceptance remain open.

Restore-target cleanup prerequisite: target provisioning, every original/artifact
copy and graph/vector mutation now records a scoped pending write under the actual
restore lease before remote I/O. Issued I/O is shielded and drained even through
repeated cancellation; an error leaves an uncertain record without exception text.
Only acknowledged completion is settled. Target allocation records the tracking
protocol; old untracked targets are not silently approved. The owner job page/API
show scoped pending/uncertain counts. Four cancellation/record/full-restore checks
passed in 216.34 seconds; four tracking/status checks passed in 102.71 seconds.
Ruff and strict mypy passed. No real model was called. The previous timed preview
finished successfully in 722.17 seconds and cleaned up; it is currently closed.
Cleanup/refund itself remains a separate unfinished step.

Inactive restore cleanup is now locally verified. Owner admission freezes one
terminal restore's namespace, placement, settled object inventory and uncommitted
quota reservation. It permanently fences restoration retry. The model-free worker
checks current owner/lease around storage I/O, erases exact originals/versions/
unfinished uploads and scoped graph/vector rows without dropping shared stores,
then deletes target ledger rows and releases quota with completion/audit in one
Mongo transaction. Unknown target objects block refund even after partial erasure.
Repeated completion cannot refund twice. Old untracked or uncertain targets remain
blocked. Local lease/policy refusals settle write tracking; unacknowledged remote
errors remain uncertain.

The first focused run passed 11 checks in 171.37 seconds. The extended run proved
pending/uncertain admission rejection and rollback but exposed an incorrect test
requesting manual retry of an automatically requeued job (299 pass/one fail).
The corrected final run passed 300 checks in 115.19 seconds, including owner
console/API, CSRF, reader/editor denial, source preservation, transactional rollback
and automatic retry. Ruff and strict mypy pass across 220 source modules. All
providers were synthetic and Docker retained its 1.476 GB hard limits.

Owner operation failure/expiry audit now commits atomically with job state.
The actual attempt ID and finite safe error code are recorded once, anchored to
immutable admitted actor provenance. An already-revoked actor can have a failure
recorded without granting that actor permission to continue work. Exhausted expired
owner leases are swept in bounded batches of 100 with a distinct lease-expired
reason. Private error text, endpoints and credentials are omitted. API and console
audit reads require current owner access and retain scoped opaque cursors.
Five focused checks passed in 27.38 seconds. The final 305-check light/real-store
suite passed in 55.44 seconds; two ordinary source-job cancellation/recovery/
retry-exhaustion regressions passed in 31.82 seconds. Ruff/mypy pass for 221 source
modules. No model was called. This does not close the independent generation,
complete backup-authority and multi-profile release acceptance gates.


Model-free current-space generation core (2026-10-17): a genuine owner operation
pins bounded immutable inputs, current placement/schema/embedding settings and
material fences. It reuses sealed vectors one source at a time and stages fresh
attempt/generation records. Graph/vector/lexical read-back precedes one fenced
MongoDB root/configuration switch. Read lineage preserves the actual artifact
job; repeated rebuild and later curation repair do not impersonate an old actor.
Three focused unit/real-store checks passed in 127.95 seconds, including injected
vector failure, old-view availability, retry, two successive rebuilds, preserved
manual history and later repair. Ruff/mypy pass for 227 source modules. Docker
configured and active limits both measure 1.476 GB, swap disabled. Zero live model
calls. Purged bindings and retired curation dependencies are explicitly refused;
schema/model migration, UI/worker, hidden-source and backup drills remain open.


Generation controls checkpoint (2026-10-17): the owner operations page and typed
HTTP reindex command now share the same admission service. The dedicated
`--generation-only` worker uses an empty provider registry and handles one kind
serially. An empty-KG API/CSRF/cancel/retry/worker drill and five retained-source /
material-fence / configuration / revoked-owner / MongoDB-abort checks passed.
The light suite reported 297 passes and one failure in 405.12 seconds: the
separate new backup/restore case exposed missing rebuild artifact ancestry in
backup. That compatibility fix and its focused verification remain in progress;
this checkpoint does not claim a wholly green backup suite. Zero live models.
See [generation rebuild](generation-rebuild.md) for limits and commands.


Generation lineage checkpoint (2026-10-17): backup now authenticates rebuild
results and records the true source/curation artifact job separately from the
real generation job. The retained authority envelope keeps explicit generation
provenance without reusable old leases. A real native/manual backup → fresh
restore → rollback → restored-KG rebuild drill passed (164.73 seconds). A mixed
native/manual/imported-unverified rebuild passed (62.29 seconds), including
default exclusion and explicit inclusion of unverified evidence. An independent
twelve-substitution fixture exposed source/job namespace/output-owner metadata
checks; after correction, 292 unit/focused checks passed in 47.34 seconds. The
prior cohort's sole failure was that new integrity probe, now corrected; the
already-green restore/import drills were not repeated. Ruff/mypy pass for 227
source modules. Fixture teardown includes its own old/new generation collections
and inactive restore namespaces; production retained-generation collection is
still unfinished. No unrelated storage is erased.

Offline distributions were rebuilt and boundary-checked: wheel 259 entries,
SHA-256 `1604096fc405a1e12640642166900f414274e576f5e3c14d0ee6d196f5c7754d`;
source archive 262 entries, SHA-256
`9ff31431c509582190c36a0c938ef6eac467099f09544d07d3ce02a21a1ece59`.
No image build or live provider request was performed. This completes the
explicit `rebuild_current` subplan, not full Task 18 schema/model migration,
complete backup history, physical-profile recovery or Task 19 acceptance.


Live console checkpoint (2026-10-17): the opt-in synthetic loopback preview now
includes an explicitly cancelled, never-executed restore for safe cleanup review,
and a serial dispatcher for generation and restore-cleanup jobs. Real HTTP
owner forms were submitted: generation reached ready at source publication 3;
inactive-target cleanup reached ready; owner operations and audit returned 200;
reader operations was denied. HTTP verification does not print cookies or CSRF
tokens. The 600-second preview fixture is still running at this checkpoint, so
its final pytest/teardown result is not yet claimed. Opening the browser panel
was queued; the loopback links themselves were verified. Zero live model calls.


Owner usage-history checkpoint (2026-10-17): `GET /api/v1/kgs/{kg_id}/usage/history`
accepts `days=1..31` (default 7) and optional past UTC `end_date`. It reads only
the exact KG daily ledger and revalidates current owner access after the cursor
read. Responses contain recorded dates and conservative spent/reserved counters;
no historical limits, profile identifiers, endpoints, raw source/job payloads or
organization aggregates are inferred or exposed. Missing dates remain absent.
History reads neither call a provider nor settle/refund a reservation. Invalid
calendar/future windows and corrupt counters fail safely; backend errors are
redacted. The operations page shows the last seven UTC days. These are service
provider counts, separate from Codex conversation usage.

Model/DTO RED, calendar-underflow RED, service-missing/API-404 RED preceded the
implementation. A same-basename pytest collection issue was corrected by naming
the unit module `test_usage_history_models.py`. Final 299 checks passed in
22.76 seconds, including actual pending/unknown reservations, namespace/window
filtering, owner revocation after read, safe backend failure and API/console role
denials. Ruff/mypy pass for 228 source modules. No live model request. The previous
full console preview completed successfully in 696.41 seconds (600 seconds of
viewing), including its cleanup; its server is now closed. Task 18/19 remain open.


Light preview/configuration checkpoint (2026-10-18 local): the explicit boolean
`tests.preview_restore_demo` defaults to false. Normal page previews retain real
source/manual data and genuinely cancelled inactive targets while skipping the
complete restore setup; a restored-KG identifier is returned only in the explicit
full-demo mode after activation. Three configuration regressions were observed
red before implementation; all 16 resource-profile checks pass (1.10 seconds).
Configured and running Docker hard limits remain 1.476 GB with swap disabled.
The new lightweight live HTTP check verified the usage-history page/API, a real
generation job to ready, an inactive cleanup job to ready, owner audit 200 and
reader denial. The 600-second preview is currently live; its final timed-fixture
result is pending, separate from the completed prior 696.41-second preview.

Offline distributions were refreshed after usage history: wheel 260 entries,
SHA-256 `85dd6f87cb44ea86e89a798c31655ab0af68eba06228fc6728e634a4c62a7164`;
source archive 263 entries, SHA-256
`81363e65e299f9ad5cc3b1a5004aacdf85e9b64dbf82c2880f8311ccbeadd96d`.
Both passed the archive boundary checks. No Docker image or actual model call.


Preview/source-isolation checkpoint (2026-10-18 local): the lightweight preview
completed successfully in 647.47 seconds, including its 600-second viewing window
and fixture teardown. Its startup/teardown portion was about 47 seconds versus
about 96 seconds for the prior complete-restore preview. A new committed-source
launcher isolates application/templates from working edits, using a bounded
temporary Git archive, verified import roots and an owned child process group.
Four snapshot/link/budget/deadline checks pass. Initial end-to-end startup exposed
a real parser-child code-path mismatch: its sanitized environment lost the
snapshot path and fell back toward the editable installation. The parser now
sets `PYTHONPATH` to its own resolved code root; no sandbox/network permission was
expanded. A real sandboxed two-step marker/parse check proves code matching even
with an untrusted ambient path. The full 300-unit suite passed in 6.45 seconds;
seven focused checks, including native memory/runtime refusal without heavy
inputs and actual macOS network denial, passed in 6.16 seconds. Ruff/mypy pass for
228 source modules. The launcher is committed before re-running its frozen HEAD
so that this parser fix is included. End-to-end launcher startup remains pending
at this checkpoint. Zero model calls; Docker caps unchanged.


Restore split/follow-up checkpoint (2026-10-18 local): a single tiny real-store
drill backed up accepted merge→split history, restored it into a fresh KG through
the actual restore service, replayed completion without a second publication,
verified separate identities and source evidence, and denied the source reader
and editor access to the target. The current target owner then retracted the
imported split and published a new split, retaining the original editor's actor
and reasons alongside new owner provenance. The source KG remained separate and
unchanged. The case passed in 80.24 seconds; providers were forbidden after the
initial synthetic source preparation. No application correction was required.

The frozen preview also passed end to end and cleaned its owned temporary tree
and fixtures (385.59 seconds including viewing; explicitly finished to release
the serial store lock). The import-root guard and trusted native-parser path
worked from committed revision `00c6bd3`. Latest offline distributions retain
260/263 entries and pass the boundary checker: wheel SHA-256
`1936722edce613def7784059a481763dfab23de5f14045c46d0d308d3017fbc8`,
source SHA-256 `52d0c31669343ea228d7d275cb50ffe8ced7695351e27783189ec3c5a817d6f7`.
Second physical-profile recovery, complete purged/history capture, generation
retirement/model-schema migration and final evaluation remain open.


Serial physical-profile recovery checkpoint (2026-10-18 local): a real retained
backup with one native source and a human rename restored through the owner
worker onto different Neo4j/Qdrant volumes/ports and a separate authority database
on the same Mongo replica set. Original bytes, human provenance, lexical retrieval
and denial of old reader/editor grants were verified. The stopped primary profile
failed explicitly. Deleting one exact target vector made readiness fail; a real
source repair restored readiness without providers. A new target-owner metadata
edit then succeeded. Switching back recovered the primary data with its original
name and without target-only tags. Only the fresh target projections and the
drill-owned secondary authority database were cleaned; no volume was removed.

The explicit physical case passed in 119.53 seconds (115.08-second call). Before
switching, on secondary storage and after primary restoration, all four active
databases had combined hard limits of 1.476 GB and disabled swap. Both switches
used cached, pinned images with `--pull never --no-build`. The first attempt
exposed an overlong test database suffix, failed safely and restored primary;
shortening the fixture name corrected the harness. No application correction or
actual model call was needed. This is a sequential capped-laptop drill, not a
simultaneous multi-profile throughput benchmark. Required backup/quota/audit
regressions are running separately; Task 18/19 remain open.
