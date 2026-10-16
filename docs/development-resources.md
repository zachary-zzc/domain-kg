# Laptop resource limits

The smoke stack has a combined hard container RAM limit of 1,408 MiB (1.476 GB): MongoDB 576 MiB, Neo4j 512 MiB, Qdrant 192 MiB, and S3 128 MiB. Container swap is disabled. CPU limits total 2.5 cores. These limits apply to the services; they do not change the laptop's system RAM or Colima VM configuration.

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
