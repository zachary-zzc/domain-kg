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

Node corrections, semantic relationship claims, history, rollback and selective
vector work have passed targeted local checks. Description-only relationship
annotations, merge/split, suppression and retention-safe cleanup are still being
completed; Task 14 remains unchecked. HTTP/MCP exposure follows in Task 15.
