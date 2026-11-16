# Parser settings implementation plan

**Goal:** Default every document format to 1024 MiB and expose bounded operator-owned parsing controls in Advanced settings.

**Architecture:** Extend `ParseOptions` with the process-group RSS budget. A server-selected JSON file stores an atomic, revision-checked configuration; browser/MCP callers cannot supply paths. The job runner captures it at the start of each job execution and builds one immutable pipeline for that execution. Existing namespace, ACL, evidence and publication checks remain unchanged.

**Scope:** Memory 64–8192 MiB, runtime 1–300 seconds, pages/slides 1–500, expanded content 1–100 MiB and OCR. The existing chunk/model window configuration remains separate. The file is global to the server; only a current human platform operator who owns the displayed KG may save. No new dependencies, paid model calls or automatic corpus retry.

- [x] Add failing tests for all-format defaults, custom memory enforcement, malformed/out-of-range configuration and stale/concurrent saves.
- [x] Extend parser options and Linux address-space enforcement; use one budget for parent/children across formats.
- [x] Add bounded atomic file loading/saving and operator-authorized advanced form; verify CSRF, field validation, permissions and no model calls. Expose the same service through typed MCP tools.
- [x] Load configuration at each job boundary, keep it fixed while that job runs and bind it through existing checkpoint hashes. Prove two successive jobs observe the change.
- [x] Run focused parser/configuration/console/worker checks, update documentation, restart only when idle, and verify the live form and saved defaults in Chrome.

**Stop condition:** All formats use the same 1 GiB default; an authorized user can save and reload parsing settings, the next job consumes them, invalid/stale/unauthorized saves preserve the accepted file, and the actual local settings page is open.

## Verification evidence

- The full unit/protocol run passed 846 cases and exposed one snapshot test that combined the old committed worker/schema with current `ParseOptions`. Its fixture now commits a coherent copy of the current source to a disposable test repository before freezing it. The corrected snapshot test passed, preserving source binding without a production compatibility workaround.
- 29 focused PDF/OCR, snapshot, diagnostics and translation checks passed after the page-limit diagnostic change. Another 26 configuration, task-snapshot, diagnostics, translation and MCP schema checks passed after the final persistent-default-save fix. The snapshot check passed again after making fixture subprocess work asynchronous.
- 17 real-store integration cases passed for browser/MCP parser configuration, existing model settings and native PDF/Office parser behavior. They cover invalid/stale saves, CSRF, reader/editor denial, memory/runtime termination and no real model requests.
- Ruff and strict mypy (290 source files) passed; wheel/source archives built offline. `git diff --check` passed.
- Confirmed zero active user jobs, restarted the local API/workers, opened Advanced settings and saved the defaults through Chrome. Reload retained revision 1, 1024 MiB, 120 seconds, 500 pages/slides, 100 MiB expanded content and enabled OCR. The private JSON file was created with mode 0600. Saving did not start an import, retry or provider request.

The runtime gate ran on macOS. Linux address-space enforcement derives from the same validated options but was not exercised on a Linux host in this change. Any deployment's container/host cap still applies independently of the configured parser budget.
