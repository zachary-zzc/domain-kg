# Filtered vector and lexical branches

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
explicit. Complete mode dispatch, cross-KG fusion and deep orchestration follow
in Task 13. The LightRAG chunk-vector adapter is bound to one scope; it rejects
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
