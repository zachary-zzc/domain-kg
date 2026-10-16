# Legacy import boundary in progress

The offline decoder accepts a CSV triple export, directed GraphML, or a portable
LightRAG 1.5.7 snapshot envelope. It consumes bytes supplied by the service, never
opens a caller-selected path, connects to a legacy gateway or runs database text.
The decoder and durable planning service do not admit or publish imports; jobs,
source reconciliation and verified graph/vector publication are subsequent work.

Inputs are at most 2 MiB, 1,000 nodes, 1,000 relations after source expansion and
2,000 source-owned observations. Parsing rejects malformed fields, duplicate or
dangling IDs, namespace injection, nested graphs, unsupported directedness, XML
DTDs/entities, unknown snapshot sources and incompatible snapshot versions. Source
fan-out is bounded during construction. Opaque IDs retain their original spelling.

The CSV contract is the seven PhageAgent columns `entity1`, `entity1_type`,
`relation`, `entity2`, `entity2_type`, `pdf_name`, and `source`. Source rows retain
independent ownership. Legacy names are opaque keys within the declared import
origin; all planned identities are generated under the service's organization/KG.
No string in an export establishes an accepted database identity or permission.

GraphML uses `entity_name`, `entity_type`, `description`, `source_id`, and `relation`
data keys. Descriptions aggregated across unknown sources are dropped from serving
records with an explicit warning; the original upload remains available for
review. Isolated nodes retain a `missing-original` ownership marker. A portable
snapshot declares `format_version=1`, `engine_version=1.5.7`, `consistent=true`,
source document IDs/checksums, nodes and source-linked edges. This is an explicit
export envelope, not an undocumented native database dump reader. Its consistency
flag and claimed source hashes do not prove that originals are available.

All decoded assertions remain `imported_unverified`. No snapshot status, uploaded
CSV or declared checksum manufactures a quotation from a missing document. The
future execution service must reconcile originals, enforce current source policy
and exclude unverified claims, descriptions and import chunks from default search
before any activation is allowed.

The raw SHA-256 identifies exact uploaded bytes. A separate versioned NFC/newline
normalized fingerprint is scoped by organization, KG and import origin; it never
changes evidence text or bypasses source permissions. Normalized equality alone
cannot establish an accepted extraction or authorize artifact reuse.

The reviewed [PhageAgent reuse map](code-reuse.md) supplies the CSV format and
manifest/normalization ideas. This adapter is newly written; no application code,
corpus, private snapshot or evaluation output was copied. Stable namespaces,
source ownership and current policy are Domain KG's responsibility.

The shared planning service reads a completed upload owned by the current caller,
checks all blob/reference fields and its byte checksum, validates node and
predicate types against the selected schema, then seals an immutable MongoDB plan.
The plan binds the actor, namespace, policy, KG configuration/publication/visibility,
schema and original blob. It expires within one hour or with its upload. Replay
rechecks the original, current transactional references and sealed plan/normalized
records; a stale binding or conflicting idempotency key is rejected. Plans expose
counts, hashes, warnings and revision pins, without private export text or keys.

An optional opaque import `origin` preserves old/new identity mappings across
changed exports from the same legacy system. Without one, the exact upload hash
provides an isolated origin. Neither an origin nor an export establishes a grant.
Planning alone creates no job or accepted publication binding and is not yet a
public transport operation.

Accepted import provenance is a source-qualified immutable marker copied into the
embedded document, projection attempt and accepted binding, and reconciled with
the authoritative origin ledger. Missing or mismatched markers fail closed before
visibility selection. Native records omit this optional extension to preserve
existing sealed serialization. Default scopes exclude imported sources before
branch predicates; opting into unverified evidence still requires current ACLs.
The explicit prepared-import type rejects verified/manual claim support, invented
claim spans and inherited descriptions; its snapshot chunks retain their actual
parsed artifact separately. Admission and the import worker are not wired yet.

Internal atomic admission now joins the material/version, restricted uploader ACL,
import job/outbox, origin ledger, quota transfer and upload claim in the existing
MongoDB material transaction. It validates the exact sealed plan and current
policy/schema/publication again, and verifies plan/job/source/provenance references
before returning a receipt. Concurrent and original-read-racing calls observe one
accepted job. Forged plans cannot reserve the valid command key. Native material
submission remains unchanged. This component is private: ordinary workers still
exclude import jobs, and no public execute route is available until the dedicated
import pipeline is ready.

The prepared-row builder verifies the exact export checksum and retains its raw
UTF-8 text (including a BOM, when present) as export context. Original document
claim spans remain empty. Observation/support IDs include material and version,
while semantic assertion IDs include polarity and remain stable across versions
from the same declared origin. GraphML and portable snapshots preserve explicit
positive, negative and uncertain polarity; the CSV contract implies positive.
A decoded name absent literally from raw XML/JSON context produces an explicit
unlocated-record warning, without a fabricated quotation. Earlier experimental
private plans predating polarity support must be recreated rather than reused.
