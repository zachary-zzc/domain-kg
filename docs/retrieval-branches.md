# Filtered retrieval branches and evidence ranking

Both branches consume the same immutable `ReadScope`: current access and material
metadata, accepted source/attempt mappings, generation, support status and filters.
Eligible sealed source bodies are authenticated before derived candidates are
used. Only the requested derived backend is opened. Projection payloads, vectors
or fulltext membership that disagree with their accepted artifacts fail closed;
backend presence alone never establishes acceptance. Current policy, visibility
and the complete accepted mappings are checked again before returning evidence.

The vector branch queries Qdrant chunk projections with namespace, generation,
paired source/attempt/manifest/hash, eligible point IDs, chunk kind and embedding
space in the filter before top-k. Material filters read current Mongo metadata.
Entity, type, predicate and confidence restrictions first resolve qualifying
accepted visible support and its chunks. Without those assertion restrictions,
text-only chunks remain eligible. A source's independently owned observations
supply annotations. Every returned payload is matched against the sealed row.

A query embedding uses the KG's approved embedding space. The fingerprint binds
endpoint, model, dimensions and weight revision. A different chat model can
reuse compatible vectors; identical model names at different embedding endpoints
cannot silently share them. Query embeddings are the only provider call in these
branches. Metadata-only filter edits need no source embedding or extraction.

The lexical branch exhausts the bounded eligible fulltext keys in batches of 256,
verifies their complete payload set, and computes BM25 statistics over only the
eligible source text. It discards the index's global relevance scores. Thus a
hidden corpus cannot affect visible ranks through global inverse-document
frequencies, and a broad global top-k cannot omit a later eligible hit. User query
text never becomes Cypher or Lucene syntax: index keys are server-derived UUID
hex strings. Unicode normalization, CJK characters/bigrams and intact identifiers
support text and identifier recall without generative keyword expansion.

The read budgets are 1,000 eligible bindings and 20,000 sealed rows, with at most
512 distinct lexical query terms and 50 candidates per branch. Budget errors are
explicit. Mode dispatch, cross-KG fusion and deep orchestration use the same
scope. The LightRAG chunk-vector adapter is bound to one scope; it rejects
untyped precomputed vectors and uses the verified query-embedding path. Its
contract test executes the pinned upstream chunk-context reader.

Run `python3 scripts/smoke.py --task 12 --keep-services` for the laptop gate.
Fixtures use tiny public synthetic materials, strict mocked provider HTTP and
real MongoDB, Neo4j, Qdrant and S3. They compare filtered vector recall with an
exact eligible-set request, exercise current metadata and support restrictions,
check CJK/identifier/plain-text recall, prove hidden index changes do not alter
visible lexical scores, and inject payload corruption and mid-read visibility
changes. A separate compiler test uses 1,000 accepted-binding DTOs and only nine
Qdrant points to check paired predicates and generation/namespace isolation.
These are correctness checks, not ANN quality or large-corpus load measurements.

## Graph ranking and fusion

Graph seeds use visible names, aliases and identifiers matched within a question,
plus source-vector seeds in their original priority. Generic words and numeric-only
names are not lexical anchors. Scoped manual curation remains eligible. Node
materialization, traversal, accepted support authentication and final scope
revalidation still establish eligibility; ranking cannot establish authority.

Graph candidates are scored with scoped BM25 source relevance and bounded
predicate/path cues before the 50-candidate cap. Short supported paths may break
otherwise weak relevance ties. The predicate cues are deliberately soft lexical
hints, not a claim that a relation is entailed by its quote. Explicit
`SearchFilters.predicates` remain hard eligibility constraints on every branch.
Both lexical and graph queries reject more than 512 distinct terms explicitly.

Vector/text use RRF with constant 60. `fuse_evidence` merges graph annotations but
does not give an existing text candidate a correlated third vote. A graph-only
source competes against text tails by its rank; there is no mandatory graph quota.
Fast mode still has no graph expansion, generative query model or reranker.

Packing deduplicates evidence, retains whole passages when possible and backfills
from later candidates. It preserves per-KG representation when both budgets allow
it. An unavoidable excerpt is explicit and loses its graph paths; selection never
rewrites the accepted source.

## Unified offline evaluation

`eval/scoped_retrieval_run.py` reuses production BM25, seed scoring, bounded
`GraphRepository.traverse`, path tracing, graph ranking, fusion and packing.
Its public-data adapter replaces stores and credentials, not the separate
real-store authorization tests. Saved label joins are retrieval hints, not
accepted entity merges. Saved embeddings are replayed without a provider.

Arms are V12; vector+BM25 RAG; production relation-guided supplementation
(`GUIDED`); an experimental query-anchor/terminal-predicate constrained graph
supplement (`REL`); and the same REL sources with quoted paths (`PATH`).
REL's narrow lexical planner abstains without a named anchor or recognized
relation. It retraces from query anchors within the same bounded graph, preventing
vector-seeded intermediate nodes from flattening every explanation to one hop.
This planner is an offline experiment, not a new default service query policy.

Source budgets are identical. PATH's extra path metadata has a separate budget
and is not an equal-total-token answer experiment. The evaluator reports
reference-source coverage only as a diagnostic. Native semantic review inspects
source sufficiency and relation faithfulness, accepts equivalent wording and
reasonable source-grounded inference, and never compares answer strings.
See [the measured report](retrieval-quality-report.md).

Graph search reserves capacity for expansion before materializing the ranked seed
set: the seed limit is `max(1, max_nodes // (effective_hops + 1))`. Previously the
seeds could fill the entire node budget, leaving no capacity for an unseeded
neighbor or bridge. Zero-hop requests retain the full seed allowance. Truncation
still reports omitted seeds, and every expansion continues through the same
accepted-support and visibility checks. The public offline adapter uses this
same policy; this does not change the explicit-seed subgraph API.
