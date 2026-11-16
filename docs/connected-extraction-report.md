# Connected, content-derived extraction

Tested 1 November 2026 against the requested Downloads hormone-testing folder.
Automatic extraction now retains only nodes participating in validated relations.
Built-in profiles accept content-derived node types and predicates. Source text
remains independently searchable even when extraction produces no graph facts.

## Algorithm changes

- Extract relations and their endpoints without supplying a preset vocabulary.
  Built-in schemas are open, version 2; stored legacy/custom schemas remain closed
  unless explicitly configured otherwise. Labels remain bounded property values.
- Preserve valid first-response facts when the single corrective response omits
  them. Remap temporary IDs and deduplicate exact observations and assertions;
  different confidence estimates do not create extra evidence ownership.
- Route entity/assertion record overflow through existing bounded source splitting.
  Malformed fields still fail validation; no qualifiers or citations are silently
  repaired to admit a claim.
- Remove automatic orphan observations after evidence validation and before
  LightRAG. Map native types opaquely so upstream punctuation restrictions cannot
  silently discard legitimate content-derived labels.
- Share schema validation with imports and curation, preserve namespace/access
  checks, and invalidate older extraction checkpoints. Publication remains staged
  and immutable; existing accepted graphs are not edited in place.

## Corpus comparison

The production parsers read 14 documents: 3 DOCX, 3 XLSX, 6 PDF and 2 PPTX;
11 are original documents and 3 are generated summaries already in the folder.
The inventory contains 69,456 parsed characters and 2,052 blocks. Navigation HTML,
integrity metadata and hidden build artifacts were excluded. One PDF required a
local evaluation override of 2,048 MiB and 300 seconds; OCR warnings were retained.
Production parser settings were not changed.

Both runs used the same 251 frozen source windows and configured Alibaba
`qwen3.6-35b-a3b` profile: `source_names_v1`, `source_roles_v1`, JSON-object output,
16,384 context tokens and 8,192 maximum completion tokens. The user approved the
content transfer and paid calls. The baseline source snapshot includes the user's
pre-existing workspace changes. Final correction-preservation behavior was tested
by replaying the exact saved revised responses, with **zero additional API calls**.

These are extraction observations summed across windows, **not counts of a
deduplicated, published KG**. Failed windows contribute no observations.

| Measurement | Baseline | Revised |
| --- | ---: | ---: |
| Successful windows | 221 / 251 | 215 / 251 |
| Node observations | 1,520 | 644 |
| Isolated node observations | 896 | 0 |
| Relationship observations | 437 | 540 |
| Distinct literal predicate labels | 6 | 158 |
| Distinct literal entity-type labels | 5 | 195 |
| Distinct literal endpoint/predicate/polarity tuples | 391 | 489 |
| Recorded API responses | 387 | 411 |
| Input tokens | 629,795 | 558,439 |
| Output tokens | 449,320 | 458,726 |

The aggregate relationship count increases 23.6%, but the gain is concentrated
in generated summaries. Original-document relationships decrease from 189 to 171;
summary-document relationships increase from 248 to 369. On the 192 windows where
both runs succeed, relationships decrease from 355 to 329. This experiment proves
the connectivity invariant and removal of the fixed vocabulary; it does **not**
establish a general relation-recall improvement. Literal label counts include
synonyms, spelling variants and different languages, not 158 normalized meanings.

The revised run has 36 failed windows versus 30 in the baseline. All report
invalid structured responses; observed causes include unsupported qualifier names,
missing required fields and malformed records. These failures remain explicit.
A failed material cannot publish only its successful windows through the normal
ingestion pipeline. The comparison did not publish the private corpus to a KG.

A deterministic spot check selected one relationship from each of the 12 documents
with accepted observations. It confirmed useful sampling, method, analyte and
table relationships, but also found an unsupported directed relationship between
two nearby terms in fragmented PDF text. Exact citation matching is not a semantic
truth guarantee. This small, non-blinded check is not a precision estimate; broader
semantic accuracy and normalization remain limitations.

## Verification and adoption

- Complete unit suite: **871 passed**.
- Ruff: passed. Strict mypy: passed for **290 source files**.
- Broad real MongoDB replica-set / Neo4j / Qdrant suite: 47 passed and 3 failures
  from fixtures hardcoding the former built-in schema revision. Those fixtures now
  use `schema.revision`; the full affected publication/resolution rerun passed all
  7 tests. Together these cover 50 distinct selected integration cases, including
  publication failures, concurrent revisions, removal, curation, imports and caches.
  A final cache/retry rerun after the last algorithm change passed all 5 cases.
- Regression tests cover all three extraction response formats, correction ID
  collisions, duplicate confidence estimates, rejected-edge orphan pruning, record
  limits, punctuation-bearing types, searchable orphan-only text, and legacy schema
  serialization and restrictions.

Existing built-in-profile materials adopt the change through normal re-ingestion
and verified publication. Accepted projections remain unchanged until that succeeds.
Explicit stored closed schemas retain their vocabulary. Manual curation and
independently supported assertions are preserved by the existing lifecycle.

Private parsed documents, saved requests/responses, source snapshots, per-window
results and the spot-check evidence remain under ignored
`.cache/connected-extraction/`. Main results are `baseline/summary.json` and
`revised-final/summary.json`; `verification-manifest.json` seals source snapshots
and the shared windows. Token totals above exclude earlier exploratory diagnostics
and do not count cached replays as paid calls. No private corpus content, provider
credentials or generated index was added to Git.
