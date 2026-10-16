# Scoped graph inspection

`GraphService.get_node` and `subgraph` resolve the same immutable `ReadScope`
as search. MongoDB supplies accepted source/attempt mappings and current material
metadata. Integrity checks follow material eligibility, so an unreadable
material is not opened to derive fields or probe its private contents.

Each eligible source is checked against its sealed S3 artifact and exact Neo4j
payload, endpoint links, projection IDs, count and checksum. Missing or changed
projections fail closed and record a repair request. Default journal verification
still checks graph, lexical and vector readiness. Graph-only reads use separate
connections and do not require the vector backend to be available.

Names, aliases, descriptions, types and counts come from eligible source-owned
observations. Bare anchors provide identity and adjacency only. A node includes
at most 50 observations and reports truncation and full eligible counts.
Predicate/confidence/entity restrictions also constrain node descriptions.
Restricted aliases cannot seed retrieval. Source-less manual curation follows
in Task 14.

Fixed, parameterized Cypher follows the namespace/entity compound index to
support rows. One accepted-binding predicate pairs the source with its attempt,
manifest and hash; independent source and attempt lists could admit a
cross-product. Eligible support IDs apply status, confidence, entity/type and
predicate restrictions before ordering and expansion. A visited set and
deterministic insertion enforce zero to two hops, 200 nodes and 400 edges,
including seeds. An extra row detects cap truncation. Each returned edge includes
its support, assertion, source version and true supporting spans.

The request-bound LightRAG graph reader calls the same repository and never reads
global summaries. Graph recall builds bounded paths and source/chunk citation
identities without a query LLM or extraction call. Deep orchestration follows
in Task 13.

This implementation authenticates sealed artifacts on each request, with hard
budgets of 1,000 eligible bindings and 20,000 sealed rows. Exceeding a budget
returns `QUOTA_EXCEEDED`, without silently truncating the eligible corpus.
These are laptop-safe correctness bounds, not production throughput claims.
Indexed per-record authentication may replace whole-artifact checks while
preserving acceptance. Current policy, visibility and publication are checked
again before any graph response returns.

Run `python3 scripts/smoke.py --task 11 --keep-services`. Tiny synthetic fixtures
cover a cycle, fan-out, a visible island behind a restricted bridge, unpublished
attempts, concurrent readers, revocation and deliberate projection corruption.
The suite checks an actual Neo4j `EXPLAIN` plan for indexed adjacency.
