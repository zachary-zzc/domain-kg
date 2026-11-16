# Sources workspace and bounded document recovery

## Intended behavior

Use one Sources workspace for published sources, processing jobs, issues and removed sources. Import files/folders/text and perform batch actions inline. A persisted, actor-bound import run identifies the current selection independently of task history and survives navigation. Completed sources contribute to the KG's shared publication. Existing material lifecycle, ACL, evidence and immutable publication rules continue to apply.

## Implementation and acceptance

- [x] Persist bounded import manifests and per-item commands before admission. Stable item keys recover response loss without duplicate jobs. Fresh receipt reads and final policy/source fences prevent stale or unauthorized metadata disclosure. Verify actor/namespace isolation, conflicts and replay with real storage.
- [x] Render the unified workspace and inline batch outcomes; preserve selected view/run across actions. Keep legacy links as redirects. Verify publication/processing/attention/removed views, current-run separation and batch context with console integration tests.
- [x] Poll items independently and preserve actual stages, including repair-required. Verify transient failures, resumed runs, duplicate submission prevention and batch selection reconciliation with browser JS tests.
- [x] Bound PDF rendering/native decoding and OCR lifetimes without raising the process-group memory cap. Reproduce with synthetic and existing failed files locally; preserve text/image provenance and surface partial-document warnings.
- [x] Improve actionable safe failure messages without exposing provider payloads or sending more private content. Verify diagnostics using stored failure classifications and mocked provider responses.
- [x] Run affected unit/protocol, browser JS, real-storage integration, Ruff and mypy checks; verify the running service and assets after a safe restart. Document measured behavior and remaining limits.

## Verification evidence

- 802 unit/protocol cases passed with native subprocess permissions; the initial sandboxed run could not execute 11 native parser cases, and the permitted rerun passed all cases.
- 37 browser JavaScript tests passed, including independent polling, saved-run recovery, selection reconciliation and context-preserving errors.
- Real-store gates passed for import-run replay/concurrency/ACL/CSRF, job filters/cursor bindings, inline batch outcomes, pending deletion/restoration, publication and console role/navigation. The combined final import/job gate passed 12 cases; console/Office/native parsing gate passed 16 cases.
- Ruff and strict mypy passed; wheel and source distribution built offline. Local service and static assets returned HTTP 200 after restart.
- Two previously failed private PDFs parsed below the unchanged 384 MiB process-group cap; see `docs/console.md` for counts and measurements.
- Chrome acceptance verifies desktop and 390px layouts (document width 390px), OIDC login, synthetic PDF/DOCX/TXT uploads, independent skips, run reload, batch retry/deletion/restoration, confirmation Cancel and shared graph preservation. The directory picker could not be populated through the automation API; a later manual selection successfully reviewed 76 files, including nested paths, as 16 eligible and 60 hidden/skipped with zero errors. Those originals were not uploaded. Browser publication runs used synthetic namespaces and mocked provider HTTP only.

No new paid provider requests, corpus retry or automatic publication was part of validation. Historical generic extraction failures remain until explicit retry; no exact cause is inferred from absent diagnostics.

## Browser follow-up fixes

- Condensed batch controls so source rows appear sooner; show a filename first and retain the full parent path and reviewed bulk label.
- Moved the import status outside collapsed input details; queued/ready DOM regressions prove it stays visible after navigation.
- Replaced blocking JavaScript confirmation with the platform HTML dialog. Explicit confirmation preserves reviewed revisions, names and fee/withdrawal explanations; Cancel/Esc do not submit, and a changed selection needs another review.
- Added isolated ingestion workers and retry fixtures behind the opt-in browser test flag, without changing production login/transport or contacting a real provider.

Final follow-up verification passed 14 focused Python checks, 37 browser JavaScript
checks, four real-store batch/workspace/job integration cases, Ruff, strict mypy
(289 source files), and the offline wheel/source build. The synthetic browser
preview stopped cleanly (one pytest case passed) and cleaned its test namespace.
The real local API and both workers restarted after confirming zero active jobs;
the authenticated Sources page loaded successfully afterward. The later native
directory review and paginated 46-file acceptance are recorded in
[import usability acceptance](2026-10-31-import-usability.md); these checks do not
establish real-provider success for the historical failed jobs.
