# Bulk management implementation plan

> **Execution:** Native execution in this chat, following the repository's explicit instruction. No delegation, deployment or corpus migration.

**Goal:** Select multiple sources or jobs and delete, restore, process, retry or cancel them with independent, durable results; verify a KG combines accepted sources.

**Architecture:** A typed bounded batch coordinator calls the existing material lifecycle, upload/replacement and job queue services. Batch identities and item receipts live in MongoDB; source originals and accepted projections retain their existing ownership. Thin console routes render results, while a small shared selection script preserves selection across list refreshes.

**Tech stack:** Existing Python/FastAPI/Pydantic/MongoDB services, Jinja templates and native JavaScript. No new dependency.

**Spec:** User request; docs/contracts.md; docs/revisions-and-publication.md; console task 17 and lifecycle task 14 in docs/superpowers/plans/2026-10-16-domain-kg.md.

## Constraints and review focus

- Batch size 1–100, distinct UUID targets, current same-KG authorization and expected source revisions; no actor or storage paths from form data.
- Preserve single-item permission, idempotency, retention and publication checks. A failed item must not stop other items.
- Retries with the same batch key must not duplicate work; changed input must conflict. Recheck access before returning cached results.
- Process pending sources by resuming their existing jobs. Reprocess accepted sources through a new version of the preserved original, retaining metadata and the accepted view until readiness.
- Support whole-page selection, clear selection and retryable-only selection. Polling must not erase selection. Do not label submitted work as published.
- Verify two accepted materials share one KG view and canonical identities where grounded identifiers agree; removing one must preserve the other's support. Equal names alone must not force identity merges.

## Task 1: Typed batch orchestration

Files: operations/batch.py; transport/runtime.py; materials/models.py and service.py for replacement revision guard; tests/integration/test_batch_operations.py.

- [x] Add failing real-store tests for independent retries, replay, changed-input conflicts, source permission isolation, revision conflicts and reprocessing publication boundaries.
- [x] Implement BatchCommand/BatchTarget/BatchResult and BatchOperations.execute over existing services, with durable per-item results.
- [x] Run focused batch tests and existing lifecycle, queue and publication regressions.

## Task 2: Browser management controls

Files: console/batch.py, routes.py, workspace.py, jobs.py; source/job templates; static/batch.js and locales; tests/integration/test_console_batch.py and browser selection tests.

- [x] Add failing route tests for CSRF, readers, cross-KG targets, selections and partial-result rendering.
- [x] Add selection controls to published, pending, removed and job lists, with operation-specific review text and per-item result/job links.
- [x] Verify selection persistence, safe result rendering and real Chrome controls without modifying user sources.

## Task 3: Shared graph proof and delivery

- [x] Publish two synthetic materials into one KG, verify shared entity/assertion IDs and independently owned supports, then batch-delete one and verify remaining evidence.
- [x] Document batch behavior and KG identity limitations; run unit/protocol, focused real-store and frontend checks, Ruff, mypy and diff checks.
- [x] Reload local services and verify browser controls; do not submit or retry the user's existing corpus.

## Verification

- 695 unit/protocol checks passed; Ruff, strict mypy (252 source files), JavaScript syntax and diff checks passed.
- Six new real-store batch cases passed: shared graph/delete/restore, versioned reprocessing, concurrency/isolation, interrupted submission, metadata race and browser form authorization.
- Sixteen existing real-store submission, incremental-change, job/console and material-management cases passed.
- Fourteen existing frontend logic checks passed. Chrome verified source and pending-source controls, 11 retryable task selections surviving polling, and selection clearing; no user task was submitted, retried or deleted.
- Local API and workers reloaded successfully. Screenshot: .cache/local-browser/batch-controls.png (ignored).
- Identity limitation confirmed: compact extraction profiles do not emit entity identifiers; automatic name-only cross-document merges were deliberately not introduced. The unified-KG proof uses grounded shared identifiers and independent evidence ownership.
