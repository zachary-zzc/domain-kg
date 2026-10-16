# Implementation progress

Updated 16 October 2026. Execution is native in this chat, following the current NoSQL design and the task dependency order. Each completed task requires a laptop smoke test; expensive OCR, local models, load tests, and large-corpus benchmarks are outside this run.

| Task | Status | Verification |
| --- | --- | --- |
| 1: LightRAG and NoSQL compatibility spike | Complete | 17 tests pass against the real local stores; lint and mypy pass. Kernel RAM/swap limits verified. |
| 2: Domain contracts and namespace integrity | Complete | 47 smoke tests pass; full regression 59 tests; lint and strict mypy pass. |
| 3: OIDC identity and organization accounts | Next | Implement and test external verification; a configured development IdP login is required for completion. |
| 4–19 | Pending | Follow the plan's dependency order. |

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

Task 1's final real-store smoke passed all 17 tests in 7.86 seconds. Three Qdrant SDK warnings identify intentional HTTP use on loopback; shared deployment TLS remains a later release requirement. The kernel reported the exact configured limits, zero swap allowance, no OOM kills, and no container restarts.

The retry fixed an unsupported Neo4j Community metrics setting, macOS system-proxy routing of local S3 traffic, and a SeaweedFS disk-volume-slot limit. The S3 probe bypasses system proxies for loopback while retaining the SDK's default checksum validation. Four bounded 64 MiB volume slots permit the internal volume and bucket collections without increasing RAM. Source decisions and verification limits are in [decision 0001](decisions/0001-runtime-and-lightrag.md).

Task 2 adds transport-independent typed contracts, immutable accepted/read scopes, honest source/manual provenance, and exact-attempt publication receipt validation. Scoped canonical inserts check every reference against its organization and KG. Mongo validators and compound indexes enforce shape and uniqueness; reconciliation checks canonical references and graph/vector mappings in both directions. These are integrity primitives; current policy enforcement follows in Task 4 and publication coordination in Task 10.

Reproduce the Task 2 laptop gate with `python3 scripts/smoke.py --task 2`. Its fixtures contain only small synthetic records and three-dimensional vectors. Source text preserves whitespace so citation offsets remain exact. A publication cannot be inferred from a staging write.
