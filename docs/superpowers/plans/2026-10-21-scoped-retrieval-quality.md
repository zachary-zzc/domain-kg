# Scoped retrieval quality implementation plan

> Execute natively in this chat under the user's authorization. Use failing
> behavior tests and verification before completion. No delegation or new model calls.

**Goal:** Improve the accepted service's evidence retrieval and evaluate strong
RAG, relation-guided graph retrieval, and explicit supported paths on saved data.

**Architecture:** Share pure lexical scoring, graph relevance/path ranking, and
evidence packing between the scoped service and an offline adapter. Stores still
establish eligibility; ranking never establishes authority. The adapter supplies
saved embeddings/extractions instead of providers. Semantic evidence review is
separate from reference-source coverage and from final answer evaluation.

**Tech Stack:** Existing Python, Pydantic, pytest and local storage smoke harness.

**Spec:** User request; docs/design.md, docs/contracts.md,
docs/revisions-and-publication.md; implementation tasks 11–13.

## Constraints and review focus

- No paid or local-model inference, no new extraction, no production publication.
- Preserve original experimental records and previous semantic judgments.
- Work in the existing checkout because its untracked evaluation inputs are
  required; leave unrelated changes intact. Do not commit or migrate them.
- Scope, visible support, identity witnesses and final revalidation remain mandatory.
- Fast remains vector/text only, with no query LLM or reranker.
- Graph semantics are retrieval hints, not proof of truth or entity identity.
- Rank only visible candidates. UUID is a final deterministic tie breaker.
- Keep complete source passages whenever they fit; deduplicate and backfill from
  the available pool. Report truncation instead of silently claiming full evidence.
- Do not use answers, gold supports, dataset labels or semantic judgments to rank.
- Reference support-ID coverage is diagnostic, never answer exact match or
  semantic correctness. Equivalent evidence may be valid; polarity, entity,
  direction and time errors are still substantive errors.
- The path arm uses the same selected evidence as the relation arm, with path
  presentation counted separately. No new answer accuracy claim without answers.
- Review empty graphs, hidden aliases/bridges, negation, missing bridge evidence,
  namespace collisions, tight budgets and oversized passages.

## Task 1: Shared ranking and evidence selection

Files: retrieval/ranking.py, lexical.py, graph.py, deep.py, fusion.py;
tests/unit/test_retrieval_ranking.py, test_fusion.py.

- [x] Reproduce UUID-first ranking and destructive passage allocation with failing tests.
- [x] Extract reusable scoped BM25, rank entity seeds and graph evidence using
  source relevance and supported predicate/path cues; preserve bounded traversal.
- [x] Share path construction and source-preserving deduplicated packing.
- [x] Verify focused tests, Ruff and typing.

## Task 2: Unified offline comparison

Files: eval/scoped_retrieval.py, eval/scoped_retrieval_run.py,
tests/unit/test_scoped_retrieval.py; private .cache/scoped-retrieval-2026-10-21/.

- [x] Test the adapter uses production ranking/fusion/selection, keeps scopes
  distinct, has no model clients, and preserves relation/path arm evidence identity.
- [x] Replay saved corpus, vector ranks and graphs for all five construction
  models. Compare V12 reference, strong vector+BM25 RAG, relation-guided graph
  augmentation, and that same evidence with source-supported path presentation.
- [x] Report fixed 205 reviewed questions and disjoint unanswered questions
  separately. Use native semantic review of a deterministic bounded sample to
  assess evidence relevance/sufficiency and relation faithfulness, not EM.
- [x] Hash inputs, deterministic replay, count zero new provider calls.

Experimental distinction: keep production soft relation ranking as `GUIDED`.
Evaluate `REL` separately with named-query-anchor and terminal-predicate
constraints; abstain if this bounded lexical planner cannot resolve them.
Re-trace paths from query anchors so vector seeds do not erase the explanation
chain. Keep conservative fusion fixed across these arms. Record that this can
also limit promotion of graph-supported sources already in the text pool.

## Task 3: Service verification and report

Files: tests/integration/test_graph_retrieval.py, docs/retrieval-quality-report.md,
docs/retrieval-branches.md.

- [x] Run real MongoDB replica-set, Neo4j, Qdrant and S3 checks through the
  existing resource-limited smoke harness; no model service.
- [x] Run relevant unit and non-storage regressions; review the final diff.
- [x] Document measured results, graph correctness limitations, semantic review
  examples and the unmeasured effect of path presentation on generated answers.

Verification: `.cache/scoped-retrieval-2026-10-21-v5/verification.json`.
536 non-storage tests, 84 task checks including 47 storage-marked cases,
7 fusion integration reruns and 1 final optional-reranking integration rerun
passed. Ruff and strict source typing passed. v4's independent replay was
byte-identical; final v5 core results match both v4 processes, with final code
hashes recorded separately. Started storage services were stopped; volumes kept.
