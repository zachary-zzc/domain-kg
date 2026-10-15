# Domain KG acceptance and evaluation

The first release is ready when the deterministic isolation and lifecycle gates pass, the frozen pilot corpus meets agreed retrieval targets, and backup/restore/repair works across independent registered storage profiles. Numerical retrieval and latency values below are initial engineering targets to validate during the pilot; they are not measured results.

## Permission and isolation gates

All of these must pass with zero unauthorized disclosures through HTTP, MCP tools, MCP resources, the console, citations, exports, and diagnostics.

- Exercise at least two organizations, independent MongoDB/Neo4j/Qdrant storage profiles including two physical graph/vector deployments, three KGs, and owner/editor/reader/denied identities. Include group membership and a scoped service identity.
- A reader cannot upload, change metadata, replace, curate, or remove materials. An editor cannot manage grants, archive a KG, or register a database. A platform operator has no implicit application data access.
- A restricted material grants access only within the KG membership boundary. A visible entity supported by both public and restricted materials exposes only its visible names, descriptions, assertions, paths, and counts.
- An invisible edge cannot serve as a bridge in graph retrieval. An invisible alias cannot seed a visible search. A hidden vector observation cannot contribute a result or contaminate a returned description.
- Requests using IDs from another organization, KG, or backend fail safely. Scoped reference validation and cross-store reconciliation reject cross-KG material/version, graph endpoint, and support references; validators/indexes alone are not treated as foreign keys.
- Membership revocation, group removal, local suspension, service identity revocation, and ACL changes deny subsequent access. Recheck policy revisions before returning an in-flight result; invalidate old cursors and any cached scope.
- Interleave principals on reused MongoDB/Neo4j/Qdrant clients and LightRAG adapters; prove no mutable global request scope leaks. A workspace-only filter is insufficient; every branch constrains accepted visible source versions before selecting top-k.
- Wrong issuer/audience, expired tokens, insufficient scopes, CSRF, and uninvited login fail. Control-database unavailability denies policy resolution.
- Last-admin and last-owner protections hold under concurrent updates. Permission changes are audited with actor, target, revision, and outcome.

## Material and graph lifecycle gates

1. Uploading bytes or text preserves a checksum-verifiable original and source metadata. Replaying an idempotency key returns the same receipt; different input under the same key conflicts.
2. PDF citations identify the correct physical page and block. DOCX citations identify a real heading/paragraph/table location. CSV citations identify rows; JSON citations identify pointers. Every quotation resolves to an immutable parsed block.
3. Malformed files, mismatched media type, encrypted PDFs, over-limit files/pages, invalid model JSON, unsupported predicates, and nonmatching quotations produce actionable rejection or explicit warnings.
4. A killed worker resumes without duplicate accepted versions or assertion supports. Cancelling a job or revoking its writer before publication prevents activation.
5. During replacement, a reader observes the complete prior version until graph/vector projections are verified and MongoDB accepts the new manifest. Concurrent replacement has one winner and an explicit version conflict; stale jobs cannot overwrite it.
6. If materials A and B support the same assertion, removing A leaves B's assertion retrievable and removes A's citation. Removing B removes the unsupported assertion from all branches. Test the same sequence with different material ACLs.
7. Exclude and restore are reversible within retained state. Delete tombstones immediately; purge follows retention and reference checks. Removing a material during ingestion cannot resurrect it through delayed publication.
8. Rename/type/description/property and relationship edits retain stable IDs, immutable before/after history, and manual provenance where meaning changes. Refresh affected vectors; persist overrides across re-ingestion. Merge/split and rollback retain source supports and current permissions.
9. Suppression fences a node and incident facts immediately across all branches, survives source re-ingestion, and cannot be undone by a delayed worker. Metadata-only filter changes trigger zero unnecessary extraction/embedding calls.
10. Replacing one changed block reuses unchanged extraction/embedding artifacts, recomputes affected dependencies, and keeps unrelated material untouched. New version-specific ownership references must remain correct.
11. Deduplication does not reveal material existence across organizations, KGs, or visibility classes. Shared artifacts retain separate source and deletion ownership.
12. A missing legacy original produces imported-unverified provenance, not a fabricated quotation. Such assertions are excluded from default verified search.

## KG and database management gates

- Create two KGs sharing one storage profile and a third using independently registered graph/vector endpoints. Validate MongoDB authority, Neo4j namespaces, Qdrant collections/payloads, and object prefixes. Demonstrate authorized search across selected KGs and isolation when one user lacks one of the grants.
- Update a KG's profile and state through expected revisions. Read-only blocks writes; archive removes normal search; unavailable storage never becomes an empty successful search.
- Register only preapproved connection-secret references. Catalog and error responses redact hosts where unnecessary, credentials, and provider payloads. Refuse backend detachment while a KG references it.
- An adapter declares supported modes and filters. An unsupported restriction fails; it is never silently ignored. A legacy gateway API key does not substitute for a user's policy.
- Cross-KG fusion uses ranked evidence with separate namespaces. It does not compare raw distances from unrelated embedding models or merge homonymous entities across KGs.
- Backup a KG including originals, restore it into a new namespace on the second profile, verify checksums and source supports, and test retrieval. Old memberships and material grants must not silently reopen access.
- Archive or purge a KG without changing another KG in the same physical infrastructure. Quota reservations, expired uploads, and orphan cleanup remain scoped.

