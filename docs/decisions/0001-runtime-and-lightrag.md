# Task 1: runtime and LightRAG compatibility

Status: Task 1 verified on 16 October 2026, with 17 passing tests against real local stores under the revised 1.5 GB RAM cap. This compatibility spike does not establish production readiness or complete later domain/identity tasks.

## Locked runtime

Python is constrained to 3.12; this laptop used its installed CPython 3.12.7. `uv.lock` fixes all 106 resolved packages. Direct pins include:

| Component | Version |
| --- | --- |
| Official MCP Python SDK and mcp-types | 2.3.0 |
| LightRAG (`lightrag-hku`, without API/offline/model extras) | 1.5.7 |
| FastAPI / Uvicorn | 0.143.2 / 0.54.0 |
| PyMongo async API | 4.19.0 |
| Neo4j driver | 6.4.1 |
| Qdrant client | 1.19.1 |
| Boto3 | 1.43.114 |
| Pydantic / pydantic-settings | 2.14.1 / 2.16.0 |

Compose pins ARM64-capable image manifests for MongoDB 8.0.34, Neo4j Community 5.26.32, Qdrant 1.19.2, and SeaweedFS 4.48. Its single-process S3 service satisfies the object-store boundary. MinIO's latest source release had no matching Docker Hub image; its repository is archived and Docker Hub images have been withdrawn. The actively maintained SeaweedFS single-node S3 option avoids building a replacement MinIO image. See the [official SeaweedFS quick start](https://github.com/seaweedfs/seaweedfs#quick-start) and [MinIO releases](https://github.com/minio/minio/releases).

## MCP transport decision

Inspected the [official SDK 2.3.0 server source](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/src/mcp/server/mcpserver/server.py) and the installed package before implementation. The selected API is `mcp.server.mcpserver.MCPServer`, not the older FastMCP import. The SDK owns `/mcp` and protected-resource routes in a root-mounted `streamable_http_app(json_response=True, stateless_http=True)`. The FastAPI lifespan runs `server.session_manager.run()`.

The SDK's `Client` calls the mounted ASGI app through `httpx2` and its Streamable HTTP transport. The smoke verifies actual discovery and a structured tool call with protocol `2026-07-28`. A live external MCP client and older-protocol interoperability remain unverified. `ToolAnnotations` uses Python snake-case arguments and serializes the protocol field names.

Configure DNS-rebinding Host/Origin checks for the public origin and explicitly set `AuthSettings.validate_token_resource=True`. The default verifier rejects every token until Task 3 installs external OIDC verification. A test-only issuer verifier is injected only by tests; no request argument, development static token, or local password bypass exists. Only a read-only transport probe is exposed; KG methods arrive in their planned tasks.

## Source-preserving engine decision

