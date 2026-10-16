# Implementation progress

Updated 16 October 2026. Execution is native in this chat, following the current NoSQL design and the task dependency order. Each completed task requires a laptop smoke test; expensive OCR, local models, load tests, and large-corpus benchmarks are outside this run.

| Task | Status | Verification |
| --- | --- | --- |
| 1: LightRAG and NoSQL compatibility spike | In progress; retrying real stores after Docker cleanup | Eleven lightweight tests, lint, and mypy pass. Six real-store tests await services. |
| 2–19 | Pending | Depend on the Task 1 real-store gate. |

Implemented the locked Python environment, protected mounted MCP transport, source-preserving LightRAG extraction boundary, explicit scoped projection probe, real-store test fixtures, resource-limited Compose stack, private development environment generator, and reproducible smoke runner. The default MCP verifier denies tokens until external OIDC is implemented.

A live loopback smoke also verified `/healthz` returns 200 and unauthenticated `/mcp` returns 401 with a Bearer challenge. The development server shut down cleanly afterward.

Run the available checks from the repository root:

```bash
uv sync --locked
python3 scripts/smoke.py --offline
```

Docker storage was emptied at the user's explicit request: images, containers, volumes, and build cache all reached zero, leaving about 57 GiB free in the Docker data filesystem. Only this project's pinned smoke images are being downloaded again. Run the complete Task 1 smoke:

```bash
python3 scripts/smoke.py --task 1
```

The four services have hard limits of MongoDB 448 MiB, Neo4j 640 MiB, Qdrant 192 MiB, and S3 128 MiB: 1,408 MiB (1.476 GB) combined, below the user's 1.5 GB RAM cap. Swap is disabled. The runner rejects unbounded RAM, swap allowance, or an over-budget total, and verifies the running Docker limits before tests. CPU limits total 2.5 cores; image pulls run serially.

The runner stops services it started after the test; `--keep-services` keeps them running for inspection. It retains volumes and never prunes images during ordinary smoke runs. Existing running services are left running. Credentials live in ignored `.env`, generated with restrictive file permissions.

The initial full-suite attempt failed on a Neo4j connection while Docker was full; its retry awaits the real stores. Task 1 is not complete; its plan checkboxes remain unchecked. Source decisions, exact versions, verification limits, and the resumption gate are in [decision 0001](decisions/0001-runtime-and-lightrag.md).
