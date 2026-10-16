# Laptop resource limits

The smoke stack has a combined hard container RAM limit of 1,408 MiB (1.476 GB): MongoDB 448 MiB, Neo4j 640 MiB, Qdrant 192 MiB, and S3 128 MiB. Container swap is disabled. CPU limits total 2.5 cores. These limits apply to the services; they do not change the laptop's system RAM or Colima VM configuration.

Task 4's repeated disposable schema tests exposed MongoDB's inherited open-file ceiling: WiredTiger aborted with `Too many open files` during an index build. This was not an OOM event. The Mongo container now uses the [MongoDB-recommended open-file limit](https://www.mongodb.com/docs/v8.0/reference/ulimit/) of 64,000. Raising a descriptor ceiling does not reserve additional RAM; the 448 MiB hard cap is unchanged. The existing volume recovered normally and the complete real-store suite passed after restart.

Smoke tests use tiny synthetic records and three-dimensional vectors. No local models, corpus indexing, load generation, or OCR model downloads run in these checks. Independent storage deployments are tested sequentially when simultaneous instances would exceed the RAM cap. Runtime secrets, private originals and generated indexes remain outside Git.