## Publication and failure recovery gates

Inject failures before/after each [publication protocol](revisions-and-publication.md) transition. All must preserve accepted-view integrity:

- Neo4j-only or Qdrant-only completion cannot publish. Buffered LightRAG writes do not count as acknowledged projections.
- Worker death before marker advancement resumes safely; death after commit returns the same accepted receipt on replay.
- Expired lease holders write only isolated attempt IDs and cannot overwrite accepted records or advance a manifest.
- Abandoned/unpublished sources, curations, or vectors never become eligible in a later revision. Retained accepted records remain available to pinned authorized reads.
- Metadata/ACL changes, tombstones, and suppression override stale projection payloads immediately; in-flight responses recheck their visibility revision.
- Projection loss or checksum/generation mismatch produces a readiness/consistency error and repair, never an unverified old/new mixture. Authority failure denies reads.
- Repair and restore reconstruct or verify the accepted manifest before making the KG ready. Backup checkpoints identify the canonical revision and exact projection references.

## Retrieval evaluation

Build 120 hand-checked questions from a mixed work-domain corpus: 30 exact identifier/source questions, 30 semantic questions, 30 cross-document questions, 20 explicit graph relationship questions, and 10 questions with no supporting answer. Use a separate development set for prompt, threshold, and fusion tuning. Keep material versions, source locations, accessible KGs, and expected supporting assertions in each case.

Add overlays for material/type/tag/date filters, entity and predicate filters, restricted sources, multiple KGs, selective vector recall, and one unavailable backend. Do not use an LLM judge as the sole arbiter of source validity or permissions.

Compare vector, lexical, graph, fast, deep, deep plus reranking, and upstream LightRAG with the same material access, extraction inputs where possible, provider budgets, and output budget. Report differences in construction strategy separately from differences in retrieval. The PhageAgent evaluation corpus is useful as an additional scientific-domain regression set when authorized, but does not replace the work-domain set.

| Metric | Initial acceptance target or reporting rule |
| --- | --- |
| Unauthorized result/citation/derived-field exposure | Zero |
| Cross-KG constraint and routing failures | Zero |
| Returned source citations and parsed span resolution | 100% valid; manual results cite real curation records without invented spans |
| Accepted assertions with validated source, explicit manual curation, or imported-unverified provenance | 100% |
| Recall@10 on answerable pilot questions | At least 0.85 |
| Deep Recall@10 on cross-document and graph questions | Aim for at least 5 percentage points above vector-only; diagnose by category if not met |
| Overall deep regression against strongest single branch | At most 2 percentage points; revise defaults if worse |
| Precision@10, nDCG@10, source diversity, and graph-path validity | Report by question category and filter overlay |
| Selective-filter ANN recall against exact eligible-set search | At least 0.95 in the benchmark configuration |
| Questions without supporting evidence | Return no supported graph facts; calibrate per-profile relevance cutoffs on the development set and measure false-positive evidence |
| Deletion and ACL update visibility | No removed or unauthorized evidence on any new request after commit |
| Adapter and backend failure coverage | Correct explicit partial/error status in every injected failure case |

Report confidence intervals or per-case differences for retrieval comparisons. A small benchmark can guide release decisions without establishing a statistically significant advantage. If deep adds no benefit for a category, retain fast as the default and document when explicit graph exploration helps.

## Performance and operational checks

Use a recorded reference configuration, initially an 8-vCPU/32-GiB database host budget split and recorded across MongoDB, Neo4j, and Qdrant with 100,000 chunks, ten concurrent search requests, two selected KGs, fast mode versus deep with maximum two hops and reranking disabled. Measure cold and warm behavior, filtered and unfiltered search, per-branch latency, and memory. Start with end-to-end p95 targets below two seconds for fast and below five seconds for deep, with query embedding latency reported separately and included in wall time. These are unmeasured engineering goals; record resource allocation and revise targets from the pilot. Fast must make zero generative query/keyword, extraction, and reranking calls; explicitly count allowed query embedding calls.

Record parsing time per page, extraction and embedding usage, retry rates, and queue age under a fixed provider profile. Measure a 50-MiB input and a 500-page job within the worker resource limit; do not infer throughput from tiny fixtures. Enforce per-KG budgets and surface provider throttling without indefinite retries.

Run a backup/restore drill with a pilot goal of a 24-hour recovery point and recovery within four hours. Record actual dataset size and elapsed time. Verify accepted-manifest reconstruction, graph/vector repair, audit integrity and redaction, migration rollback procedures, worker restart, full-backend failure, object-store failure, and API restart during accepted ingestion. A successful restore must include permissions and originals, not just graph counts.

## Release record

The evaluation result manifest must include corpus hashes, dependency/container versions, schemas, parser/OCR settings, embedding profile and dimensions, extraction model/prompt version, LightRAG version/patches, projection generations, publication receipts, fusion settings, hardware, access fixtures, request budgets, and all errors. Keep provider secrets and source text outside the published manifest. Link the actual command output and report from the release checklist; an unchecked or failed invariant blocks readiness.
