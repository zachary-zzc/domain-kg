# Laptop resource limits

Task 11's first graph traversal check hit Neo4j's 512 MiB cgroup limit after a
long-lived JVM warmed up. The cap and all volumes were preserved. The smoke
profile now bounds direct buffers to 32 MiB and the JIT code cache to 48 MiB,
uses first-tier compilation on its single CPU, and keeps 128 cached queries per
database. Native-memory summaries were enabled for diagnosis. During the passing
five-case graph suite Neo4j used about 408–429 MiB; the final complete smoke is
recorded with the task. These settings trade compilation throughput for laptop
headroom. The [Neo4j query-cache settings](https://neo4j.com/docs/operations-manual/current/configuration/configuration-settings/#config_server.memory.query_cache.per_db_cache_num_entries)
and [Java VM options](https://docs.oracle.com/en/java/javase/21/docs/specs/man/java.html)
describe the controls. This is not a deployment sizing recommendation.

The smoke stack has a combined hard container RAM limit of 1,408 MiB (1.476 GB): MongoDB 512 MiB, Neo4j 512 MiB, Qdrant 192 MiB, and S3 192 MiB. Container swap is disabled. CPU limits total 2.5 cores. These limits apply to the services; they do not change the laptop's system RAM or Colima VM configuration.

Task 4's repeated disposable schema tests exposed MongoDB's inherited open-file ceiling: WiredTiger aborted with `Too many open files` during an index build. This was not an OOM event. The Mongo container now uses the [MongoDB-recommended open-file limit](https://www.mongodb.com/docs/v8.0/reference/ulimit/) of 64,000. Raising a descriptor ceiling does not reserve additional RAM; the then-current 448 MiB hard cap was unchanged. The existing volume recovered normally and the complete real-store suite passed after restart.

Smoke tests use tiny synthetic records and three-dimensional vectors. No local models, corpus indexing, load generation, or OCR model downloads run in these checks. Independent storage deployments are tested sequentially when simultaneous instances would exceed the RAM cap. Runtime secrets, private originals and generated indexes remain outside Git.

Task 6 includes one streamed 50 MiB original to verify the documented upload
boundary. A single S3 PUT exhausted the object store's 128 MiB cap. Originals
larger than 5 MiB now use sequential 5 MiB multipart transfers; the same boundary
passed with the unchanged cap. Client spooling uses at most 1 MiB before rolling
to temporary disk; downloads verify on temporary disk before yielding 64 KiB
chunks. This is a size-boundary check, not a throughput/load test.

Long sequences of disposable database/index tests reached MongoDB's former
448 MiB cap, including after a fresh restart. Task 6 redistributes the same total:
MongoDB now has 576 MiB and Neo4j 512 MiB, with a 128 MiB Java heap and 32 MiB page
cache. Mongo's WiredTiger cache remains 256 MiB; it is only one component of
[MongoDB memory usage](https://www.mongodb.com/docs/manual/core/wiredtiger/).
No aggregate RAM, swap, or CPU limit was raised. Test fixtures remove their own
randomly namespaced graph objects, per-KG vector collections, S3 bucket, and
Mongo database. Sustained resource behavior remains an operations/evaluation
gate; a smoke run does not establish production sizing.

Mongo's former health check launched `mongosh` (Node) every five seconds inside
that same RAM cgroup. The repeated auxiliary process left insufficient headroom
as the database warmed, even at 576 MiB. The container now uses a shell socket
probe; the existing host Python runtime separately initializes and verifies a
writable `rs0` primary with a two-connection client before smoke tests. Run
`uv run python scripts/init_replica_set.py` after a direct `docker compose up`.
Application connection strings still require the replica set and fail closed
while it is unavailable; a listening socket alone never authorizes a transaction.

The health change alone did not resolve sustained warm-service growth: Mongo
later reached its cap again after the passing Task 6 runs. Allocator diagnostics
showed about 41 MiB of unused heap immediately after recovery. Task 7 configures
`tcmallocReleaseRate=10485760` (10 MiB/s) to return unused allocator memory sooner,
using the [MongoDB 8.0 parameter](https://www.mongodb.com/docs/v8.0/reference/parameters/#mongodb-parameter-param.tcmallocReleaseRate).
This trades reuse/performance for headroom and changes no RAM cap or persistence
setting. Warm repeated correctness runs must still be checked; this is not a
production-sizing claim.

Correctness fixtures reuse one randomly named Mongo test database's schema per
pytest run. They remove all synthetic documents between cases, close each
case's client, and drop that owned database at run end. This avoids repeatedly
creating/dropping hundreds of identical indexed collections while preserving
fresh application state and real cross-KG/organization checks. Independent Mongo
profile/source-database tests still allocate separate databases explicitly.

Qdrant's pinned SDK has a separate background compatibility probe that ignores
the configured HTTP transport. The adapter replaces that probe with an explicit
version check through the same bounded direct client used for data requests.
Incompatible versions fail the capability check; compatibility checking is
preserved. Tests reproduce ambient-proxy routing and verify both version and
data traffic stay on the approved connection.

Task 10's longer publication suite reached the S3 128 MiB cap at the upload
boundary. The same total now assigns MongoDB 512 MiB and S3 192 MiB; observed
warm Mongo usage was about 260 MiB with the allocator and schema changes above.
S3 uses 1 MiB filer chunks, an 8 MiB reader-buffer budget, no persistent chunk
cache, a 96 MiB Go memory goal and earlier garbage collection. The default
[pinned reader cache](https://github.com/seaweedfs/seaweedfs/blob/4.48/weed/s3api/s3api_server.go)
otherwise allows 256 downloader slots without a byte budget. Application writes
share one writer slot per registered backend and keep it until synchronous SDK
I/O finishes, including cancellation. Client multipart parts remain 5 MiB.
The streamed 50 MiB boundary passed after these changes; sustained regression is
verified separately rather than inferred from that one check.

Seaweed mini persists nondefault CLI settings in `mini.options`. Removing a flag
from Compose can restore its saved value on restart; inspect only the relevant
nonsecret keys when troubleshooting. Trial concurrency gates were removed from
that saved configuration after preserving a backup. These configuration changes
do not delete bucket data. Eight lazy volume slots allow tests to continue while
older abandoned fixture buckets are preserved pending explicit cleanup approval.
Additional slots do not reserve RAM or eagerly allocate eight full data files.
