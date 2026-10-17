# Bounded projection-work observability

Task 18 follow-up; native execution, existing capped stores, synthetic providers.
Read alongside design/contracts/revisions-and-publication. This observes pending
work and does not declare acceptance from a job stage or claim cross-store atomicity.

- [x] Define owner-only namespace-qualified metrics for projection-bearing jobs:
  pending/failed/repair-required counts and oldest pending admission time/age.
  Bound rows to 1,000 plus one overflow sentinel; report truncation explicitly.
  Validate rows, omit job/source/provider payloads, fail closed on corruption.
- [x] Revalidate current configure permission before/after one bounded Mongo query.
  Add a scoped index, typed HTTP endpoint and bilingual console summary. Keep
  accepted graph/vector receipt readiness separate from queue age.
- [x] Verify real Mongo scoping, terminal stages, overflow, post-read revocation,
  corrupt/backend errors and owner/reader HTTP/page boundaries serially. Run
  lint/mypy and the focused unit suite, record duration, then commit the stage.
