# PhageAgent KG code review and reuse

## Review scope

This is a static review of the local PhageAgent source tree for reuse in Domain KG. It did not contact a running KG, modify PhageAgent, execute its evaluation jobs, or inspect an actual LightRAG storage snapshot. The application wrapper and the upstream LightRAG library have different capabilities; the wrapper's missing features do not imply that upstream lacks them.

## Reuse map

| Existing source | What it provides | Domain KG treatment |
| --- | --- | --- |
| [Triple graph](/Users/zachary/Workspace/PhageAgent/codes/bioagent/tool_box/tools_impl/graph_rag/graph_rag.py) | CSV triples, NetworkX graph, lexical overlap, neighborhood JSON | Import format and graph-response reference; retain as an offline baseline, not the managed store |
| [Graph service](/Users/zachary/Workspace/PhageAgent/codes/bioagent/tool_box/tools_impl/graph_rag/service.py) | Lazy loading, error mapping, focused result ordering | Reuse interface ideas; replace global singleton and app-specific settings |
| [LightRAG tool](/Users/zachary/Workspace/PhageAgent/codes/bioagent/tool_box/tools_impl/lightrag_query.py) | Async retrieval-only client, bounded previews, structured evidence, timeout handling | Adapt into the primary LightRAG client boundary with stable source IDs, scoped retrieval, revision receipts, and app-independent settings |
| [Query gateway](/Users/zachary/Workspace/PhageAgent/experiments/knowledge_graph/lightrag_vectorrag_eval/construction_scripts/scripts/rag_query_gateway.py) | Concurrent fan-out, lexical recall, reranking, merged citations, partial shard metadata | Extract fan-out/fusion ideas; replace fixed shards with storage profiles, fast/deep modes, accepted views, and explicit failure policy |
| [Deduplication preparation](/Users/zachary/Workspace/PhageAgent/experiments/knowledge_graph/lightrag_vectorrag_eval/construction_scripts/scripts/prepare_new_md_deduped_to_shards.py) | Normalizes parser-specific IDs and paths before hashing; deterministic shard placement | Reuse normalization concept, version it, use SHA-256, and scope reuse to authorized material classes |
| [Global manifest](/Users/zachary/Workspace/PhageAgent/experiments/knowledge_graph/lightrag_vectorrag_eval/construction_scripts/scripts/build_global_dedup_manifest.py) | Material hashes, document and shard identifiers, status inventory | Build an import manifest with stable namespaces and provenance checks |
| [Lexical index builder](/Users/zachary/Workspace/PhageAgent/experiments/knowledge_graph/lightrag_vectorrag_eval/construction_scripts/scripts/build_rag_lexical_index.py) | SQLite FTS5 over LightRAG chunks | Reuse fixture and query ideas; implement source-aware Neo4j full text indexing with the common accepted read scope |
| [Document reader](/Users/zachary/Workspace/PhageAgent/codes/bioagent/tool_box/tools_impl/document_reader.py) | Basic PDF, DOCX, image, and text reading | Useful smoke fixtures and error cases; replace flattened extraction with location-preserving parser output |
| [Scientific graph](/Users/zachary/Workspace/PhageAgent/codes/data2evidence/src/scientific-graph.ts) | Evidence nodes, hashes, accepted attempts, lineage manifests | Port provenance principles; it is a derivation graph, not a document entity extraction engine |
| [Evaluation bundle](/Users/zachary/Workspace/PhageAgent/experiments/knowledge_graph/lightrag_vectorrag_eval) | Questions, vector baseline, graph answers, judging and reports | Adapt harness structure; create work-domain fast/deep, revision, source-change, and failure-recovery benchmarks |

The source links point to the original local workspace. Source-relative paths are also listed in `research-and-skills.md` for use on other machines. Do not copy corpora, evaluation outputs, credentials, or launch scripts wholesale. Review ownership and preserve upstream licenses and source attribution when adapting implementation.

## Concrete limitations in the inspected implementation

