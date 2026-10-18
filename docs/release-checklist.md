# Local platform and release gates

The current work targets a complete local platform. External deployment and
cross-environment migration will be agreed separately. Task 9 real extraction
quality remains outside this completion pass. An opt-in test being skipped
never counts as acceptance.

The user confirmed that this pass completes the platform on its current
configuration. Model/schema upgrade workflows will be decided later.

## Local verified evidence

- [x] Build an application wheel/source pair and inspect its private-file boundary.
- [x] Build Linux/arm64 image and verify its actual default API command, non-root
  read-only/no-network execution, console assets, worker CLI, offline tokenizer,
  native text/PDF parser and unauthenticated MCP denial.
- [x] Run the explicit 12-case/five-mode local evaluation, validate returned
  citations and hidden/empty scopes, and record model calls and measured metrics.
- [x] Complete retired-source and alternative-checkpoint authority/history checks,
  including repeat backup/rebuild after restoration.
- [x] Complete safe abandoned generation/capture collection and local maintenance
  controls; time elapsed alone does not prove a stale writer has stopped.
  Generations with legacy/uncertain writes stay protected; registered placement
  and schema/model upgrades retain separate scope decisions.
- [x] Collect acknowledged, stopped incomplete backups and earlier failed attempts
  after successful retry; preserve uncertain/legacy writes and the accepted backup.
- [x] Run fresh final unit/protocol/frontend and appropriate real-store regressions.
  The 733 collected pytest cases were audited across partitions: 727 passed,
  six explicitly skipped, zero unresolved failures. A test observer was corrected
  to preserve the native function signature; its failed case and all unexecuted
  cases were rerun (651 passed, one skipped). Application source was unchanged.
  The opt-in current-source evaluation, browser preview and physical restore/repair
  then each passed separately. Frontend checks, Ruff and strict mypy also pass.
- [x] Rebuild the final image from the verified application revision and retain
  its immutable local ID and verification report.

Final local verification: **19 October 2026 (Asia/Shanghai)**. The release
manifest is `.cache/local-release.json`; per-case states and hashed logs are in
`.cache/verification/regression-report.json`. The final evaluation took 112.83 s,
the physical restore/repair 126.53 s and the frozen-source browser preview 89.89 s.
The original physical configuration was restored and the preview exited cleanly.
These are bounded synthetic-provider platform checks; they made zero live model
calls. File-picker and real external IdP/client walkthroughs remain separate.

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
