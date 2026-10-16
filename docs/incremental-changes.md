# Material changes and bounded reuse

Metadata edits update the MongoDB material metadata, record an immutable before/after
command, and advance the visibility revision. They call no model or derived store.
A separate metadata epoch prevents an older queued replacement from overwriting a
later edit; material grants retain their independent policy checks.

Content replacement verifies and parses the new original, then compares source
windows against the same material's currently accepted version. Reuse requires
identical text, parser/chunker versions, span offsets and contextual headings, plus
matching extraction settings, schema and provider selection. Physical source
positions may move. Ambiguous repeated windows are processed again instead of
merging mentions by name.

Reused extraction passes the canonical source validator again after every entity,
relationship and qualifier span is rebound to the new immutable source locations.
Unchanged unambiguous mentions retain entity IDs. Observations, supports, chunks,
artifacts and projection attempts receive new version-specific references. The old
accepted projection remains intact until the successor passes readiness and the
MongoDB acceptance transaction commits.

Vectors can be copied from that same accepted source when projection kind, complete
embedded text and embedding profile/space match. Only changed inputs reach the
embedding client. A changed extraction model can therefore require extraction while
unchanged embedded text still avoids embedding work. Provider configuration should
set `extraction_revision` to the pinned model weight/API revision and change it when
those weights change. Endpoint changes also invalidate extraction reuse; key rotation
does not. Embedding compatibility has its own endpoint/model/weight/window identity.

Current source permissions, removal fences, active version and provider clearance
are checked before and after reuse. Identical content in a different material does
not reuse another material's extraction or vectors. A missing or invalid accepted
artifact fails closed rather than becoming an unauthenticated cache hit.

Local verification uses small nonprivate fixtures and real MongoDB replica-set,
Neo4j, Qdrant and object-storage services. Provider responses in these lifecycle tests
are synthetic; they establish reuse, ownership and race behavior, not model quality.

Explicit retained-version searches select an actual accepted historical manifest.
They retain a separate current publication and visibility fence, use current grants
and mutable material metadata, and use the requested original's media type. The
response reports the current KG revision and the selected projection generation.
Active versions remain the default. Staged versions fail with `REVISION_NOT_READY`;
a version belonging to another material is inaccessible. A tombstone or a revoked
grant prevents retained evidence from being returned.

Retained reads currently require the selected generation to remain the current
routed generation. An older generation returns an explicit readiness error until
its routing/rebuild is available; the service does not search another collection
or embedding space as a substitute.

The material lifecycle domain service commits exclusion or deletion as an immediate
visibility fence. Removal previews count only the material's owned versions,
original bytes and dependent pending jobs after checking the complete source owner
chain. Exclusion is reversible indefinitely; deletion has a 30-day live retention
window. Restoration verifies the original checksum and accepted graph/vector
readiness before clearing that fence. Missing projections create a durable
provider-free restoration job that publishes a fresh immutable attempt. An original
lost after a preparation checkpoint still blocks final restoration.

An original removed before its first publication uses a separate full
`reprocess_material` job. The service authenticates the initial ingest receipt,
canonical version and retained original, then runs parsing, extraction, resolution
and embedding under current provider clearance. A server-only permit binds each
retained read to the pending restore intent, actor, material revision, live job
lease and current KG generation/visibility. It does not invent an accepted parent.
The material stays hidden until all projection receipts pass and Mongo accepts the
new publication. Original loss after staging, permission revocation, repeated
withdrawal or expired deletion retention must prevent final activation.

The native `--restore-only` worker remains limited to provider-free accepted-data
restoration. Full reprocessing runs in a normally configured worker and needs an
approved extraction/embedding profile. These lifecycle checks use synthetic provider
responses; they do not establish the small native chat model's extraction quality.
The combined local restoration and replacement regression passed 18 real-store
checks in 358.74 seconds, including current provider restrictions and activation
failures after original loss, grant revocation, deletion and retention expiry.

Owner-only retained physical cleanup now passes its bounded local checks, including
partial deletion recovery, active pins, conflicting references, independent source
corrections and public human claims. See [material purge](material-purge.md).
Complete transport exposure and merge/split are still in progress; Task 14's plan
gates have not been marked complete.
