# Uncapped local usage and evidence failure investigation

- [x] Confirm that the user means removing local daily call and Token caps.
- [x] Add an explicit null policy at the existing reservation boundary; keep
      historical defaults, scoped ledgers, settlement and authorization.
- [x] Update usage/settings/maintenance views to represent no daily cap honestly.
- [x] Set both private local policy scopes to null and reload the service.
- [x] Inspect the reported task and sibling failures without resending source data.
- [x] Add safe, specific canonical evidence failure reasons and persistence/display.
- [x] Reproduce the reported extraction failure with the existing approved endpoint.
      The user explicitly approved at most three small requests; all three were used.
- [x] Fix the demonstrated cause with one bounded, reauthorized, separately accounted
      correction request and unchanged canonical source validation.
- [x] Verify the live corrected chunk, complete synthetic publication pipeline and
      model-context checks. All 14 existing source windows fit the selected profile.

Validation so far: six budget unit tests, 22 extraction/diagnostics unit tests,
nine real-store budget/settings/metrics tests, seven real-store task/console
tests and one maintenance/usage API test pass (17 integration checks total). Final native unit/protocol run: 677 passed. Browser module checks: 14 passed. Ruff, strict mypy across 250 source files and `git diff --check` passed.
Do not mark the unresolved extraction work complete from diagnostic improvements.

Chrome confirmed the current KG shows all three resources as unlimited. Screenshot:
`.cache/local-browser/unlimited-settings.png`. The user approved the limited replay; three diagnostic calls are exhausted. Existing failed jobs are preserved for explicit retry.


## Demonstrated cause and repair (2026-10-30)

The model returned six entities and four relations for the 133-character second
chunk. Two relations cited only one endpoint's passage; the other endpoint was in
a different source passage. No single passage contained both names. The failure
was correctly classified as missing relation endpoint evidence, rather than a
context overflow. All 14 persisted windows fit the configured chat (16384) and
embedding (8192) input bounds, with schema/output/framing included.

Extraction now permits one corrective request with the safe validation reason and
instructions to cite every supporting passage or omit unsupported claims. The
actual corrective prompt is checked against the context limit before transmission.
It rechecks authorization and reserves/settles its own usage. The corrected reply
must pass the original canonical validator. If correction still fails or cannot
fit, the source remains unpublished. Output-ceiling splitting retains its existing
bounded two-level behavior.

The approved live correction passed with six entities and three validated
relations. The first of the three diagnostic requests selected the already-good
first chunk; the second reproduced the second-chunk failure; the third validated
its correction. No full-file or batch retry was performed under this three-call
approval. Original jobs and completed chunk checkpoints remain available.

A real-store synthetic regression confirms two separately charged calls, zero
outstanding reservations, reuse of the validated checkpoint, no premature
publication, and successful verified publication after repair. No validator was
relaxed, and no original document or private provider response entered Git.


Final proof: model-window/checkpoint/budget/transport files passed 11 checks,
including the existing budget cases; the new complete correction/publication
regression passed separately. Together with the earlier 17 focused integration
checks, these cover 24 distinct integration cases. Ruff and strict mypy across
250 source files passed after the final full-format correction guard. Local
API/worker restart applies the final implementation without resubmitting sources.