1. **Graph results have no semantic relevance floor.** `graph_rag.py:105–133` sorts every triple and takes the first k, including zero-score triples. An unrelated query can therefore return unrelated facts. The new graph baseline needs an explicit no-evidence outcome.
2. **Neighborhood size is not a hard bound.** `graph_rag.py:135–164` checks the limit after adding a whole frontier; a high-degree seed can exceed `max_nodes`. `visited` is populated but does not prevent re-expansion. Enforce node and edge caps during insertion and skip visited nodes.
3. **Same-file edits do not trigger reload.** `service.py:49–54` returns the cached graph when `os.path.samefile` succeeds. Updating content at that path leaves the process with its old graph. Domain KG uses accepted MongoDB manifests and scoped Neo4j/Qdrant projections rather than a process-global graph singleton.
4. **Focus ordering and prompt evidence can disagree.** `service.py:132–139` reorders returned triples after `rag.query` has already constructed the prompt. If reused, evidence formatting must follow the final selected and ordered evidence set.
5. **Entity IDs are bare names.** `graph_rag.py:58–75` keys nodes by entity strings, so homonyms merge and later types can overwrite earlier ones. There is no KG or source namespace. Use stable internal entity IDs with source-bound observations and explicit resolution decisions.
6. **Shard order can affect recall.** `rag_query_gateway.py:559–636` accumulates results in shard order and truncates the combined candidate list before later global reranking. A later shard's best result can be excluded before reranking sees it. Retrieve bounded candidates fairly per KG and fuse ranks before applying a global cutoff.
7. **Partial availability is not an explicit caller choice.** The gateway reports success when any shard succeeds at line 639. It does return shard metadata, which is useful, but a client expecting complete coverage can mistake it for a complete answer. Require `allow_partial` and return a separate partial status.
8. **The retrieved-source hint is not an access filter.** The request model at `rag_query_gateway.py:103–123` has retrieval options but no shared user/KG/material ACL contract. Source rewriting and rerank priority cannot enforce permissions. Build the authorized scope before every retrieval branch.
9. **The checked wrappers do not own the full material lifecycle.** `lightrag_query.py:77–253` calls `/query/data`; the local NetworkX class loads CSV. Neither entry point preserves original uploads, versions material contributions, or implements permission-aware removal. Domain KG must own those records around its primary LightRAG engine. Native mutation APIs do not replace source ownership, persistent curation, or cross-store publication.
10. **Legacy evidence has limited location metadata.** The CSV contract includes `pdf_name` and `source`, not a guaranteed material version and exact source span. Migration needs source reconciliation and an explicit imported-unverified state when originals are unavailable.

These findings inform the LightRAG-centered NoSQL design; no fixes were applied to PhageAgent. Source provenance, node edits, and graph/vector synchronization are service-owned extensions to the upstream engine.

## Migration sequence

1. Inventory the actual files and snapshots the owner wants to import. The included construction bundle excludes large storage and source corpora, so it cannot by itself recreate all existing KG data.
2. Export a consistent source manifest containing old document IDs, hashes, filenames, chunk IDs, source content availability, and KG origin. Hash the manifest and preserve old-to-new ID mappings.
3. Create a separate target KG, storage profile, and domain schema. Import original materials and parse them before resolving legacy supports where possible.
4. Validate triples and GraphML endpoint types; reject malformed cross-namespace edges. Mark unsupported provenance as imported-unverified and hide it from default factual evidence search.
5. For LightRAG snapshots, import source-linked chunks and relations through the primary engine adapter and versioned importer, then publish through verified MongoDB/Neo4j/Qdrant manifests. Do not write directly to undocumented upstream storage from the new service.
6. Compare material counts, supported assertions, source coverage, and retrieval on a frozen sample. Exercise deletion of one multiply supported fact and verify that another material's support remains.
7. Activate the new KG after its acceptance checks pass. Keep the original service and source snapshot available for rollback; redirect clients explicitly.

The historical evaluation bundle reports improvements over its vector baseline. Treat those as results for its particular corpus, configuration, and judge, not expected gains for team work documents. Reuse evaluation structure and measure again.
