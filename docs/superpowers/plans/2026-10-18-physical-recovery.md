# Serial physical-profile recovery drill

Continue Task 18's second-profile restore/repair gate natively. Follow the design,
contracts and publication protocol. Use one tiny native source and one human
rename, synthetic provider responses, the existing Mongo replica set/S3 service,
and the existing Compose secondary graph/vector volumes and ports.

- [x] Seal a real authoritative backup on primary storage; verify image/cache and
  hard memory bounds before changing containers. Forbid providers after source
  setup. Close clients before switching only Neo4j/Qdrant to secondary storage.
- [x] Register the server-approved secondary connections/profile, restore through
  the actual owner worker, verify original bytes, retrieval, human provenance,
  separate authority database and denial of old grants. The stopped primary
  profile must fail explicitly rather than route into secondary projections.
- [x] Remove one exact fixture-owned target vector, request a real source repair,
  verify readiness/retrieval and append a target-side metadata edit. Confirm the
  source remains unchanged after switching back.
- [x] Always restore primary containers in `finally`, including on failure. Use
  the same cached images with `--pull never --no-build`; no volume/image removal,
  extra running database pair, load test or real model request. Clean only this
  drill's random target namespace and secondary authority database.
- [x] Run the explicit `--run-physical` case serially, record duration/cap/routing
  evidence and commit. Simultaneous physical-profile throughput is outside this
  capped laptop drill; preserve the other open Task 18/19 gates.
