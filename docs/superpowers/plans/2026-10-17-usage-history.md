# Owner-scoped usage history

Continue Task 18 group 5 natively against the design, contracts and publication
protocol. Keep the current preview available while preparing isolated modules;
do not edit its imported runtime or run another storage test until it stops.

- [x] Define a bounded UTC date window (1–31 days) and history response. Reject
  future dates, duplicates, foreign namespaces and malformed counters. Return
  only recorded days; absent records do not prove zero usage. Keep historical
  counts separate from today's configurable limits.
- [x] Read only the exact org/KG `scope=kg` daily ledger, with current owner
  authorization before and after retrieval. Pending holds remain reserved;
  uncertain settled calls retain their conservative upper bound. Do not release
  reservations or call any provider when reading history.
- [x] Add the thin HTTP route and a small operations-page history table. Verify
  current-owner access, reader/editor denials, changed permission, window bounds,
  other-org/KG isolation, and conservative counts using one tiny real-store
  fixture; run static checks and commit with the progress update.

This is service provider usage, not the Codex account's token consumption or a
price estimate. No endpoint, profile secret, raw request, source/job identifier or
organization-wide aggregate is exposed.
