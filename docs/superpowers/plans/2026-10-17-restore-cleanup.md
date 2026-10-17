# Cleanup of inactive restore targets

Expand operations sections 3/5. Execute natively, serially, with synthetic models
and the existing 1.5 GB Docker database cap. Never drop a shared database, graph,
bucket or vector service. Do not erase an active catalog namespace.

- [x] Register every external target mutation under a real restore lease and
  current owner. Shield/drain issued I/O through repeated cancellation. Retain
  pending or uncertain writes conservatively; never assume lease expiry proves
  that a remote write has stopped. Keep exact copied-object references.
- [ ] Admit typed owner cleanup for an exact failed/cancelled restore job. Freeze
  its fresh namespace, placement and bounded settled write inventory. Fence
  restoration retries permanently and serialize admission against writers.
- [ ] Erase only exact target objects/versions/uploads and scoped graph/vector
  projections. Remove allowlisted target authority/control rows. Revalidate the
  cleanup lease, current owner, inactive state and approved placement around I/O.
- [ ] Verify absence before an atomic completion/audit and original-quota release.
  Replay and partial failures must not double-refund or allow activation. Missing
  or uncertain historical write records block cleanup without refund.
- [ ] Add thin HTTP/console controls and a dedicated model-free worker. Show
  actionable pending/blocked states without credentials or private paths.
- [ ] Run tiny real-store corruption/revocation/cancellation/failure/replay tests,
  light unit/protocol regressions, Ruff/mypy; commit each verified milestone and
  update progress. Whole Task 18 remains open for its other acceptance gates.
