# Folder import recovery implementation plan

> Execute natively in this chat with `executing-plans`; the repository's native-execution instruction takes precedence over skill delegation and approval defaults.

**Goal:** A bad file cannot stall unrelated files; users can understand progress, diagnose failures and recover accepted jobs after leaving the page.

**Architecture:** Reuse authoritative durable jobs, material commands and current-policy inspection. The browser reviews and uploads files independently, then monitors accepted jobs without holding subsequent uploads behind parsing. No private filenames or credentials enter browser persistence.

**Tech stack:** Existing Python/FastAPI/MongoDB, Jinja2 and native JavaScript; no new dependencies.

**Spec:** User's Chrome folder-import report; `docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`, implementation tasks 7, 8 and 17.

## Global constraints

- Preserve current organization/KG/source authorization before and after reads.
- Preserve immutable originals, expected versions, idempotency and publication fences.
- Keep model budgets unchanged; do not retry failed paid work automatically.
- Keep existing uncommitted changes; no corpus migration or deployment.

## Review focus

- Empty/unsupported/oversized/unreadable file beside valid files: show its own result and continue.
- Lost upload/submission response: replay stable keys; never create another accepted material.
- Session/policy failure: stop submissions and hide stale source metadata.
- Leaving/reloading: accepted jobs remain named and discoverable; unuploaded bytes require reselection.
- Quota failure: explain recovery; never report a failed or merely queued file as published.

### Task 1: Named, diagnosable durable jobs

Files: `jobs/models.py`, `jobs/queue.py`, `jobs/runner.py`, `jobs/inspection.py`, HTTP job-list route, job templates, locale catalogue.

Interfaces: extend `JobReceipt` with validated source identity, creation time and safe structured failure; `JobInspection.list` returns newest first with bound opaque cursors.

- [x] Write and run failing integration tests for source names, failure diagnostics, newest-first pagination and denied/corrupt references.
- [x] Enrich only authorized receipts; retain safe error codes and correlation references, never raw exceptions; mark a stage before executing it.
- [x] Render names, percentage/stage, actionable errors and automatic refresh; expose the existing inspection service through HTTP.
- [x] Run real-store job and console regressions, Ruff and mypy.

### Task 2: Independent file review, submission and monitoring

Files: `console/static/folder-plan.js`, `folder.js`, `templates/folder.html`, folder route, source navigation and CSS; `tests/browser/`.

Interfaces: pure review/submission functions call existing typed upload/version/job APIs. Review matches incomplete jobs by exact source/checksum; retry requires an explicit user action.

- [x] Write and run failing Node regressions for mixed files, unrelated failed jobs, continuation after one failure, cancellation, lost responses and existing-job reuse.
- [x] Replace batch-wide failure with per-file outcomes; show checked/uploaded/finished counts, file sizes and errors in the selected language.
- [x] Separate uploads from background processing; poll stages independently; add a named recent-jobs recovery panel and explicit resume/retry controls.
- [ ] Run focused Node and Python regressions; verify Chinese/English and mobile layout in the current Chrome; document actual results.

## Execution record

Initial evidence: the Chrome failure displays only `QUOTA EXCEEDED`; folder state is memory-only, both loops abort on their first error, and a global pending-material check blocks unrelated sources. Existing safe error policy prohibits publishing raw worker/provider text.

Verification: 655 unit/protocol checks passed; 37 distinct affected real-store checks passed across the focused integration runs; 12 Node workflow checks passed. Ruff, strict mypy (245 source files), JavaScript syntax and diff whitespace checks passed. The parser snapshot test initially failed in the outer sandbox and passed when the native subprocess could execute; the final complete unit/protocol run passed. Real-store Qdrant checks emit the existing local HTTP API-key warning.

Chrome confirmed Chinese and English pages, source-named failure diagnostics, and recovery of the original failed Downloads job after a real OIDC login and page navigation. No uncaught page errors were recorded. Screenshot: `.cache/local-browser/import-recovery.png`. The mixed-file chooser acceptance remains unverified in Chrome: extension file-URL permission blocked automation and the native picker fallback timed out. Its file planning and independent submission behavior is covered by Node regressions; no paid extraction was started.

Scope ruling: native implementation and author self-review preserve the repository's explicit no-delegation rule and existing uncommitted work. No commits, deployment, browser-security changes or budget increases were performed. The remaining unchecked step is the blocked browser file-selection acceptance (mobile was not separately exercised).
