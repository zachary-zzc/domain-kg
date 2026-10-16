# Material originals and submission

The HTTP API streams original bytes through `POST /api/v1/kgs/{kg_id}/uploads`.
Query parameters supply `media_type`, `sha256`, `size_bytes`, and an
`idempotency_key`; replacement uploads also supply `target_material_id`.
The request Content-Type must match. Extra parameters, object keys, and server
paths are rejected. A caller needs current editor/owner access before any bytes
are stored. Upload receipts contain no bucket, key, or credentials.

Upload IDs belong to the exact verified organization/KG/uploader. Completion
checks length, UTF-8 or file signature, SHA-256, and the 50 MiB limit. Full parser
validation happens in the subsequent parsing stage. Unclaimed uploads reserve
organization and KG quota for at most 24 hours. Expiration fences claims before
object deletion and releases quota once; failed cleanup retains its ownership
record for retry. An upload lease prevents concurrent replay from interfering
with the first stream. An interrupted stream may be retried after its five-minute
lease expires; immutable stored bytes are verified before recovery.

`POST /api/v1/kgs/{kg_id}/materials` accepts an upload ID or inline UTF-8 text,
title, metadata, ACL mode, and an idempotency key. One MongoDB transaction creates
the stable material/version, source ownership record, current ACL, queued job,
outbox event, and receipt. The command journal records hashes rather than inline
source text. Identical bytes under different materials/ACLs use distinct objects.
Selected logical Mongo databases share the authority client, so policy and
source records participate in the same replica-set transaction.

`POST /api/v1/kgs/{kg_id}/materials/{material_id}/versions` also requires
`expected_active_version`. A replacement remains staged and leaves the accepted
version and searchable metadata intact until verified publication. A receipt
means durable acceptance for processing, not graph publication.

Original downloads use
`GET /api/v1/kgs/{kg_id}/materials/{material_id}/versions/{version_id}/original`.
The service verifies the complete object hash before sending bytes, checks the
version-to-material chain, and rechecks current permission/ACL between chunks.
Readers can download accepted/retained versions; editors/owners can inspect
staged versions within their visible material scope. Responses are private,
non-cacheable attachments, with no permanent public object URL.

Run `python3 scripts/smoke.py --task 6 --keep-services` on this laptop. The checks
include one streamed 50 MiB synthetic object, immutable-key collision, MIME/hash
rejection, upload ownership, cross-database transaction rollback, replay races,
quota/expiry, replacement conflicts, HTTP privacy, and mid-stream revocation.
The full system's worker/parser/publication stages are separate task gates.
