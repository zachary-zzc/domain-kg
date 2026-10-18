# Local platform completion implementation plan

> Execute inline with `executing-plans`, `test-driven-development`, and
> `verification-before-completion`. User requests completion of platform features
> and images locally; external deployment and cross-environment migration will
> be agreed separately. Task 9 live extraction quality remains a separate gate.

**Scope confirmation:** The user explicitly chose the current-configuration
platform for this pass. Embedding-model and domain-schema upgrade workflows
will be decided later, separately from environment migration.

**Goal:** Finish and verify the local platform, including a usable application
image, safe operations, and reproducible local release checks.

**Architecture:** Keep MongoDB authority and immutable Neo4j/Qdrant projections.
Retain existing shared policy/services for console, HTTP, and MCP. Local checks
must distinguish synthetic-provider correctness from real model quality.

**Spec:** `docs/design.md`, `docs/contracts.md`,
`docs/revisions-and-publication.md`, `docs/acceptance.md`, Tasks 18–19 of
`docs/superpowers/plans/2026-10-16-domain-kg.md` and the current user scope.

## Constraints and review focus

- Use the existing feature branch and native execution, without delegation.
- Existing four database services retain their 1,476,395,008-byte aggregate cap
  and disabled swap. No corpus/model downloads or live model requests.
- No remote deployment, image push, real corpus migration or IdP provisioning.
- Test namespace/permission checks, expired workers and deletion races against
  real stores. TTL alone never grants collection authority.
- Preserve manual provenance, independent source support and accepted history.
- Build images without credentials, private materials, development fixtures or
  runtime network requirements for tokenization; validate Linux parser isolation.
- Leave model-quality, external-client/IdP acceptance and high-resource benchmark
  gates open unless actually executed. Local platform acceptance is separate.

### Task 1: Reproducible locally verified image

Files: `Dockerfile`, `.dockerignore`, `scripts/check_distribution.py`,
`scripts/check_image.py`, runtime tokenizer preparation, packaging tests,
`docs/deployment.md`.

- [x] Reproduce missing-tokenizer/offline-image behavior before changing code.
- [x] Pin and verify required public tokenizer data at build time; never fetch
  model weights. Install the checked application wheel and include console assets.
- [x] Build a local image and verify non-root/read-only execution, private default
  MCP, liveness, worker entry points and isolated native PDF/text parsing with
  outbound network disabled. Record image identity and exact checks.
- [x] Run relevant unit/protocol, lint/type and package checks; commit verified work.

### Task 2: Complete local authority/history and safe operations

Files: `operations/authority.py`, generation/capture/retention services, typed
operation controls, `materials/purge.py`, corresponding integration tests.

- [x] Reproduce remaining history-only material, retired/purged source and
  alternative-checkpoint cases with tiny original/curation records.
- [x] Preserve complete allowed authority ancestry; implement explicit retirement
  and collection semantics without reviving erased sources or deleting accepted
  artifacts. Freeze target inventories and validate current owner/lease/pins.
- [x] Complete local typed maintenance controls and failure/retry paths; keep
  cross-environment placement migration outside this scope.
- [x] Run focused real-store lifecycle/permissions/fault tests and the required
  backup/quota/audit regression; commit only verified behavior.

### Task 3: Reproducible local evaluation and release manifest

Files: `eval/`, `tests/evaluation/test_release_gates.py`,
`docs/evaluation-report.md`, `docs/release-checklist.md`, `scripts/smoke.py`.

- [x] Define independent development and held-out local cases, including absent
  evidence, ACL overlays, multiple KGs, metadata updates and lifecycle faults.
- [x] Compare available retrieval branches under identical fixtures/budgets;
  record corpus/configuration hashes, per-case metrics and real-store evidence.
- [x] Clearly separate local synthetic-provider results from Task 9-dependent
  real-corpus semantic quality, remote IdP/client gates and production sizing.
- [x] Run the local release checks, bounded multi-KG pilot and restore/repair
  regression; retain measured results and only check verified gates.

### Task 4: Integration and final verification

- [x] Verify unit/protocol/frontend checks, real-store regression, distribution
  inventory and image runtime; inspect the final diff in a separate review pass.
- [x] Update status documents around local completion and explicit deferred
  deployment/model-quality gates, without reusing historical unchecked claims.
- [x] Commit verified stages; report concrete artifacts, commands and remaining
  dependencies. Do not mark broad Tasks 18/19 complete on partial evidence.

## Verified outcome, 19 October 2026 (Asia/Shanghai)

- Application revision `02a23d8`, source tree
  `e181b512d2b53e360917c9548eea4bd85c11a08d`; verification revision `0856abee`
  has the same application source. Later changes are documentation only.
- The complete default pytest inventory was reconciled across audited partitions:
  727 passed, six opt-in skips, zero unresolved failures, 733 collected. The native
  mix observer needed `functools.wraps`; the corrected failure and every remaining
  case were rerun (651 passed, one skipped). This is not a claim of a single
  uninterrupted pytest success. The per-ID proof and hashed logs are retained.
- Explicit current-source evaluation: 1 passed in 112.83 s; 12 questions, five
  modes, 41 valid source citations, zero unauthorized results and no live models.
  Multi-KG permissions, metadata updates and lifecycle faults are covered by the
  real-store regression, separately from the small retrieval ranking set.
- Explicit physical backup/restore/repair: 1 passed in 126.53 s; primary restored.
  Frozen-source browser preview: 1 passed in 89.89 s; the new generation option
  was selected, screenshot retained, preview finished. No browser cleanup was
  submitted. Optional file-picker walkthrough remains unverified.
- Frontend (four checks), Ruff, strict mypy (239 modules), distribution inventory
  and isolated image runtime passed. Exported Linux/arm64 image was loaded again;
  checksums and exact identities are in `.cache/local-release.json`.
- The bounded multi-KG pilot here is automated synthetic-fixture validation. Real
  human team use, independently reviewed real corpus metrics, Task 9 quality,
  external identity/client acceptance and production sizing remain separate.
  Model/schema upgrades and external deployment/migration are deferred by the
  user's explicit scope decision. Original composite Task 18/19 boxes stay open.
