# LightRAG construction, serving and upgrade boundary

LightRAG is pinned to `lightrag-hku==1.5.7` in `uv.lock`. No upstream patch is
carried. Domain KG owns current authorization, evidence validation, canonical
identities, source ownership and publication. Upstream mutation APIs may operate
only on isolated staging; they cannot change an accepted projection in place.

The installed helper signatures and pinned upstream source were inspected before
adding the capability gate. `checked_capabilities()` rejects a different version
or incompatible extraction, mix-search or vector-query interface. This is an
upgrade tripwire; the integration suite remains mandatory before updating a pin.

| Boundary | Upstream behavior | Managed service behavior |
| --- | --- | --- |
| Construction | `extract_entities` returns source/chunk-bound proposals | Validate schema and actual source spans before accepted native extraction; imported legacy records remain unverified |
| Retrieval | Native mix combines graph and vector inputs | Resolve current immutable scope, apply branch filters before ranking, supply only verified request-local graph/vector inputs, authenticate returned evidence and recheck policy |
| Vector scope | Stock Qdrant query selects a workspace without a per-request ACL/filter argument | Use the scoped service adapter for organization, KG, accepted version/curation, visibility and requested filters |
| Deferred writes | Upstream vector upsert buffers work until `index_done_callback()` | Flush/read back readiness; stage immutable attempt IDs and accept only at the MongoDB manifest commit |
| Legacy snapshots | A native store layout is not an import contract | Accept the bounded portable envelope with explicit source IDs and checksum claims; never treat claimed hashes as available originals |

See the pinned [construction and mix implementation](https://github.com/HKUDS/LightRAG/blob/v1.5.7/lightrag/operate.py)
and [Qdrant adapter](https://github.com/HKUDS/LightRAG/blob/v1.5.7/lightrag/kg/qdrant_impl.py).
Code calls the dependency's helpers; it does not copy their implementation. The
[PhageAgent reuse map](../code-reuse.md) attributes the reviewed CSV, normalization,
manifest, bounded-error and evaluation ideas. No PhageAgent code, corpus or private
snapshot was copied, migrated or changed.

Service modes are `fast`, `deep`, `vector`, `lexical` and `graph`; native `mix` is an
internal orchestration detail. Unknown filters or native mode names fail typed
validation. An unsupported restriction never falls back to broad workspace reads.
Existing legacy gateways stay private/read-only and are not registered as a
managed serving backend.

The compatibility suite compares native mix helper output with managed deep
retrieval on identical already-permitted synthetic graph/vector fixtures. It also
checks default unverified exclusion and a reader denied the restricted import.
This isolates interface/ID preservation and permission boundaries. It is not a
quality comparison with a separate unfiltered deployment, a real-model benchmark,
or authorization proof for stock LightRAG. Construction and deferred-write probes
exercise actual upstream functions with deterministic tiny responses and real
Qdrant. The focused real-store gate passed nine tests in 279.27 seconds. The opted-in
editor fixture retained exactly two nodes, one relation and one vector chunk;
default unverified retrieval and the denied reader retained zero. Managed deep
results and upstream helper chunk IDs agreed. The offline Task 16 smoke passed
31 checks with 27 real-store cases explicitly deselected; the combined final
Task 16 gate remains separate.

The upgrade suite is `test_lightrag_capabilities.py`,
`test_lightrag_scope_probe.py`, `test_legacy_import.py`, `test_import_worker.py`,
`test_import_recovery.py`, `test_import_curation.py`, `test_import_mappings.py`,
`test_import_http.py` and the engine capability unit tests. All database cases run
serially under the laptop's separate 1.5 GB Docker database ceiling.
