# Publication-aware operations implementation plan

Execute natively in this chat, in dependency order, with bounded synthetic laptop
fixtures and verified local commits. This plan does not authorize delegation,
production deployment, a real corpus migration or additional model downloads.
Task 17's final console/browser gate precedes implementation here.

Spec: `docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`,
Task 18 of `2026-10-16-domain-kg.md`, and `docs/acceptance.md`.

MongoDB remains authoritative. Neo4j/Qdrant are immutable derived projections;
S3 holds originals and retained backup objects. Use logical authority databases
on the same Mongo replica set, as the existing placement registry requires.
No cross-store transaction or independent database dump establishes acceptance.
Keep the four Docker services within 1,500,000,000 bytes combined, without swap;
run storage tests serially. No large corpus benchmark is part of laptop smoke.

## 1. Typed operation jobs, current authority and redacted audit

Files: `operations/models.py`, `operations/service.py`, `operations/audit.py`,
existing `jobs/queue.py`, `jobs/runner.py`, runtime/HTTP/MCP adapters and tests.

- [ ] Define failure cases before code: reader/operator cannot admit owner work;
  revoked owners cannot continue it; forged target namespaces and mismatched job
  references fail; cancellation/recovery never makes incomplete work accepted.
- [ ] Extend existing declared `backup`, `restore`, `export` job kinds with real
  operation records and handlers. Do not pass non-source targets through source
  version checks or fabricate a material/version. Bind each operation to the
  verified actor, org/KG, command digest, lease and publication boundary.
- [ ] Keep admission/outbox/audit atomic in Mongo. Reuse existing policy audit
  records and add allowlisted outcomes/IDs; omit source text, queries, tokens,
  endpoints, exception payloads and credentials. Audit reads require current
  owner permission and scoped opaque cursors.
- [ ] Run serial operation admission/recovery/permission tests and static gates;
  commit the verified operation foundation.

## 2. Retained, manifest-bound backup and scoped export

Files: `operations/backup.py`, `operations/export.py`, `scripts/backup.py`,
`tests/integration/test_backup_restore.py`, and transport/console controls.

- [ ] Test a backup racing with publication, source removal/purge and policy
  revocation; original/manifest corruption; replay and a lost worker response.
- [ ] Pin one accepted manifest, source/curation ownership, metadata/visibility
  fences and required immutable authority records in Mongo. Protect referenced
  objects from live collection while capture is in progress. Stream bounded
  pages/objects with checksum/length verification into server-selected retained
  backup storage; seal a versioned manifest only after every required object
  and ledger record is present. Mark failed/incomplete packages unusable.
- [ ] Preserve original bytes exactly, validated evidence, import provenance,
  durable human history, suppression, source ownership and projection checkpoints.
  Do not include private connection/provider credentials or copy active leases.
  Keep backup expiry separate from live source retention and purge.
- [ ] Export only current, support-qualified evidence under an explicit read
  scope, including default unverified exclusion and every field's source ACL.
  Revalidate before sealing and download. Never expose arbitrary paths/object
  keys or let a package choose database commands.
- [ ] Run checksum/namespace/concurrency/export leakage tests, a tiny capture
  smoke and static gates; commit.

## 3. Restore to a fresh inactive placement

Files: `operations/restore.py`, `operations/remapping.py`, `scripts/restore.py`,
existing catalog/publication integration, and restore drill tests.

- [ ] Test the second approved storage profile, corrupted/cross-KG references,
  disabled old grants, current actor revocation, failure after one projection,
  manual edits/suppression/import trust, original bytes, and subsequent edits
  and native/import resolution without identity duplication.
- [ ] Allocate a fresh inactive KG under current restore authority and an
  approved placement. Build and validate typed compound-reference remapping;
  never replace UUID-looking substrings in evidence or user text. Preserve
  original hashes and locations. Recompute derived artifact seals and projection
  IDs; reject unsupported package/schema versions explicitly.
- [ ] Restore authority and retained curation history with no old membership,
  group, service-identity or material grants. Only the explicitly appointed
  current restoring owner receives a new grant. Historical actor provenance
  remains history and cannot establish present authority. Tombstones and
  suppression remain effective.
- [ ] Build immutable target projections and verify both stores against the
  restored authority. Require current approved embedding-space configuration;
  do not copy a provider credential, namespace allowlist or approval from the
  package. Atomically activate only the fully verified target manifest. Failed
  restores remain inactive and resume/clean up only their own namespace.
- [ ] Run the full-stack tiny restore/repair drill, verify retrieval, edits,
  originals and permissions, record duration and commit.

## 4. Complete repair and generation migration

Files: `operations/maintenance.py`, curation/source projection repair and tests.

- [ ] Test accepted curation/identity projection loss, provider failure, a
  concurrent edit/deletion and interrupted generation work before code.
- [ ] Reconcile accepted receipts/IDs for sources and curations. Rebuild missing
  projections from sealed authority artifacts without rerunning human intent or
  treating imported content as verified extraction. Publish verified replacement
  bindings; do not overwrite accepted records.
- [ ] Admit explicit index/schema migration into a new generation with current
  approved configuration. Old accepted generation serves throughout preparation;
  verify every required target before one fenced configuration/manifest switch.
  Preserve manual overrides, source ownership, ACLs, suppression and import trust.
- [ ] Run tiny failure/recovery/generation tests and static gates; commit.

## 5. Budgets, scoped metrics and deployment artifacts

Files: `operations/metrics.py`, provider budget integration, `Dockerfile`, CI,
Compose/least-privilege setup examples, `docs/operations.md`, quota/audit tests.

- [ ] Test racing quota/provider-budget reservations, failed calls and retries,
  audit redaction and operator-without-KG denials.
- [ ] Reuse transactional org/KG original-storage quota ledgers and provider usage;
  add bounded, durable provider reservations rather than post-call accounting
  alone. Expose owner-scoped usage, accepted/pending revisions, projection lag,
  failures and repair progress without source text or hidden data counts.
- [ ] Provide reproducible container/worker setup and least-privilege connection
  configuration, separate operator/bootstrap and application credentials,
  health/readiness behavior, backup expiry and recovery procedures. Preserve
  the laptop Docker caps. External deployment/provisioning remains a release
  acceptance gate, not an action performed by this plan.
- [ ] Run `pytest tests/integration/test_backup_restore.py
  tests/integration/test_quotas_audit.py -q`, applicable transport/console
  regressions, Ruff and strict mypy. Record measured drill results, update only
  verified Task 18 boxes and commit. Continue to Task 19 natively.
