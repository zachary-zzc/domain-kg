# Implementation progress

Updated 16 October 2026. Execution is native in this chat, following the current NoSQL design and the task dependency order. Each completed task requires a laptop smoke test; expensive OCR, local models, load tests, and large-corpus benchmarks are outside this run.

| Task | Status | Verification |
| --- | --- | --- |
| 1: LightRAG and NoSQL compatibility spike | In progress; blocked on Docker disk space | Six lightweight tests, lint, and mypy pass. Six real-store tests await services. |
| 2–19 | Pending | Depend on the Task 1 real-store gate. |

Implemented the locked Python environment, protected mounted MCP transport, source-preserving LightRAG extraction boundary, explicit scoped projection probe, real-store test fixtures, resource-limited Compose stack, private development environment generator, and reproducible smoke runner. The default MCP verifier denies tokens until external OIDC is implemented.

A live loopback smoke also verified `/healthz` returns 200 and unauthenticated `/mcp` returns 401 with a Bearer challenge. The development server shut down cleanly afterward.

Run the available checks from the repository root:

```bash
uv sync --locked
python3 scripts/smoke.py --offline
```

Docker's Colima storage volume is full: 59 GiB used, 0 available. Free at least 4 GiB inside that VM, then run the complete Task 1 smoke:

```bash
python3 scripts/smoke.py --task 1
```

The runner caps the four services at 2.25 GiB RAM / 2.5 CPU cores and stops services it started after the test; `--keep-services` keeps them running for inspection. It retains volumes and never prunes images. Existing running services are left running. Credentials live in ignored `.env`, generated with restrictive file permissions. Partial image downloads may remain in Docker's cache after the failed pull.

The full current suite is not green: its first real Neo4j connection fails because no database container could start. Task 1 is not complete; its plan checkboxes remain unchecked. Source decisions, exact versions, verification limits, and the resumption gate are in [decision 0001](decisions/0001-runtime-and-lightrag.md).
