# Local platform completion implementation plan

> Execute inline with `executing-plans`, `test-driven-development`, and
> `verification-before-completion`. User requests completion of platform features
> and images locally; external deployment and cross-environment migration will
> be agreed separately. Task 9 live extraction quality remains a separate gate.

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

- [ ] Reproduce missing-tokenizer/offline-image behavior before changing code.
- [ ] Pin and verify required public tokenizer data at build time; never fetch
  model weights. Install the checked application wheel and include console assets.
- [ ] Build a local image and verify non-root/read-only execution, private default
  MCP, liveness, worker entry points and isolated native PDF/text parsing with
  outbound network disabled. Record image identity and exact checks.
- [ ] Run relevant unit/protocol, lint/type and package checks; commit verified work.

### Task 2: Complete local authority/history and safe operations

Files: `operations/authority.py`, generation/capture/retention services, typed
operation controls, `materials/purge.py`, corresponding integration tests.

- [ ] Reproduce remaining history-only material, retired/purged source and
  alternative-checkpoint cases with tiny original/curation records.
- [ ] Preserve complete allowed authority ancestry; implement explicit retirement
  and collection semantics without reviving erased sources or deleting accepted
  artifacts. Freeze target inventories and validate current owner/lease/pins.
- [ ] Complete local typed maintenance controls and failure/retry paths; keep
  cross-environment placement migration outside this scope.
- [ ] Run focused real-store lifecycle/permissions/fault tests and the required
  backup/quota/audit regression; commit only verified behavior.

### Task 3: Reproducible local evaluation and release manifest

Files: `eval/`, `tests/evaluation/test_release_gates.py`,
`docs/evaluation-report.md`, `docs/release-checklist.md`, `scripts/smoke.py`.

- [ ] Define independent development and held-out local cases, including absent
  evidence, ACL overlays, multiple KGs, metadata updates and lifecycle faults.
- [ ] Compare available retrieval branches under identical fixtures/budgets;
  record corpus/configuration hashes, per-case metrics and real-store evidence.
- [ ] Clearly separate local synthetic-provider results from Task 9-dependent
  real-corpus semantic quality, remote IdP/client gates and production sizing.
- [ ] Run the local release checks, bounded multi-KG pilot and restore/repair
  regression; retain measured results and only check verified gates.

### Task 4: Integration and final verification

- [ ] Verify unit/protocol/frontend checks, real-store regression, distribution
  inventory and image runtime; inspect the final diff in a separate review pass.
- [ ] Update status documents around local completion and explicit deferred
  deployment/model-quality gates, without reusing historical unchecked claims.
- [ ] Commit verified stages; report concrete artifacts, commands and remaining
  dependencies. Do not mark broad Tasks 18/19 complete on partial evidence.
