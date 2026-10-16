# Human identity grouping

Merge and split append human curation revisions. They group stable original entity
IDs without rewriting extracted observations, source assertions, support ownership,
or quoted spans. A merge chooses an explicit original as its representative. A
split supplies disjoint groups and an explicit representative in every group.

Each command names its complete dependency group and the current revision of every
member in that exact source/public scope. A dry run returns this membership and
revision preview without creating a job. Publication advances all affected node
counters together in MongoDB after isolated projection readiness checks. This is
not a transaction across the database engines.

Source-owned grouping applies only where all claim owners fall within the mapping's
source set. An A-only merge does not group an independently supported B claim or a
joint A/B manual claim. Owner-public grouping is structural metadata and applies
within a source-filtered view without adding public text or pretending to supply
source evidence. Private labels and human field corrections keep their own owners.

Graph views and search evidence return effective IDs with human identity witnesses.
Mapped edges and paths retain original endpoint IDs and unchanged assertion/support
IDs. An unambiguous alias lookup redirects to its visible representative; conflicting
source-specific representatives require an explicit source filter.

Identity changes publish metadata with no text extraction or embedding. Accepted
identity events remain durable and ordered so a later split does not discard an
earlier mapping needed by another group. The bounded read path rejects excessive
metadata rather than silently compacting across permission scopes.

MongoDB assigns an internal increasing sequence when admitting identity commands.
The partition reducer uses that sequence, so clock skew between service instances
cannot reorder causally accepted mappings or controls. Audit timestamps remain
timestamps; external evidence and history do not expose the internal sequence.

Owner rollback appends a control revision that disables the selected mapping, or
reverses a prior control. The current partition is replayed with later independent
changes preserved. Admission and worker publication recheck current ownership,
source access, source versions and every affected node revision. Historical grants
are never restored. Typed history includes partitions and explicit enable/disable
states with human provenance.

Suppression stores the original group members per source context and expands current
aliases before evidence filtering. Source-specific aliases do not become global
public fences. Persisted group members remain fenced after a later split.

The dedicated identity regression passed 11 real-store checks in 521.58 seconds.
It covers merge/split/owner rollback, all five search modes, independent A/B support,
source removal, member revision conflicts, current grants, public mappings under
source filters, suppression through re-ingestion, projection retry and opaque
retention after empty extraction. Metadata-only operations forbid model calls.

The broader Task 14 smoke passed 60 checks and encountered one transient Qdrant disconnect; that case passed a fresh rerun in 67.43 seconds. A clean combined-command verification remains pending. Its laptop command is
`uv run python scripts/smoke.py --task 14 --keep-services`. It uses tiny synthetic
fixtures and verifies the combined Docker hard limit remains below 1.5 GB with swap
disabled. It starts no native model. Task 14 remains unchecked until this gate passes.
