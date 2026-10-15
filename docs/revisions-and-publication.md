# Domain KG revision and publication protocol

## Authority and identifiers

MongoDB is authoritative for accepted materials, source supports, curation, permissions, and published views. Neo4j and Qdrant serve projections; S3 preserves raw bytes and parsed artifacts. Every reference includes organization and KG plus an immutable source or curation revision.

A material has a stable `material_id` and immutable `version_id` records. Nodes have stable `entity_id` values independent of display names. Assertions retain semantic identities and independent support IDs. Human changes create `curation_revision_id` records. Publication advances a monotonic `published_revision` and immutable manifest reference.

Projection keys include source/curation revision, index generation, and attempt ID. MongoDB maps accepted logical objects to those immutable keys. Retry writes are idempotent within an attempt; a recovered worker uses a new isolated attempt namespace. Presence in Neo4j, Qdrant, or a LightRAG cache never establishes acceptance.

## Published views and read scopes

`PublishedView` identifies the KG revision, index generation, accepted material-version map, accepted curation map, and required projection receipts. Store large maps in indexed MongoDB records referenced by the manifest, avoiding unbounded BSON documents. New version-specific support records can reference unchanged content-addressed parsed or embedding artifacts.

`ReadScope` combines this view with current access, material metadata, visibility revision, support status, and requested filters. Resolve accepted visible source versions and applicable curations, then compile backend predicates. Every graph step, vector candidate, description, name, and count consumes this scope.

Neo4j stores source-bound observations and support edges. Qdrant stores chunks, observations, and curation vectors with indexed namespace/version/generation fields. Shared assertion identities are permitted, but traversal/display requires an eligible support. Apply eligibility before branch top-k and recheck acceptance and visibility before response publication.

For the initial workload, resolve authorized material-version sets per query. Benchmark set size, selective-filter recall, and query cost. Large sets require an equivalent indexed partition or bounded query plan; never drop a restriction or truncate eligible IDs silently. Cursors pin a retained view and expire on policy or visibility changes.

## Publication state machine

The command lifecycle is `accepted -> preparing -> projecting -> verifying -> published`. Work may instead become `failed`, `cancelled`, or `repair_required`. API job stages give detailed parser and extractor progress.

1. Validate the actor, idempotency key, expected object revision, and expected KG revision. In a MongoDB transaction, write the command, staged canonical records, and durable job/outbox event.
2. Acquire a fenced per-KG publication lease. Preparation can run concurrently; one publisher selects the next view. Recheck actor access, tombstones, KG state, and expected revisions.
3. Preserve original/parsed artifacts and source/curation records. Reuse authorized content-addressed artifacts with separate source and deletion ownership.
4. Project changed observations, assertion supports, lexical documents, mappings, and vectors under attempt-specific IDs. Do not overwrite a record referenced by an accepted manifest.
5. Await backend completion and explicitly flush deferred LightRAG writes. Verify affected IDs, counts, generation, and checksums with read-back checks. Store graph and vector receipts tied to the exact attempt and content manifest.
6. In MongoDB, atomically compare the lease token and expected revisions, accept source/curation mappings, and advance the published manifest. Losing or stale attempts receive `VERSION_CONFLICT` and cannot advance the marker.
7. Emit audit/outbox events and return the accepted receipt. Collect unreferenced attempts and superseded records only after retention and pinned-read checks.

The MongoDB commit is the acceptance point. No transaction spans all stores. Receipts establish readiness at acceptance, not permanent backend availability; later projection loss causes a consistency error and repair. A request cannot serve an unverified mixture of revisions.

## Material changes and source ownership

Content replacement diffs parsed blocks, reuses unchanged extraction/embedding artifacts, and creates version-specific source/support references. Recompute the affected shared observations, facts, and vectors. The old version remains accepted until the new projections pass verification; retained accepted records support already pinned reads.

Metadata that does not affect embedded content updates authoritative filter metadata and increments the visibility/filter revision without re-embedding. Resolve eligibility from MongoDB so stale payload tags cannot choose the old filter value. Fields contributing to embedded text require projection-bearing revisions.

Exclude, delete, and suppress commit an immediate MongoDB visibility fence. New queries cannot select the source/node while cleanup runs, and in-flight responses recheck the fence. Removing A withdraws only A's support; a shared fact remains available through B. Descriptions and observation vectors follow the same source ownership.

An old-version argument cannot bypass a tombstone. Restore is a new authorized revision; prepare projections before clearing the fence. Purge obeys live-store retention and shared-blob checks. Backup expiration is managed separately.

## Node edits and rollback

Curation records actor, reason, scope, expected revision, before/after values, source targets, and redirects. Apply only accepted curations visible to the caller. Keep extraction observations independent from human corrections.

Rename/type/description edits refresh affected entity/relation vectors when their embedded content changes. Merge/split publishes new mappings and retains prior mappings for reversal. Suppression fences immediately and remains effective across re-ingestion and rebuilds.

Rollback appends a compensating revision against current state. It does not rewrite history or restore old grants. Source-scoped edits remain scoped; KG-wide public overrides require owner authority. Concurrent contradictory edits return `VERSION_CONFLICT`.

## Failures and repair

| Failure | Required behavior |
| --- | --- |
| Before projection writes | Keep the previous view; retry or cancel staged work |
| Graph succeeds and vector fails, or the reverse | Do not publish; retain the accepted view and retry missing work |
| LightRAG writes remain buffered | Do not count the call as a readiness receipt |
| Worker dies after both writes but before publication | Revalidate receipts and authority before resuming or abandoning |
| Lease expires while an old worker continues | Reject its publication; isolated attempt IDs cannot overwrite accepted records |
| Publication commits but response is lost | Idempotent replay returns the accepted receipt |
| Deletion, suppression, or ACL change races with a read | Re-resolve visibility or return `POLICY_CHANGED`; return no fenced evidence |
| Accepted projection is unavailable or inconsistent | Return an explicit backend/consistency error and schedule repair |
| Authority is unavailable | Deny policy/publication resolution; projections do not become authority |

Repair reconciles accepted manifests and source ownership against projection IDs, regenerates missing records, and verifies them before re-enabling the view. Fast mode requires vector and lexical branches unless the caller explicitly permits partial branch coverage. Authority or publication-integrity failures always fail the affected KG.

Backup consistency uses the accepted manifest. Independent database dumps are insufficient without validating that boundary. Restore into an inactive namespace, verify or rebuild projections, then assign explicit current grants.
