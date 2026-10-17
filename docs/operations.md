# Operations implementation status

The owner-operation foundation is implemented. Complete backup packages, restore,
export downloads, generation migration and deployment acceptance are still being
implemented under [the operations plan](superpowers/plans/2026-10-17-operations.md).
No backup/restore success or recovery readiness is claimed at this checkpoint.

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
unit/protocol suite passed 185 checks in 4.04 seconds. Full capture/sealing and
restore handlers remain in progress.
