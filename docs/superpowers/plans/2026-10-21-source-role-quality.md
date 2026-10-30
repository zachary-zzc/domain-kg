# Source-role extraction quality implementation plan

> Execute natively in this chat using executing-plans. User authorized continued work and API use; no delegation, publication or corpus migration.

**Goal:** Improve evidence-grounded KG assertions and useful bridge coverage, judging semantic consistency rather than answer exact match.

**Architecture:** Share explicit relation-role instructions between an opt-in production extraction policy and a bounded public-source experiment. Keep canonical span/schema validation, immutable accepted graphs, and manual curation unchanged. Compare fresh baseline, role-aware extraction, and role-aware wider coverage with a source-only semantic judge; never feed QA answers to construction.

**Tech stack:** Existing Python, Pydantic, httpx, pytest, frozen public corpus and approved DashScope endpoint.

**Spec:** docs/design.md, docs/contracts.md, docs/revisions-and-publication.md; original implementation Tasks 9 and 19; docs/retrieval-quality-report.md.

## Constraints and review focus

- Existing provider configurations retain legacy behavior and cache identity; opt-in policy changes cache identity.
- Exact source quotation validates provenance only. Direction, role, temporal/conditional scope and negation need semantic assessment.
- Accept equivalent terminology and justified source-local coreference. Do not use QA exact match or require identical edge strings.
- Extractors see source text only. Separate previously examined development documents from hash-selected held-out documents.
- API run: at most 100 attempts, RMB 8 conservative reservation, 4,096 extraction / 6,144 judge output tokens; no automatic retries. Checkpoint before dispatch, protect secrets, never repeat ambiguous calls.
- Model verdicts are provisional evaluation data, never human curation or accepted publication.

## Task 1: Opt-in source-role policy

- [x] Reproduce policy propagation/cache separation failures in tests/unit/test_extraction_semantics.py.
- [x] Add providers/semantics.py, a legacy/source_roles_v1 profile setting, and shared instructions in full and compact extraction payloads. Preserve legacy cache serialization in ingestion/pipeline.py.
- [x] Verify all extraction formats, unsupported predicate restraint, qualifier instructions, immutable legacy schema, and cache separation with unit tests.

## Task 2: Bounded source-only comparison

- [x] Test strict fact parsing, quote provenance, semantic judgment coverage, source-only requests, output limits, checkpoint/resume and budget stops in tests/unit/test_source_role_eval.py and tests/unit/test_source_role_run.py.
- [x] Implement eval/source_role_eval.py and eval/source_role_run.py: frozen selection, equal-budget baseline/roles, larger-coverage arm, blinded fact IDs, source-grounded factual precision and semantic coverage judgments.
- [x] Prepare frozen local artifacts: 8 development and 12 held-out sources, excluding 3,155 previously reviewed sources.
- [ ] Run API comparison when credentials are available; retain incomplete/error attempts in denominators and audit a fixed subset of verdicts. Current attempts: 0; pending private credential.

## Task 3: Verify and report

- [x] Run relevant tests, Ruff and strict source mypy; perform native diff review and address concrete failures. 58 local tests and 7 real-store tests pass; 241 source files pass mypy. Verified budget rejection for a 4K full-format policy and recovery with a supported 8K fixture profile.
- [x] Record measured results, credential or runtime limitations, total usage, and promotion decision in docs/source-role-quality-report.md. API attempts and spend remain zero; default policy and Task 9 quality gate remain unchanged.
