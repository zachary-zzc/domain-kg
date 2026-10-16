# Local publication and repair

MongoDB owns acceptance. An ingestion job first preserves and validates its source,
then prepares source-owned chunks, observations, assertion supports and embeddings.
Neo4j and Qdrant receive immutable IDs derived from organization, KG, attempt,
manifest, record kind and logical ID. No accepted record is overwritten.

Neo4j writes use transactions, create-only properties and fixed support/observation
edges to namespace-bound identity anchors. Anchors carry no derived display fields.
The full-text index updates at commit; readiness also probes each opaque indexed
key. Qdrant uses `insert_only` with `wait=True`. Verification reads back complete
payloads, support endpoints and normalized vectors, then checks the exact record
set, counts and checksums. A completed API call alone is insufficient.

One Mongo transaction checks current actor access, source ownership, source version,
schema/provider selection, generation, the expected published revision, worker
token and fenced KG publication lease. It accepts the view, updates active source
metadata, retains the previous original, records audit/outbox events, and marks the
job ready. This is the acceptance point; there is no transaction across stores.
Concurrent replacements have one winner. Idempotent acknowledgement replay returns
the original accepted receipt after current permission checks.

Manifests compose indexed source bindings, including unchanged accepted attempts.
Record references are stored individually; headers and idempotency records do not
duplicate unbounded maps. Read scopes bind each source to its exact accepted
generation, attempt, manifest, content hash and chunk artifact. A source-version
filter alone cannot establish eligibility. Abandoned repair attempts remain
ineligible even when they target the currently accepted source version.

Current permissions and source visibility remain authoritative. Replacement does
not change material grants; those use the separate owner policy service. Names,
descriptions and support endpoints remain source-owned. Missing or corrupted
accepted projections return an explicit consistency/backend error and record a
scoped repair request. A detector never invents a human identity for a repair job.

An authorized caller requests repair through `RepairService.request`. Repair copies
the retained, checksum-verified embedding artifact into a new job-owned artifact
and publishes a new attempt after full read-back verification. It makes no provider
call, preserves current metadata/grants and checks that its accepted parent is still
current. The original ingesting actor does not need continued membership; the repair
actor needs current access. Retention must preserve accepted artifact ownership
ledgers and referenced parsed/chunk artifacts.

The native worker supports:

```bash
uv run --locked python -m domain_kg.worker --once
uv run --locked python -m domain_kg.worker --repair-only --once
```

Normal ingestion requires approved private provider configuration before claiming
jobs. Repair-only execution can run without provider configuration and leaves
ingestion jobs queued. Native scanned PDFs preserve the original and report
`ready_with_warnings` / `OCR_REQUIRED`; this laptop profile does not run OCR models.

Run `python3 scripts/smoke.py --task 10 --keep-services` for tiny real-store failure,
race, repair and native LightRAG buffered-write checks. The aggregate Docker hard
RAM limit is 1.476 GB with swap disabled. Store suites take a process lock because
their S3 development authentication fixture and resource allowance are shared.
No throughput benchmark, large corpus, OCR model or paid provider is used.

The curation task adds the authorized mutation producer and accepted curation read
path to these projection primitives. Manual projection IDs and provenance are
separate from generated source spans; no source-less curation is served before
that path is implemented and verified.

API details were checked against the pinned installed clients and primary docs:
[OpenAI embeddings](https://developers.openai.com/api/reference/resources/embeddings/methods/create),
[Neo4j 5 full-text indexes](https://neo4j.com/docs/cypher-manual/5/indexes/search-indexes/full-text-indexes/),
and [Qdrant point insertion](https://qdrant.tech/documentation/manage-data/points/).
