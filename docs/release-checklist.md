# Local platform and release gates

The current work targets a complete local platform. External deployment and
cross-environment migration will be agreed separately. Task 9 real extraction
quality remains outside this completion pass. An opt-in test being skipped
never counts as acceptance.

## Local verified evidence

- [x] Build an application wheel/source pair and inspect its private-file boundary.
- [x] Build Linux/arm64 image and verify its actual default API command, non-root
  read-only/no-network execution, console assets, worker CLI, offline tokenizer,
  native text/PDF parser and unauthenticated MCP denial.
- [x] Run the explicit 12-case/five-mode local evaluation, validate returned
  citations and hidden/empty scopes, and record model calls and measured metrics.
- [ ] Complete retired-source and alternative-checkpoint authority/history checks,
  including repeat backup/rebuild after restoration.
- [ ] Complete safe abandoned generation/capture collection and local maintenance
  controls; time elapsed alone does not prove a stale writer has stopped.
- [ ] Run fresh final unit/protocol/frontend and appropriate real-store regressions.
- [ ] Rebuild the final image from the verified application revision and retain
  its immutable local ID and verification report.

## Separate acceptance dependencies

- [ ] Task 9: qualified extraction provider and genuine full-document publication.
- [ ] Task 19: independently reviewed real pilot set, branch/baseline comparisons,
  team usage and workload measurements with the approved hardware/provider budget.
- [ ] External OIDC and target MCP client/Inspector acceptance.
- [ ] External least-privilege deployment, CI/hosting and environment migration.

Sources: `docs/acceptance.md`, `docs/status-review.md`, the local completion plan,
`docs/evaluation-report.md` and `docs/deployment.md`. The main Task 18/19 boxes
remain open until their complete original gates are verified or the user
explicitly revises their release scope.
