# Import usability implementation plan

**Goal:** Make a selected directory, eligible files, skipped files and the next action clear without a long unbounded table.

**Architecture:** Extend the existing Jinja/vanilla JavaScript workspace and cursor-backed lists. Keep the complete import plan independent of its displayed page; filtering never changes the submitted manifest. Existing policy, source identity, publication and model billing boundaries remain intact.

**Observed failure:** Chrome accepted the user's directory: 76 reviewed files, 16 eligible, 60 hidden/skipped, zero errors. All 76 rows appeared together, full paths wrapped repeatedly, and the checkbox column consumed 12rem. The native picker did not provide a useful selection summary. No originals were uploaded in this check.

- [x] Test and implement import list filtering/search/pagination in `folder-plan.js` and `folder.js`; default to included files, show skipped counts and optional details, retain individual errors. Pagination must not drop uploads or reveal a different run.
- [x] Replace the native picker's visible copy with a keyboard-accessible choose button and explicit directory/file count. Simplify `import_form.html`, show the main action before the list, display basename and parent path, and explain empty/all-skipped/over-limit states.
- [x] Correct column ownership and responsive spacing in `console.css`. Use existing service cursors for source/task list navigation and page-size controls; retain filter and revision bindings.
- [x] Run focused JS/rendering/real-store checks, then inspect desktop and 390px Chrome views using synthetic fixtures. Do not send the user's newly selected originals to a provider. Record native selection separately from model-backed publication.

Acceptance includes mixed valid/skipped/invalid rows, more than one list page, search on nested paths, all-skipped files, over 100 eligible files, saved-run reload, and current-page batch selection. No new dependencies or provider requests.

## Final verification

- 833 unit/protocol cases passed: 822 in the initial run and the remaining 11 native parser cases in a permitted rerun. The sandbox denied `/bin/ps`, which the parser's memory monitor requires. The combined unit/integration collection was split because both directories contain `test_import_runs.py`.
- 42 browser JavaScript cases passed, covering full-manifest pagination, skipped/error filters, nested-path search, all-skipped/over-limit input, unsafe per-file paths, response loss, upload counts and current-page batch selection.
- 13 real-store import/workspace/job cases passed, including publication during run reads, four-at-a-time item reads, actor isolation, late ACL/source changes, lightweight head rendering and retained batch outcomes.
- Ruff, strict mypy (289 source files), offline wheel/source build and `git diff --check` passed.
- Chrome reviewed 48 synthetic files as 46 eligible and two skipped. Upload was initiated from the final six-row page; all 46 files were admitted and reached `ready`. Reload restored that batch. Desktop width remained 1280px; the 390px viewport had a 390px document, with horizontal scrolling confined to the table.
- A final two-file preview used the latest head-first shell. Reload visibly showed only date/count and loading status before asynchronous details restored both `ready` rows, upload/finished counts of 2/2, the completed upload label and no pause button. The tab recorded no console errors. The preview stopped cleanly (one pytest case passed in 173.00s) and cleaned its synthetic namespace.
- The real local API and workers restarted after confirming zero active jobs. Existing OIDC login was restored; the actual Sources page rendered with 25-row pagination and no horizontal page overflow.

The previous 46-file run's full HTML timing (6.069s) used the older full-render controller and is not a measurement of the final lightweight shell. No paid provider call, private original upload or historical job retry was performed. These checks do not establish live-provider success for historical extraction failures.

## Status-read correction

Construction previously changed graph publication while progress reads were resolving whole-KG eligibility, causing `POLICY_CHANGED` and repeated expensive graph resolution during admission. Actor-owned import metadata now rechecks the exact admitted sources and current policy without pinning unrelated graph publication. Item reads are bounded to four at a time. Source withdrawal and revoked access still deny responses.

Saved-run HTML reads only an actor-bound run head and never waits for all job details. The existing authorized API loads rows after the page appears; failed refreshes retain a retry control, while access denial clears protected details. Source/evidence pages keep their graph publication fences.
