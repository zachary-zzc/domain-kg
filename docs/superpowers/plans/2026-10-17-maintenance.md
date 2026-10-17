# Curation repair and generation maintenance

This expands operations-plan section 4. Execute natively, serially, with synthetic
providers and the existing 1.5 GB Docker ceiling. No installation, new chat,
production migration or external deployment is authorized by this document.
Read `docs/revisions-and-publication.md` and preserve its publication rules.

## 1. Owner maintenance admission

- [ ] Add a typed owner `maintenance` job/command, initially `repair_curation`,
  with KG, expected publication, curation UUID and idempotency key. Reuse current
  verified-owner admission, lease, snapshot and audit; never invent a material
  version or impersonate the historical curator.
- [ ] Before admission, locate the exact accepted curation binding, canonical
  history and immutable document under the current namespace. Capture its owner
  key, manifest, content seal and base artifact provenance. Deny reader/editor,
  cross-KG, stale-publication and unresolved/pending history requests.
- [ ] Keep ordinary source workers and model-free backup/export workers from
  claiming maintenance. A dedicated serial worker handles it.

## 2. Immutable repair without replaying human intent

- [ ] Demonstrate missing node/relation/identity projections, current actor
  revocation, concurrent edit/deletion and a partial backend failure first.
- [ ] Read the sealed accepted curation artifact and verify its directive against
  authoritative history. Reuse its valid vectors. Write attempt-specific graph,
  vector and lexical projections in the current generation, then read them back.
  Make no extraction/embedding call and do not change curation history/counters.
- [ ] Publish only a verified replacement binding under the same established
  owner key. A Mongo transaction rechecks the real owner job, current policy,
  source/configuration snapshot and parent binding, then updates the manifest,
  completion record and audit. No accepted projection is overwritten.
- [ ] A typed maintenance origin authenticates the artifact independently of the
  current repair actor. Accepted readers, subsequent backups and restore must
  understand it without manufacturing an old active lease. Keep artifact ancestry
  bounded and preserve the historical actor as provenance only.
- [ ] Verify node/relation/identity reads, current grants, rollback after repair,
  backup/restore after repair, cancellation/replay and failure isolation. An
  already broken accepted view continues to fail closed until repair completes.

## 3. Explicit generation preparation and switch

- [ ] Admit an explicit owner generation/index or compatible schema migration
  against an accepted snapshot and approved target configuration. Reject
  unsupported/breaking transformations explicitly; never drop facts to make a
  schema appear valid.
- [ ] Preserve canonical IDs, originals, evidence, manual history, source ownership,
  current ACLs, suppression and imported-unverified status. Any retained-source
  reads need a real maintenance-job permit, not relaxed ordinary retrieval.
- [ ] Stage a separate target generation, obtain only needed approved embeddings,
  verify every source/curation projection, and recheck current policy/configuration
  before one fenced Mongo manifest/configuration switch. The old accepted
  generation remains serving throughout preparation.
- [ ] Test provider/backend failure, concurrent edit/deletion and interrupted
  preparation/switch with a tiny mixed native/manual/import fixture.

## 4. Service, console and final gates

- [ ] Expose typed repair/migration controls through the shared services and thin
  authorized transports. Show pending/accepted state without hidden source fields,
  credentials, arbitrary object keys, database commands or server paths.
- [ ] Run focused real-store failure/recovery tests, lightweight unit/protocol
  regressions, Ruff and strict mypy. Commit verified milestones. Keep the main
  Task 18 box open until restoration, retention, quotas/metrics and deployment
  artifacts also meet their separate gates.
