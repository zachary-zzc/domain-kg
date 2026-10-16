# Federated evidence search

`RetrievalService(queue, providers).search(access, SearchRequest(...))` is the shared
domain entry point. HTTP/MCP wiring follows in Task 15. It authorizes every selected
KG before retrieval, resolves accepted source visibility, runs at most two KGs at
once, and rechecks all policy, publication and visibility fences before returning.

Fast mode uses filtered source vectors and scoped lexical recall. It never calls
graph expansion, a generative keyword model, or a reranker. Query embeddings may
call the approved embedding endpoint. Explicit vector, lexical and graph modes
use their corresponding branches.

Deep mode adds bounded graph exploration and pinned LightRAG 1.5.7
`_perform_kg_search(mode="mix")` orchestration through request-bound adapters.
Source-vector hits seed visible entity observations; lexical names/aliases can
also seed traversal. There is no mixed-access global entity vector or summary.
The adapters expose only the verified, bounded request graph and source-vector
recall; native calls cannot expand into an unrestricted store. Native logs receive
an opaque query identifier. This maintained integration differs from stock
LightRAG's global entity/relationship-vector stores.

Every returned full chunk, source owner, citation and graph step is authenticated
again against sealed accepted artifacts before optional reranking. Fusion keeps
KG/source/version/chunk/curation identities separate, rejects conflicting sealed
text, removes duplicate branch hits, and uses equal-weight reciprocal rank fusion
with constant 60. Raw similarity values never cross embedding profiles.

Selection waits for all KGs. When top-k permits, one best hit is reserved per
nonempty KG, then remaining slots use fused rank. The evidence text budget is
shared across selected hits; only exact text prefixes are shortened, citation IDs
retain their original chunk ownership, and truncation is explicit. A branch pool
reaching 50 candidates also reports conservative truncation.

Keyword generation and reranking require explicit deep-mode flags. Keywords are
bounded literal query terms; their elapsed time is reported in response warnings.
Reranking considers at most 50 candidates across the whole request, sends each
KG's evidence only through that KG's approved profile, and accepts only a complete
permutation of known indices. Model output cannot rewrite evidence or citations.
Restricted material requires provider clearance before and after the call.

Branch status distinguishes empty, skipped, unavailable and unsupported results.
Partial coverage requires `allow_partial=true` and at least one valid retrieval
response; an empty authorized response is valid. Only typed derived-backend I/O,
provider unavailability or unsupported branches can qualify. Authority, policy,
source-artifact, malformed-evidence, embedding-space and publication-integrity
errors fail the request. A generic backend error cannot silently become partial
evidence. No cross-KG distributed snapshot is claimed.

Run the small local check with `python3 scripts/smoke.py --task 13 --keep-services`.
The model provider tests use synthetic HTTP responses; live model acceptance is
recorded separately in [the model proposal](model-proposal.md).
