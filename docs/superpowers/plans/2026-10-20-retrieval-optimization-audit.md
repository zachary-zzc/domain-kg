# Retrieval optimization audit implementation plan

> **For agentic workers:** Execute natively in this chat, as explicitly required by AGENTS.md. Steps remain unchecked until verification succeeds.

**Goal:** Explain the limited dual-path gains from frozen construction, retrieval and semantic-review records; measure small offline retrieval variants without generating new answers.

**Architecture:** A supplemental evaluator traces the frozen lightweight GraphIndex and compares its evidence with the 205-question common eligible cohort. Original graph/model responses and prior reports remain immutable. Platform source inspection is separate from benchmark measurements.

**Tech Stack:** Existing Python/JSON and native source review; one process, no new dependencies, cloud inference, local LLM, DB startup, publication or production mutation.

**Spec:** User request to inspect optimization space; docs/design.md, docs/contracts.md, docs/revisions-and-publication.md and implementation tasks 11–13 establish scope and visibility constraints. Existing semantic-review eligibility is frozen.

## Constraints and review focus

- Do not edit frozen eval/dual_path_*.py or overwrite prior results.
- Main evidence/answer comparisons use exactly the same 205 eligible questions across all five models and four saved conditions; errors remain outcomes.
- Distinguish evidence absent from construction, reachable but outranked, packed out, present but uncited, and present but answered incorrectly. Gold support coverage is a diagnostic, not factual entailment.
- Trace the exact original seed/traversal/order/cap behavior and verify replay against all 20,000 frozen retrieval plans.
- Graph equality, numeric/generic hubs and hash tie-breaking cannot be treated as resolved entity identity or relation-aware inference.
- Any new ranking consumes only question/source text, graph facts and saved vector ranks; gold labels never enter selectors.
- Retain V6 and enforce 12 documents / 12,000 serialized source characters for every variant. Preserve complete source units and original IDs.
- Fix at most three variants before inspecting results; do not enter an unbounded tuning loop. User's five unsuccessful attempt cap remains a hard maximum.
- No new answer accuracy or citation-completeness claim for changed contexts without fresh answering. Report retrieval evidence coverage only.
- The 584 questions with no saved formal answers may receive a secondary, disjoint-question reference-coverage check; their original question/gold quality has not been reviewed. No generalization or significance claim.

## Task 1: Reproduce and trace

Files: eval/retrieval_audit.py; tests/unit/test_retrieval_audit.py; private retrieval-audit/ artifacts.

- [x] Write and run failing behavioral tests for graph trace/tie diagnosis, source-preserving capped backfill and duplicate-aware candidate fusion.
- [x] Implement minimal trace and selectors; verify tests and exact frozen baseline replay.
- [x] Audit construction coverage, batch-position loss and parser rejection reasons without relaxing grounding checks.

## Task 2: Predeclared offline contrasts

All three retain V6 and use the frozen graph facts and 12-item vector ranks:

1. **fill:** original U12, then fill remaining admissible slots from V7–V12.
2. **tie_fill:** same seed/traversal and graph scores; break equal graph-score ties by source-text BM25 against the question, then apply fill. Compare with fill to isolate tie ordering.
3. **pool_rrf:** protect V6; fuse vector tail and graph top-24 candidates using rank-only RRF (constant 60), deduplicate and pack under the same limits. No answer/gold-based filter.

- [x] Run all three contrasts once on the fixed 205 cohort; record per-question document provenance, support coverage and budget compliance.
- [x] Run the same frozen variants on unanswered questions, reporting original reference coverage separately.
- [x] Decompose saved U12/V12 wins/losses and original citation gaps; inspect concrete changed examples.

## Task 3: Report and verify

- [x] Document benchmark versus platform differences, measured bottlenecks, prioritized next changes and remaining unknowns.
- [x] Verify original input/answer/review hashes and API checkpoint count unchanged, replay deterministically, run relevant tests and Ruff.
- [x] Deliver a concise Chinese summary and a source-linked report. Do not claim answer accuracy improved from retrieval-only replay.

## Verified outcome

20,000 original retrieval plans replayed exactly. Three predeclared retrieval-only variants produced 11,835 source-preserving budget-compliant records on 205 common quality-reviewed questions and 584 completely unanswered questions (470 original positives). The two partial-tail questions are excluded from the secondary cohort. Two independent processes produced byte-identical outputs. All 8,299 saved formal responses and their prior scores match the semantic inventory; 4,690 graph requests and accepted parse results revalidated. Original inputs, reports, production source and dependency files remain unchanged.

Seven new behavioral tests failed before implementation and passed afterwards. Targeted tests: 29 passed. Non-storage regression: 516 passed, 364 deselected. Ruff passed. No new inference/model/database work or accepted-projection changes occurred. Current variants improve some primary reference coverage but do not exceed V12 on unanswered-question coverage; no new answer-accuracy claim.
