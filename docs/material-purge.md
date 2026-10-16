# Retained material cleanup

`MaterialLifecycle.purge` accepts a typed `PurgeMaterial` command with a material
ID, expected revision, reason and idempotency key. Current KG owner access is
required for admission, job status, cancellation/retry and every worker stage.
An exclusion is insufficient: the material must be tombstoned and its live
retention deadline must have passed. A dry run returns only its owned impact and
creates no job. HTTP/MCP exposure follows in Task 15.

Admission writes a durable cleanup intent and job. The worker checks source and
artifact ownership, full object-key references, source span blocks, projection
attempt/job bindings, active uploads, source workers and retained read pins. It
refuses inconsistent references before physical deletion. Work is bounded to
1,000 versions and 20,000 objects, artifacts, supports or attempts; an older
projection generation requires explicit cleanup routing.

A fenced Mongo publication removes only this material's source binding from a new
accepted view. Other source and curation bindings keep their immutable receipts.
The worker then deletes exact namespace/attempt/manifest/generation/hash graph and
vector records and verifies their absence. Original/artifact deletion uses typed
object identities beneath this material's server-selected namespace. Source-owned
orphan objects are included; each discovered key must have the canonical UUID
layout and valid object metadata. No bucket, global content hash or another
material's prefix is deleted.

Cleanup remains fenced if storage fails or the actor loses permission. Explicit
job retry resumes idempotently, including after partial object deletion. The
worker verifies that the owned object prefix is empty before the final Mongo
transaction marks the material purged, releases quota once and accepts the ready
job receipt. Mongo acceptance and physical store deletion are separate stages.

Final cleanup removes source blocks/chunks/observations, engine-name bindings and
source support spans. It removes orphan assertions only when no independent
support or human history needs them. Opaque entity/version identities, publication
metadata and durable human curation history remain. Material display metadata is
scrubbed, and a purged source cannot be restored. Independently owned source
support, human corrections and public human claims keep their ownership. Backup
expiration belongs to the separate owner maintenance workflow.

The provider-free native worker is:

```bash
uv run python -m domain_kg.worker --once --purge-only
```

The bounded laptop tests use synthetic materials and the capped local stores:

```bash
uv run pytest tests/integration/test_material_purge.py tests/integration/test_material_purge_recovery.py -q
```

They cover retention/owner denial, impact previews, idempotency, independent source
support, current permission revocation, shared-reference refusal, active read pins,
orphan cleanup, and interruption/retry without duplicate quota release.
The five recovery checks passed in 87.58 seconds; the two source/curation checks
passed in 90.80 seconds. No additional container or model was started.
