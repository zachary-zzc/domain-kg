# Model-free generation rebuild

Continue Task 18 against `docs/design.md`, `docs/contracts.md`, and
`docs/revisions-and-publication.md`. Execute natively and serially with the
existing four capped databases. No model requests, new image, or new service.

This first mode rebuilds indexes in a fresh generation with the current schema,
approved placement and embedding space. Schema/model changes require a separate
mode and verification. Never manufacture an ingest/curation job or old lease.

- [ ] Define a real owner generation command, bounded immutable target and exact
  publication/configuration/material snapshots. Reject inaccessible, stale,
  oversize or physically purged inputs before writes. Preserve retained hidden
  sources, manual directives and imported-unverified trust.
- [ ] Pin source ownership under the actual current owner job; block purge while
  the live pin exists. Read immutable source and manual artifacts under this
  narrow permit, with policy and snapshot checks before and after reading.
- [ ] Stage each original binding under isolated attempt/generation IDs, preserving
  artifact ancestry. Verify graph/vector/lexical receipts before a single fenced
  MongoDB manifest/configuration switch. Keep the old generation usable on any
  failure; accepted replay cannot advance the publication twice.
- [ ] Authenticate accepted rebuild ancestry for normal reads, subsequent repair,
  repeat rebuild, and backup. Do not give ordinary readers the private permit.
- [ ] Add typed HTTP/console owner controls and a model-free serial worker mode.
  Run tiny real-store failure/retry, permission, concurrency and mixed source /
  manual tests; run static checks; update status/page and commit the stage.

Limit: physically purged source bindings require explicit retirement semantics
and a separate history-preservation test. This mode refuses them rather than
dropping them or inventing evidence. Abandoned generations remain private until
the separately verified retained-generation collector can erase them.
