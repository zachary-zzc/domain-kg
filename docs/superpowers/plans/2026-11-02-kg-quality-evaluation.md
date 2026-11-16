# KG quality evaluation implementation plan

**Goal:** Freeze a reproducible evaluation of evidence retrieval, the incremental QA
benefit of KG, and descriptive graph structure, without changing production algorithms.

**Architecture:** Versioned evaluation modules reuse `OfflineIndex`, production
fusion, `scale_retrieval.pack`, and the immutable Alibaba call runner. Historical
scores and scripts remain reproducible and are not reinterpreted as v2 results.

**Spec:** `docs/kg-quality-evaluation.md`. Python 3.12, existing Pydantic/NumPy/pytest.
Implementation is native in this chat, as requested by repository instructions.

## Acceptance and limits

- Question review precedes model output inspection. Preserve original wording,
  provenance, edits and exclusions. Reject pending reviews and stale query ranks.
- Four paired arms: text, KG sources, same KG sources plus paths, reference-only
  reader control. Same reader settings, corpus and maximum evidence budget.
- Retrieval reference metrics and reference-free semantic sufficiency are separate.
  Reader/gold answers never enter evidence-only judging.
- QA correctness, grounding, citations, abstention and protocol failures are separate.
  Missing/invalid judge calls remain unknown; report coverage and bounds.
- Structural analysis includes isolated observations, components, degree, type
  distributions, source coverage and construction failures. Never infer truth from density.
- New paid calls are not part of this implementation verification. No production
  publication, algorithm tuning, dependency addition, or historical artifact mutation.

## Tasks

- [x] Add regression tests for stale ranks, review gates, blinded prompts,
  semantic alternatives, invalid evaluator output, missing pairs and graph isolates.
- [x] Implement protocol and metrics in `eval/kg_quality.py`; use strict models at
  review/judgment boundaries and explicit denominators in aggregate reports.
- [x] Implement construction replay and structural analysis in `eval/kg_quality_graph.py`.
  Preserve complete-document failure semantics and source-scoped identities.
- [x] Implement CLI in `eval/kg_quality_run.py`: draft/freeze, rank, retrieve,
  calibrate, answer/judge, report. Reuse immutable call/checkpoint infrastructure.
- [x] Provide synthetic calibration cases and end-to-end fixture tests. Schema and
  fixture success do not establish real-world semantic evaluator accuracy.
- [x] Run focused pytest and Ruff, replay saved graphs locally, and document commands,
  metric definitions, actual proof and remaining corpus-review/calibration work.

## Review focus

Preserve isolates absent from relation endpoints; do not count unknown scores as
wrong; enforce identical sources in the path ablation; avoid gold contamination;
reject stale embeddings after question edits; retain failed extractions in corpus
denominators; do not silently relabel reused development questions as holdout.
