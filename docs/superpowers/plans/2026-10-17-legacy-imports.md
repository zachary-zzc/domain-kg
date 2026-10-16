# Task 16 legacy import steps

Follow the Task 15 local transport verification milestone; keep its external login
and later maintenance-route gates recorded. Execute here with tiny generated
fixtures, no actual corpus migration, new services, native models or delegation.
Read `design.md`, `contracts.md`, `revisions-and-publication.md`, `code-reuse.md`
and Task 16 of the main plan before changing the publication path.

1. Define bounded, namespace-owned import records and a deterministic import plan.
   Accept service upload IDs and typed CSV/GraphML/pinned LightRAG manifests,
   never server paths, database commands, endpoints or credentials. Reject duplicate
   IDs, dangling edges, foreign namespaces, ambiguous source ownership, unsupported
   formats/capabilities and inconsistent snapshot manifests. Keep stable old/new
   mappings scoped by organization, KG and immutable import origin.
2. Adapt the reviewed PhageAgent CSV column names and normalization/manifest ideas
   with attribution. Keep byte checksums independent from versioned normalized
   hashes. Never normalize source text before validating evidence. Drop aggregate
   descriptions that cannot be attributed to one authorized source; preserve the
   uploaded original for review. Add pure malformed/adversarial fixture tests.
3. Store a durable plan bound to the actor, current policy/schema/publication,
   upload checksum, intended source mappings and expected revisions. Revalidate
   every referenced source/material/version and current permissions at execution
   and publication; replay identical commands and reject conflicting keys.
4. Import through isolated canonical preparation and the existing fenced
   graph/vector readiness/publication protocol. Keep existing human curation and
   independently supported assertions. Missing originals remain explicitly
   `imported_unverified`; default retrieval must exclude their graph claims,
   descriptions and raw import chunks. An uploaded CSV is an original snapshot,
   not proof that its claims appeared in a missing document. Actual original
   ingestion uses the regular span-validating pipeline; a legacy label or snapshot
   status must never manufacture a verified quotation.
5. Register authenticated HTTP plan/execute routes and worker mode only once their
   shared services exist. Report durable job receipts rather than fake accepted
   projections. Keep existing unfiltered gateways private/read-only and declare
   supported modes/filters explicitly. No native LightRAG mutation may touch an
   accepted projection directly.
6. Run the required serial integration command, scoped/upstream capability
   comparisons on identical synthetic fixtures, Ruff and strict mypy. Record
   construction/retrieval differences and the locked no-patch upstream boundary in
   `docs/decisions/0002-lightrag-compatibility.md`; commit verified milestones.

The existing Docker stack remains under 1.5 GB with swap disabled. Do not access or
migrate real PhageAgent corpus/snapshots, run its evaluation jobs, or modify that
workspace. Source inspection and adapting format/interface ideas are sufficient.
