# Bounded 4B hybrid retrieval pilot implementation plan

> Execute inline in the current chat, per the user's repository instructions.
> Use executing-plans, test-driven-development and verification-before-completion.

**Goal:** Measure whether a lightweight retrieval graph improves a fixed dense/lexical
baseline with the cached 4B model, and whether reviewed model relations add value.

**Architecture:** An offline, nonpublishing pilot shares source chunks, embeddings,
queries and context limits across A (dense + BM25), B (A + deterministic lexical /
document-structure graph), and C (B + source-grounded, author-reviewed 4B relations).
This is an algorithm pilot, not a reproduction of LinearRAG or a change to accepted
projection/publication semantics. The source PDF and generated indexes stay ignored.

**Tech Stack:** Existing Python environment, numpy, httpx, owned llama.cpp processes;
cached Qwen3-4B Q4_K_M and Qwen3-Embedding-0.6B Q8_0, sequentially.

**Spec:** User's 19 October request and preceding dual-path discussion;
`docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`, Task 9 in
`docs/superpowers/plans/2026-10-16-domain-kg.md`.

## Global Constraints

- No downloads, external APIs, database writes, accepted KG publication or production changes.
- Freeze cases, source hashes, code hashes and configuration before inference. Do not tune on test results.
- At most five failed diagnostic/change/retest strategies; this new authorized pilot has its own ledger.
- At most 48 chat calls and 1,800 model-call seconds; stop after two consecutive timeouts.
- Two CPU threads, no GPU, one resident model; chat RSS 3.5 GB, embedding RSS 1.25 GB.
- Same top-3 and 1,800-character original-evidence budget for every arm. Graph expansions
  are bounded to two chunk steps. No summaries or extracted claims in answer context.
- Agent-authored reference annotations and relation review are provisional, not independent
  clinical/human annotation. Keep public-source and synthetic results separate.
- Graph construction consumes source text only, never questions, answers or relevance labels.
- Every answer is scored, including timeouts/truncations; no-answer retrieval has no recall denominator.
- Never silently reuse a prior run or claim a pending/unreviewed relation is verified.

## Review Focus

- Wrong direction or negation: span correspondence alone cannot approve a semantic edge.
- Retrieval ceilings: report graph regressions and equal-context cases, including zero improvement.
- Failed calls: preserve denominator, error type, actual timing and resource measurements.
- Leakage: question/answer fields must never enter graph construction or indexing text.
- Scope: in-memory quality results do not establish real-store lifecycle or permission correctness.

## Task 1: Frozen corpus, retrieval and scoring

**Files:** `eval/hybrid_cases.json`, `eval/hybrid_pilot.py`, `tests/unit/test_hybrid_pilot.py`.
**Interfaces:** `load_corpus(parent, config)`, `build_graph(chunks, facts=())`,
`retrieve(chunks, embeddings, query_vector, query, arm, graph)`, `assess(...)`.

- [x] Write and observe failing tests for grounded quotes, citation scope, no-answer,
  graph bounds, exact identifiers, gold-free construction and failed-call denominators.
- [x] Implement deterministic BM25/RRF and bounded seeded graph diffusion. Keep lexical
  graph links separate from reviewed positive semantic edges.
- [x] Verify unit tests and freeze source/query/code hashes before inference.

## Task 2: Owned real-model runner

**Files:** `eval/hybrid_run.py`, `scripts/native_model.py`, runtime unit tests.
**Interfaces:** explicit `prepare`, `extract`, `evaluate` stages; persisted immutable
inputs, calls, review decisions, retrievals, answers, resource and metric reports.

- [x] Test that runner caps/timeout handling and failed answers cannot report success.
- [x] Allow existing owned native lifecycle to write logs inside the pilot directory.
- [x] Prepare embeddings; extract relations for all selected passages using 4B;
  independently inspect every candidate and seal an explicit agent-review file.
- [x] Evaluate all three arms with identical answer prompt/model/seed/context budget;
  cache only byte-identical requests and disclose logical versus actual call counts.
- [x] Stop owned processes and verify actual final process state.

## Task 3: Evidence and delivery

**Files:** `docs/hybrid-retrieval-report.md`; relevant status notes only if warranted.

- [x] Report retrieval recall, complete support, answer match and citation checks,
  split by development / held-out public / held-out synthetic / no-answer.
- [x] Record cold build costs, model usage, sampled RSS, regressions and limitations.
- [x] Run unit/protocol regressions, Ruff, mypy for production modules, diff checks;
  review the whole experiment inline. Leave Task 9 acceptance unchecked.

## Execution decisions

Work stays on the existing `codex/domain-kg-planning` branch and uses a fresh ignored
pilot directory. No worktree, agent delegation, installation, deployment or migration
is needed for this isolated evaluation. The old five-attempt extraction ledger remains
closed and unchanged; the user explicitly authorized this different experiment.

## Verified outcome

Completed 19 October 2026. The initial fixed fusion added no evidence. The fifth
strategy used name anchors and explicit two-step paths; its exploratory results
were 10/10 answer matches versus 8/10 for A, but complete-support answers remained
8/10 for every arm. Five strategies have ended unsuccessfully under the strict
citation criterion and the sixth-strategy guard was exercised without inference.

Execution refinements: inherited embedding allocation failed twice plus one
reproduction; source-complete <=24-token pooling and 16-token chat prefill stayed
within existing limits. v5 follows observed rankings and is explicitly exploratory.
A unit-attached numeric scoring defect was corrected uniformly in a separate
postprocessor; original scores and all failures remain preserved. No source/model
outputs were edited. The prior extraction experiment remains closed.

Verification: 410 unit/protocol tests passed in 9.85 s; whole-repository Ruff,
strict mypy (239 production modules), source-tree and diff checks passed. Owned
PIDs exited and ports 29500/29501 were closed. Full results and limitations are in
`docs/hybrid-retrieval-report.md` and ignored `.cache/hybrid-pilot/summary.json`.
