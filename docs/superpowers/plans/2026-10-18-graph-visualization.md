# Scoped graph visualization and medical PDF smoke implementation plan

> Execute inline with executing-plans, as required by AGENTS.md. No delegation.

**Goal:** Embed an existing Neo4j community graph renderer in bilingual node pages
and exercise a real approximately 400 KB medical PDF without expensive processing.
**Architecture:** The existing GraphService remains the sole data source. A small
console adapter emits only already-visible nodes/edges into safely escaped data.
Neovis.js consumes those arrays through public DataSet/network APIs in function
mode; it receives no database connection, credentials or arbitrary Cypher.
**Stack:** Jinja, existing GraphService, locally vendored neovis.js 2.1.0 Apache-2.0.
**Spec:** docs/design.md, docs/contracts.md, docs/revisions-and-publication.md;
Task 17 follow-up, user requested visualizations and a small medical PDF test.

NVL was considered but its current official licence limits use to Aura/commercial
Neo4j products. Use neovis.js for this Community installation. Pin npm integrity,
retain upstream/bundled licence notices and serve scripts locally under existing CSP.

## Stage 1 — Bounded, access-scoped interactive graph

- [x] Write failing adapter tests for namespace/dangling-edge rejection, limits,
  evidence-derived names, status distinctions and source/version-qualified links.
- [x] Add console/graph_data.py, templates/graph.html and static/graph.js; integrate
  only in node pages. Use existing 1-hop / 25-node / 40-edge service limits. Support
  pan/zoom/drag, selection links, bounded layout, fit and keyboard-accessible lists.
  Keep visible source/manual/imported-unverified distinctions. Escape labels and
  metadata; do not introduce an unrestricted graph endpoint or weaken CSP.
- [x] Pin/vendor the existing library; verify no-driver function mode against its
  exact source. Exercise JS adapter/event/error/cleanup behavior with Node unit
  tests, plus real-store node page scoping/revocation. Record the inability to run
  browser UI automation if disabled; do not claim an observed canvas render.
- [x] Run focused verification, update docs and commit the stage.

## Stage 2 — Real medical document, bounded smoke

- [x] Verify the actual downloaded PDF bytes/hash, native parse without OCR, small
  page/expanded-byte/time caps, chunks and exact evidence span resolution. Keep PDF
  and parsed content outside Git. Source: NHS Southampton, Your child's diabetes
  annual review, 412271 bytes, SHA256
  71f29a55c38addc0efec1d23d1d8900d43e7a6208268e7e120df5ceb7dbf45b7.
- [x] Exercise a source-scoped graph from that real document with the existing
  validated pipeline; clearly separate controlled fixture responses/manual facts
  from any actual model extraction. If trying the already approved 0.8B native
  model, limit to one short source window/request, CPU two threads, measured RSS,
  and stop the owned process. Never silently publish invalid extraction or claim
  synthetic responses establish medical/model quality.
- [x] Run storage checks serially under the existing Docker 1.5 GB cap; commit the
  evidence/docs and refresh a frozen bilingual preview with a clear graph entry.

Verification record: stage 1 Python 11 passed in 41.67 seconds and Node 3 passed
in 0.10 seconds. Full unit regression after the source-navigation review: 313
passed in 6.77 seconds. Whole real-PDF pipeline and page checks passed in 28.98
seconds, then the combined unit/PDF cohort passed 313 in 28.67 seconds. Ruff and
strict mypy (231 modules) passed. The fresh frozen preview was checked over real
HTTP for both languages, two nodes/one edge, exact source-version links and pinned
assets; CSP remained unchanged. Canvas appearance/dragging remains **unobserved**
because browser automation is disabled in Work settings. The timed viewing
window's pytest finished: 1 passed in 621.95 seconds, including the intentional
600-second viewing window; that duration is not extraction runtime.

## Follow-up — Correct the extraction-quality impression

- [x] Inspect the fixture: its only selected terms are `diabetes` and `annual
  review`. Thus two nodes/one edge establishes plumbing only; parsing all pages
  never established full-document model coverage.
- [x] Run one bounded real-model probe on page 4 (203 characters). Compact numbered
  evidence experiment: 133 prompt + 80 completion tokens, 3.527 s, 872611840-byte
  peak native RSS; owned process stopped. Existing canonical span validation
  rejects the batch: only one of five names matches its cited passage, zero
  relations. Zero publications. The compact experiment is not a production
  provider integration. The full protocol also fails its conservative 4096
  context preflight even for one character. No larger model downloaded.
- [x] Put an explicit bilingual controlled-fixture warning on medical preview
  pages. The missing-notice regression failed first (25.07 s); the focused
  rendering/source/access/PDF checks then passed 12 tests (21.30 s). Ruff and
  diff whitespace checks passed. This change affects only the opt-in fixture's
  isolated template environment; production templates remain unchanged.
- [ ] Produce and evaluate a genuine full-PDF KG with an adequate approved
  extraction provider. Neither this fixture nor the failed probe satisfies it.
