# Durable jobs and recovery

Material acceptance commits its source version, job and outbox in the same
MongoDB transaction. Job IDs are stable on replay. The worker polls this durable
authority queue; an interrupted outbox acknowledgement only duplicates a wake-up.

One atomic claim assigns a lease token, monotonic fence and fresh attempt UUID.
Expired work can be reclaimed up to three attempts, with bounded retry backoff.
Updates/checkpoints require the exact live token, fence, attempt and namespace.
Preparation checkpoints reference immutable artifacts and configuration/input
hashes. Projection, verification and publication checkpoints additionally bind
the attempt and cannot be reused by a recovered worker. Exceptions retain safe
codes, never raw provider messages or source bodies.

Checkpoint artifacts have a Mongo ownership ledger and immutable S3 objects
under the source's organization/KG/material/version. Missing/foreign references
are rejected. Recovery reads back and checks the complete artifact hash before
skipping a completed step, so corrupted bytes cannot silently become a resumed
pipeline input.

Current actor membership, KG state, source ownership/ACL, tombstones, and expected
active version are checked before pipeline steps and publication activation.
Preparation can run concurrently. A separate per-KG publication lease serializes
publishers, and validates the current job lease too. A stale attempt cannot keep
the publication slot after its job is reclaimed. The later publication service
must compare both leases and commit the job outcome with the accepted manifest.

Explicit authorized cancellation immediately fences the job. Cancelling a client
request or terminating a worker leaves the accepted command recoverable after its
lease expires. Failed/cancelled jobs support idempotent authorized retry. Job
progress is visible only within current material access.

The native worker processes one job at a time and adds no container. Until parsing,
extraction and publication factories are configured, `uv run python -m
domain_kg.worker --once` refuses before claiming any work. A partial pipeline can
record preparation checkpoints but cannot report a ready graph: readiness
requires an authoritative accepted publication manifest. These factories are
completed by the subsequent ingestion/publication tasks.

Run `python3 scripts/smoke.py --task 7 --keep-services`. Tiny real-store fixtures
verify concurrent claimants, worker death/reclaim, stale tokens, checkpoint
resume, competing publishers, source and actor revocation, explicit cancellation,
retry exhaustion, request cancellation, and fail-closed incomplete pipelines.
