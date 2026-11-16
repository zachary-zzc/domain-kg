# General LightRAG extraction implementation plan

> **For agentic workers:** Use executing-plans inline for this user-authorized implementation. Preserve unrelated workspace changes and frozen experiments; no commits or deployments.

**Goal:** Replace case-specific extraction/splitting heuristics with general, source-grounded model extraction and verify construction, retrieval and QA separately.

**Architecture:** Follow the pinned LightRAG 1.5.7 initial-extraction/one-gleaning pattern through our authorized provider adapter. Retain immutable evidence, open entity/relation vocabulary, typed output, scoped IDs and staged publication. Use one mature Unicode/token splitter for source subdivisions rather than language-specific delimiter lists.

**Tech Stack:** Existing Python/LightRAG/provider stack; pinned semantic-text-splitter 0.33.1 (no local inference).

**Spec:** docs/design.md, docs/contracts.md, docs/revisions-and-publication.md and the user's general-purpose extraction request.

## Global constraints

- All inference uses approved remote endpoints and the selected working model.
- Fixed semantic evaluation rubrics, questions, judge and vector/KG comparison remain unchanged.
- Namespace, source offsets, authorization checks and per-call usage accounting remain mandatory.
- Preserve accepted graphs and existing frozen evaluations; create a new run for changed code.

## Review focus

- Multilingual text, whitespace, combining characters and tiny limits: preserve exact coverage or fail explicitly.
- Tables and headings: retain source locations and intact rows whenever they fit.
- Invalid JSON/schema, refused output, timeout and revoked access: bounded repair only for model-format errors, no transport retry.
- Gleaning repeats facts or exceeds context: preserve initial valid facts without duplicate evidence or unbounded calls.
- Missing remote service: distinguish offline smoke proof from live semantic quality; retain unknowns and denominators.

### Task 1: Shared source subdivision

Files: ingestion/chunking.py, ingestion/output_splitting.py, providers/budget.py, dependency manifests; tests/unit/test_chunking.py and test_model_windows.py.

- [x] Add multilingual/structure/coverage regressions; observe current failure for an intact paragraph boundary.
- [x] Extend chunking.py with one library-backed span splitter; reuse in initial chunks, model fitting and output recovery. Remove the custom punctuation boundary list.
- [x] Run chunking, model-window and output-recovery tests; verify exact offsets, complete coverage, bounded tokens and progress.

### Task 2: General model extraction

Files: providers/extraction.py, semantics.py, compact.py, profiles.py, configuration.py; ingestion/models.py, extraction.py, pipeline.py; tests/unit/test_general_extraction.py.

- [x] Add a versioned general policy following LightRAG's initial extraction plus one context-bounded gleaning call, and one model-based format/evidence repair (shared second-call budget).
- [x] Use source-derived qualifier names, optional empty qualifiers and a generic prompt without domain/role dictionaries. Keep old explicit policies readable for frozen comparisons.
- [x] Validate all model output and union independently valid facts; record context-skipped gleaning explicitly. New configuration uses the general policy; stored profiles are not silently migrated.
- [x] Prove repair bounds, no authority changes, no repeated semantic calls on transport failure, evidence preservation and post-call revocation.

### Task 3: Smoke and evaluation

Files: eval/kg_quality_build.py, kg_quality_run.py, general smoke runner and tests; docs/providers.md and an experiment report.

- [x] Add the candidate policy to the existing frozen evaluation driver; keep metrics and judge unchanged.
- [x] Run offline end-to-end construction/retrieval smoke, focused unit tests, lint/type checks and dependency checks.
- [x] Run a small real Alibaba smoke, then paired semantic retrieval/QA evaluation if access works; stop on a confirmed billing/authentication block and save it without manufacturing scores.
- [x] Record upstream attribution, results, call costs/limits and unresolved limitations.

## Execution decisions

Implementation proceeds in this chat as requested. The user has already authorized the work and API evaluation, so no separate plan approval is needed. No new chats, subagents, commits or accepted-corpus migration are required.

Verification: 206 focused unit/evaluation tests, 23 distinct real-store integration cases,
Ruff and strict mypy passed. The real-store cases preceded the final one-line recovery
fix; its final regression passed in the focused suite. All four model preflights and
the shared judge calibration passed. Four-model construction and the frozen semantic
run finished, with unknown judgments retained (`semantic_ready=false`).

Final evidence: [optimization report](../../general-lightrag-optimization-2026-11-02.md).
The completed execution is not a semantic quality acceptance: the source smoke still
shows missing endpoint records/duplicate predicates/empty OCR extraction, the primary
judge has a confirmed false negative, and graph retrieval mostly changes path hints
rather than selected sources. No KG quality gain is claimed. Original scores and the
two-arm protocol remain unchanged; offline ablation and the assistant's source audit
are separate artifacts.
