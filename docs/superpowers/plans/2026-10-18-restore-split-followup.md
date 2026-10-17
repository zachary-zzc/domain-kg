# Restore split history and continue curation

Continue Task 18's full-history recovery gate natively against the design,
contracts and publication protocol. Use two source identities, one source fact,
synthetic providers and the existing capped databases. Keep storage tests serial.

- [x] Back up a source whose accepted human history contains both a merge and a
  later split. Forbid extraction/embedding calls after the initial tiny source
  setup. Restore into a fresh, explicitly approved inactive namespace through the
  real restore worker and verify split groups, original observations and evidence.
- [x] Verify source reader/editor grants did not become target grants. Under the
  current target owner, append a rollback of the imported split, then another
  split. Preserve both imported reasons/authors and the new owner's history;
  recheck source state and namespace ownership. Retry/response replay must not
  fabricate old leases or another publication.
- [x] Run the tiny real-store case and static checks. If a defect appears, record
  its concrete failure and implement the smallest correction before re-running.
  Update the verified recovery coverage, retain the other open gates and commit.
