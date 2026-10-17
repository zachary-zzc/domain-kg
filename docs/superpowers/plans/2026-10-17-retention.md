# Owner cleanup of expired retained packages

This expands operations section 5. Run natively and serially against the existing
1.5 GB Docker database cap, using synthetic providers only. No corpus migration,
automatic sweep, broad storage prune or inactive-target quota refund is implied.

- [x] Add a typed owner retention command with an explicit backup/export/both
  selection, expected publication and idempotency key. Freeze the server policy,
  cutoff, placement and bounded expired target descriptors. Claim targets once;
  cancelled/failed jobs resume through their original job rather than a rescan.
- [x] Require sealed source operations, immutable descriptor checksums, namespace,
  configuration compatibility, real cleanup lease and current owner permission.
  Ordinary source and backup/export workers must skip retention work.
- [x] Verify an entire backup before the first deletion. Persist a bounded,
  checksum-sealed object inventory, retain metadata until the final deletion,
  and reuse the inventory after partial erasure. Remove only exact server-derived
  retained keys, including versions, delete markers and unfinished uploads.
- [x] Commit the result and redacted audit only after verified absence; preserve
  live materials, graph publication, history, original-storage quota and siblings.
  Verify corruption, policy withdrawal, replay and resumable partial completion.
- [x] Expose the shared service through owner HTTP/console controls and a separate
  serial model-free worker. Load a bounded server configuration file and show its
  grace/target limits. Verify CSRF, explicit selection and editor/reader rejection.
- [x] Run focused real-store tests, lightweight unit/protocol regressions, Ruff and
  strict mypy; record results and commit the verified milestone.

Separate gates remain: incomplete captures, inactive restore-target erasure before
quota release, old storage-placement cleanup with an explicit retained-storage
permit, provider budget history and superseded projection collection. A completed
retention job does not establish these gates or the whole operations task.
