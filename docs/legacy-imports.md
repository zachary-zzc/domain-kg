# Legacy import boundary in progress

The offline decoder accepts a CSV triple export, directed GraphML, or a portable
LightRAG 1.5.7 snapshot envelope. It consumes bytes supplied by the service, never
opens a caller-selected path, connects to a legacy gateway or runs database text.
The current decoder does not admit or publish imports; durable planning, jobs,
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
