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
