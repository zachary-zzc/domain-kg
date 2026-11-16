# Source merge and evaluation repair implementation plan

> **For agentic workers:** Execute natively in this chat, as requested by repository instructions. The user's instruction authorizes implementation; no additional plan approval is needed.

**Goal:** Independently recall text and graph sources, merge their original evidence semantically, and repair evaluation failures without case-specific logic.

**Architecture:** Extend the existing deep reranker and source-fusion layer. The evaluator uses the same candidate-pool/ranking implementation and gives both readers original passages only; graph paths remain diagnostic data. Improve the existing general extraction/gleaning prompt rather than introduce another extractor or ontology.

**Tech Stack:** Existing Python/Pydantic, scoped retrieval and Alibaba API adapters; no new dependency.

**Spec:** `docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`, and `docs/kg-quality-35b-error-analysis-2026-11-02.md`.

## Constraints and review focus

- Preserve scope authentication, citation/source correspondence, immutable publication and current dirty workspace changes.
- Fast mode remains free of graph expansion and generative query/reranking calls. Production semantic merging uses the existing explicit deep `rerank` option.
- Retain the existing 50-candidate reranking cap, with candidates admitted from independent recall branches before text fusion can suppress graph discoveries. Final top-k and character budgets remain enforced.
- Reranking sees complete candidate passages; oversized model requests fail explicitly rather than silently slicing every passage. Provider window configuration must fit the bounded candidate request.
- A model can reorder known sources only; it cannot invent IDs, edit text, duplicate evidence or cross KG boundaries. A valid final semantic ranking must not be diluted by another round of rank voting.
- Evaluation keeps the eight semantic metric definitions, planned denominators and unknowns. Path metadata failure must not invalidate independently valid answer metrics. Missing one judge component must not erase the other.
- Model judgments need a bounded uniform review; disagreements remain unknown for the affected metrics. No regex interpretation of judge prose and no retry-to-pass loop.
- Graph extraction is source-driven, with open types/predicates, source-supported decomposition and aliases. No benchmark names, expected answers, fixed facet checklist or new local semantic heuristics.

## Task 1: Independent recall and authoritative source reranking

Files: `retrieval/fusion.py`, `retrieval/service.py`, `providers/rerank.py`; focused unit tests and `tests/integration/test_federated_search.py`.

- [x] Reproduce suppression of a useful graph candidate and dilution of a valid semantic ordering using synthetic evidence.
- [x] Add one scope-safe candidate-pool helper that interleaves independent branches before the existing 50-item cap; preserve deduplicated authenticated annotations.
- [x] Use the existing query provider to rank complete original passages with a shared general relevance/bridge prompt. Validate ordered subsets without duplicates and model-window limits.
- [x] Make a validated rerank order authoritative for the ranked candidates, retaining safe fallback for unavailable KGs.
- [x] Verify fast-mode isolation, cross-KG fairness, forged/duplicate indices and partial selections, complete passage preservation and permission revalidation with focused real-store tests.

## Task 2: General extraction and reader instructions

Files: `providers/semantics.py`, `eval/kg_quality.py`, corresponding extraction/evaluation tests.

- [x] Strengthen the existing initial/gleaning instructions to retain nested source-stated relations and supported alternate names, with no domain vocabulary or new schema.
- [x] Give both readers the same generic checks for target identity, relation direction, requested answer type, necessary citations and supported abstention.
- [x] Verify existing extraction trust boundaries, output limits and bounded gleaning remain intact; run an API smoke on public/synthetic sources.

## Task 3: Evaluation components and source-only benchmark replay

Files: `eval/kg_quality.py`, `eval/kg_quality_run.py`, focused unit tests; one small source-merge adapter if needed for readability.

- [x] Preserve correctness and support judgments independently, with path errors recorded separately and per-metric unknowns retained.
- [x] Add uniform bounded model review with reference isolation preserved; expose disagreements rather than select favorable scores.
- [x] Freeze a source-merge experiment option. Both arms use the same original-source reranker and reader; KG adds graph recall. Serialize no graph hints into reader evidence.
- [x] Reuse exact immutable corpus/questions/dense ranks; freeze new code, construction and requests in a new ignored run directory.
- [x] Run targeted regression checks, actual 35B API calibration/smoke and the fixed public evaluation when gates permit. Report failures and unknowns without altering old results.

## Delivery

- [x] Update the retrieval/evaluation contracts and write measured results, API use and remaining limitations.
- [x] Run focused Ruff/mypy/tests and `git diff --check`; inspect the complete task diff. No commits, publication or accepted-corpus migration are part of this task.

Verification so far: focused unit checks and strict mypy/Ruff passed; all 7 real-store federated retrieval integration tests passed. The frozen 35B calibration passed 12/12; full API evaluation is running. The unknown-score boundary regression is fixed. API integration also exposed an overly strict full-permutation contract; the shared ranker now accepts unique known ordered subsets and reuses production fallback order. Retrieval API/protocol errors remain unknown without aborting the matrix.

Delivered: fixed 23-question public 35B API run and eight metrics, graph statistics and source-integrity audit. Results remain provisional (`semantic_ready=false`); independent judge omissions/disagreements and specific reasoning errors are reported, with no quality-gain claim. See `docs/kg-source-merge-35b-2026-11-02.md`.
