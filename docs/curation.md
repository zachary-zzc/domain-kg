# Human curation work in progress

Node corrections are independent human records. Original extracted observations
and document spans stay intact. A durable job embeds changed manual text, stages
new graph/vector records, verifies all required receipts, then accepts the revision
in MongoDB. A partial write leaves the previous view visible.

A source-scoped edit declares material targets and inherits all their current
permissions and filters. Filtering to B excludes a correction owned only by A,
including its name, description, vector, citation and provenance. Re-ingestion
retains accepted corrections for the stable entity and material identities.

A KG-public override requires a human owner. It includes only explicitly supplied
public fields; private names, aliases, types and descriptions are not copied as
fallback values. A reader with no source access sees an opaque entity ID when no
public name was supplied. Source-less curation is excluded by material filters.

Expected revisions are scoped to a logical target and its exact curation scope:
the sorted source target set, or the owner-public scope. A-only and B-only edits
have independent revision checks. Competing edits to the same scope cannot both
advance its revision. Visible overrides have deterministic ordering by their
immutable creation time and opaque curation ID.

Manual search evidence has a curation revision, human actor/reason provenance and
a curation citation. It does not invent document version IDs, chunks or source
spans. Node-only annotations do not count as verified relationship support.

Tag-only edits do not embed text. If cumulative manual text and embedding profile
are unchanged, the new revision reuses its scope's sealed vector. The native
`python -m domain_kg.worker --once --curation-only` mode claims curation jobs only;
it can process nonembedded metadata with no configured provider. Changed text
still requires an approved embedding profile. This mode starts no model or extra
container.

Semantic relationship edits create an independently supported manual claim. The
original assertion and each material's verified support stay intact. Semantic IDs
remain stable through tag changes; each curation keeps its own human support and
provenance. A public claim requires explicit subject, predicate and target IDs and
can survive material deletion with opaque endpoint nodes. Deleted source labels
are never recovered as public fallback fields.

An owner rollback appends a compensating revision under current permissions. It
reverses only the selected revision's changed fields within the same source/public
scope and preserves later changes to other fields. Withdrawing a manual claim
removes its active projection while independently verified source support survives.

History returns typed human before/after fields, provenance and publication state.
It excludes private source snapshots and grants. Paging uses opaque cursors bound
to the principal, target, query limit, policy, visibility and accepted manifest.
A source removal or permission change expires the old cursor. Hidden source
corrections are filtered before paging, including when another source supports the
same entity.

A description-only relationship edit is a separately attributed annotation on the
original verified edge. It does not manufacture a new support. Source-A annotations
are omitted from B-only views. Annotation text is searchable in all four recall
modes; positive filters require an eligible original claim, and verified-only
requests exclude manual search evidence. Tag changes reuse unchanged vectors.

Eligible source-owned human claims also retain opaque endpoints when a replacement
contains no extracted entities. This preserves human work without recovering old
source labels, types, descriptions or spans. Removing their owning material still
excludes them. Independently public claims retain their own ownership.

Retained claims can satisfy a type filter through an explicit eligible human
endpoint type. A source-scoped claim cannot borrow a type from an independent
source. Curated endpoint fields remain visible with their own provenance when the
matching retained human claim qualifies the filter.

Node suppression immediately fences its source-scoped fields, incident claims,
chunks and human annotations in every retrieval mode. A source-scoped fence does
not hide independent support from other materials. Public suppression requires
an owner. Re-ingestion cannot clear the fence. Restoration verifies the retained
original and all projection receipts before changing visibility.

Suppression and restoration append immutable human history and use the same
target/scope revision counter as field edits. Management history can include
suppressed revisions under current curator permissions; ordinary history filters
them before paging. Owner rollback appends a compensating visibility action and
rechecks current permissions and restoration readiness.

The final visibility regression passed 21 real-store checks in 711.76 seconds.
Merge/split now retains immutable source evidence and per-source identity witnesses.
The dedicated regression passed 11 real-store checks, including competing member
edits, owner compensation, current grants and projection recovery. See
[identity grouping](identity-curation.md). Full reprocessing restoration and
retention-safe cleanup have separate passing local checks. Task 14 remains unchecked
while its combined laptop verification runs.
HTTP/MCP exposure follows in Task 15.