The [pinned LightRAG extraction function](https://github.com/HKUDS/LightRAG/blob/v1.5.7/lightrag/operate.py) accepts chunk-ID keyed inputs and returns source-bound observations. A deterministic, asynchronous response exercises the real upstream extraction parser with two original chunk IDs. Observations remain explicitly unverified: source-span/schema validation and canonical assertions belong to Task 9. Extraction does not perform database commands or publish records.

Engine names are aliases mapped to independently assigned UUIDs inside organization/KG namespaces. The spike's in-memory identity map proves the boundary and rejects accidental rebinding; it is not the durable mapping required in Task 9.

The [upstream Qdrant adapter](https://github.com/HKUDS/LightRAG/blob/v1.5.7/lightrag/kg/qdrant_impl.py) queries with a workspace condition and has no general per-request material/ACL filter argument. It also contains synchronous Qdrant I/O inside asynchronous methods. Stock query methods cannot serve protected evidence unchanged. `ScopedProjectionProbe` demonstrates explicit immutable scope arguments compiled into Neo4j and Qdrant predicates; the real concurrent-source tests passed with two interleaved accepted-version scopes. It is a compatibility probe, not the authoritative `ReadScope` resolver from Tasks 2/4/11.

`upsert()` buffers vector records and defers embedding; `index_done_callback()` performs embedding and persistence. The embedded Qdrant characterization test observes counts 0 before flush and 1 after flush, with one embedding call. The same characterization is included against the real Qdrant service. The embedded test is not a substitute for server integration, publication, or permission tests. The full lifecycle of a blocking upstream backend runs inside `run_blocking_engine()` on a worker thread; serving probes use native asynchronous clients. Runtime installation is forbidden by the characterization fixture, with required drivers preinstalled in the lockfile.

No upstream source patch is carried. The maintained extension boundary is extraction/source mapping plus explicit source-filtered serving adapters. Task 9 must pin the internal extraction function contract; Tasks 10–13 must replace compatibility scopes with accepted manifests, verified receipts, and current authorization. Stock mutation APIs must operate only in isolated staging, as required by the publication protocol.

## Laptop checks and blocker

The laptop has 16 GiB RAM. The user reduced the initial 2.25 GiB service budget to a hard 1.5 GB maximum. Limits now total 1,408 MiB (1.476 GB): MongoDB 448 MiB, Neo4j 640 MiB, Qdrant 192 MiB, and S3 128 MiB. Swap is disabled. The smoke runner checks both Compose and running Docker limits and rejects an over-budget configuration. CPU limits total 2.5 cores, image pulls are serial, published ports bind to loopback, and inputs use generated private credentials and synthetic fixtures. OCR, local model downloads, large-corpus evaluation, and load generation are excluded. Tiny correctness checks do not establish the acceptance document's performance targets.

Initial results before Docker cleanup:

- `uv sync --locked`: succeeded.
- `python3 scripts/smoke.py --offline`: lint passed, strict mypy passed, six tests passed and six real-store tests were deselected explicitly.
- Live loopback server smoke: `/healthz` returned 200 with `{"status":"ok"}` and `/mcp` returned 401 with a Bearer challenge; the probe then shut down cleanly.
- `docker compose up -d mongo neo4j qdrant object-store`: failed during image pull with `no space left on device`; no containers started.
- `colima ssh -- df -h /var/lib/containerd`: `/dev/vdb1`, size 59 GiB, 100% used, 0 available.
- `python3 scripts/smoke.py --task 1`: exits nonzero before pulling when the Colima disk has less than 4 GiB free. It never prunes images or resizes the VM.
- Complete current suite, `uv run --no-sync pytest -q --maxfail=1`: two passed, one real Neo4j fixture connection error because the service is absent. Full suite is not green.

After explicit authorization to empty Docker storage, cleanup reached zero images, containers, volumes, and build cache, with about 57 GiB free. Only the four pinned project images were downloaded again. Five new memory-budget guards passed after being observed failing first; the lightweight suite has eleven passing checks.

The final `python3 scripts/smoke.py --task 1 --keep-services` passed all 17 tests in 7.86 seconds: the real MongoDB commit/abort, Neo4j source links/full text, Qdrant filtering/concurrency, S3 byte round-trip, and server-backed LightRAG flush are verified. Three warnings concern intentional API keys over loopback HTTP; this is not a shared-deployment TLS configuration. Kernel cgroups confirmed 1,476,395,008 bytes of aggregate hard RAM limits and zero swap allowance; no service was OOM-killed or restarted.

Startup/compatibility fixes retained the budget: remove unsupported `server.metrics.enabled` from Neo4j Community; bypass macOS HTTP proxies for the local S3 fixture (`botocore.config.Config(proxies={})`); allow four bounded 64 MiB SeaweedFS disk-volume slots, since the internal system volume consumes a slot before S3 bucket collections can allocate one; and wait for the S3 listener before testing. Default SDK checksum handling remains enabled. See the [botocore proxy configuration](https://docs.aws.amazon.com/botocore/latest/reference/config.html) for the explicit client override. Task 2 may now proceed; later permission, publication, provider-quality, and performance gates remain pending.
