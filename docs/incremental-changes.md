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
