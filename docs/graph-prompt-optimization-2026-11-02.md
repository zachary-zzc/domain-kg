# Graph construction prompt experiment — 2 November 2026

The prompt can improve fact coverage, but a larger graph is not sufficient evidence
of better retrieval or answers. The new policies remain opt-in; no accepted KG or
browser default was changed.

## What the investigation established

The current built-in schema already permits source-derived node and relation types.
Early 35B output from 258 public windows contained 780 distinct literal predicate
strings, including wording variants. This is evidence against a remaining six-predicate
whitelist, not evidence of 780 distinct relationship meanings.

Saved hormone-table replies exposed a concrete omission: the model emitted relations
whose targets were price values but did not declare those values as entities. The
validator correctly rejected the undeclared endpoints. Another failure was omitted
required fields or unsupported qualifier names. Increasing the requested edge count
does not resolve either problem.

Fragmented OCR is a separate limit. A model can pair an analyte with the wrong reference
range even when both strings occur in the source. Literal quotation checks establish
provenance, not the semantic correctness of that pairing.

## Candidates and invariants

`source_facets_v1` explicitly checks source-stated roles, parts, properties, methods,
inputs, measurements, quantities, requirements, outcomes and limitations. These are
reminders rather than an ontology or quota. It asks for atomic facts and reusable,
specific labels rather than sentence-length predicates or synonym proliferation.

`source_facets_v2` adds a concrete output step: derive the entity list from all relation
endpoints, including values and property phrases, and verify that every endpoint is
declared. It also specifies qualifier fields, warns against double negation and guessed
OCR row/column associations, and preserves the source's units and ranges.

All three extraction formats support the policies. Custom closed schemas retain their
existing vocabulary and role guidance. No validator is relaxed. Automatic isolates are
still pruned. The same 8192 output / 16384 context budget, bounded evidence correction,
source splitting and remote-only inference rules apply. The first candidate fits all
754 public and 251 hormone windows; the second prompt is 76 UTF-8 bytes shorter.

## Diagnostic design

- Eight frozen sources: the smallest document ID in each of SQuAD, HotpotQA, MuSiQue
  and DROP, plus four original hormone-document windows covering a price table,
  physiological roles/limitations, sampling/transport/methods, and fragmented OCR.
- 27B and 35B build each source using the control and both candidates. This is a
  development diagnostic, not a random sample or a held-out test. The second candidate
  was informed by the first trial's failures.
- The fixed Alibaba 397B judge receives source text and anonymized facts, without model
  or policy labels. Identical fact/evidence records are shared across arms so they receive
  the same judgment. Every accepted assertion is included, in batches of at most twelve.
- Judgment reuses the existing semantic source evaluator. It accepts equivalent names
  and predicates, checks direction, roles, polarity and essential scope, and distinguishes
  supported, contradicted, insufficient and uninformative claims. Invalid/missing judgments
  stay unknown, with their denominators retained.
- A source-only inventory of at most eight propositions was generated before extraction.
  All four hormone inventories failed literal quotation-provenance validation. Those
  inventories are unavailable: the audit still judges every hormone claim against its
  source, but does not report reference coverage for those sources. This is not a penalty
  for non-exact semantic wording. The inventory is neither exhaustive nor human gold.

The original first-trial summary omitted unjudged hormone claims from its claim counters.
Do not use that summary as an eight-source precision estimate. The separate `source-audit`
pass includes every planned accepted fact record across all three policies, including
unknown judgments, and is the reportable diagnostic.

## Diagnostic results and visual counterexample

These are **automated source-audit labels, not verified precision**. Counts are across
the eight selected development sources. `Facts` counts distinct fact/evidence records;
`Relations` includes repeated observations. Public reference coverage uses the same
26 model-generated propositions on four public sources. No hormone reference coverage
is available.

| Model | Policy | Nodes | Relations | Facts | Supported | Contradicted | Insufficient | Uninformative | Unknown | Public references covered / 26 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 27B | roles | 45 | 36 | 30 | 26 | 3 | 1 | 0 | 0 | 9 |
| 27B | facets v1 | 51 | 59 | 58 | 53 | 3 | 1 | 1 | 0 | 17 |
| 27B | facets v2 | 56 | 51 | 42 | 40 | 1 | 0 | 1 | 0 | 18 |
| 35B | roles | 59 | 68 | 65 | 58 | 7 | 0 | 0 | 0 | 16 |
| 35B | facets v1 | 87 | 92 | 91 | 80 | 10 | 0 | 1 | 0 | 18 |
| 35B | facets v2 | 119 | 125 | 107 | 82 | 10 | 0 | 3 | 12 | 19 |

27B failed extraction on two of eight sources in each arm; the identities of those
sources differ. 35B completed all eight. Failed sources are retained in the source
denominator but emit no accepted facts; claim support rates alone conceal that loss.
The twelve unknown judgments are one OCR-source batch rejected because a reason
exceeded the evaluator's response limit. They are not assigned zero semantic quality.

The 35B gain is concentrated in hormone sources. On the four public sources alone,
automatically supported fact records changed from 49 (roles) to 41 (v2), while covered
references increased from 16 to 19. Even this small diagnostic therefore does not show
a uniform improvement.

Codex visually inspected the complete original PDF page for `h-124` (physical page 7,
printed page 4), rather than trusting the flattened OCR. The page has nine table rows;
OCR omitted the abbreviation in the second row and lost row/column correspondence.
For the 18 relations emitted by 35B v2, **8 agree with the visible source and 10 do not**.
Incorrect claims include attaching another row's range or unit to an analyte.

The text-only automated judge marked four of those incorrect relations supported.
Its two contradicted labels agree with the visual review, but their explanations also
misassign other rows. Twelve labels remain unknown. The eight visually correct claims
also cannot be assumed inferable from the flattened text. This is a selected failure
analysis by the assistant, not independent human gold or an estimate for the whole corpus.

The separate private `pdf-review/h-124-visual-audit.json` records every fact ID, original
automatic label, visual verdict/reason, transcribed source rows, and hashes of the PDF,
complete rendered page and audit inputs. Original scores and frozen inputs are unchanged.
The failure demonstrates that exact quote provenance does not establish entailment,
and that an extractor and text-only judge can share the same alignment mistake.
Row-preserving table extraction is a demonstrated next bottleneck; prompt guidance
alone did not repair information lost during parsing. The source's printed ranges
were checked for transcription, not independently validated as clinical guidance.

## Fixed retrieval and QA experiment

The four-model full experiment was started separately with `source_facets_v2` on the
original 747-document corpus and 23 reviewed questions. Its paired control is
`source_roles_v1` with the same sentence-level evidence adapter, so the comparison does
not conflate prompt changes with the earlier negation/evidence-granularity change.

The two methods remain dense `vector_rag` and complete `domain_kg`; embedding, judge,
semantic metrics, questions, context budgets and reader instructions are unchanged.
Identical vector-control requests are reused. `kg_quality_compare.py` permits the
declared construction-policy difference while rejecting evaluation-protocol changes.
The experiment does not isolate KG from BM25 and is not a generalization claim.

### Completed offline construction and retrieval checks

After the provider stopped, an offline replay completed the evidence-adapter control
for 27B, 35B and 122B using exact saved API requests through the frozen production
extraction code. It preserved failures and ambiguous reservations. A cache miss stops
before reservation, and the HTTP transport rejects every network request. This replay
made **zero new inference calls** and did not run a local model.

Only 35B has complete construction in both prompt arms. The following comparison uses
the same sentence-level evidence adapter, all 754 windows and all 747 documents.

| Structural measure | Roles control | Facets v2 |
| --- | ---: | ---: |
| Node observations | 4,628 | 7,471 |
| Relation observations | 4,533 | 7,491 |
| Documents with relations | 591 / 747 | 662 / 747 |
| Failed extraction windows | 129 / 754 | 75 / 754 |
| Literal node-type labels | 745 | 1,290 |
| Literal predicate labels | 1,981 | 2,816 |
| Predicate labels seen once | 1,314 | 1,777 |
| Duplicate claim observations | 244 | 352 |
| Nodes after retrieval's lexical name joins | 3,671 | 5,880 |
| Nodes occurring in multiple documents | 279 | 438 |
| Isolated nodes | 0 | 0 |
| Weakly connected components | 439 | 453 |
| Largest component's fraction of nodes | 17.3% | 29.8% |

Literal label counts include synonyms and do not establish semantic diversity.
Retrieval uses dataset-scoped normalized-name joins; these joins are not independently
verified entity resolution. Connectivity and multiple-document occurrence therefore
do not establish that those links are correct. The zero-isolate result also does not
measure missing knowledge: automatic construction already prunes unconnected entities.

The same frozen retrieval procedure was replayed for all 23 questions with each 35B
graph. **All 23 dense-control evidence packets are identical. Only 2/23 domain-KG
source packets change; all 23 domain-KG packets change when path annotations are
included.** Thus the larger graph mostly changes the paths supplied to the reader,
with a limited effect on selected sources in this sample. Whether those path changes
help or hurt answers still needs the fixed semantic judge. Packet equality is only
an invariance/output-change check, never an exact-match quality score.

`offline-35b-diagnostic/summary.json` contains graph statistics and input hashes;
the adjacent packet artifacts contain both arms. The check verifies frozen snapshots,
identical questions/corpus/windows/vector ranks, unchanged evaluation protocol except
the declared construction policy, and identical retrieval code. Replay elapsed times
are not cloud inference latency or production-datastore performance measurements.

The full run stopped on Alibaba HTTP 400 `Arrearage`. A minimal fresh request on
2 November 2026 at 16:00 Asia/Shanghai returned the same error. No benchmark workers
remain running. This is a provider billing/access block, not an extraction-quality
score or a local approval failure. There is no complete paired retrieval/QA result.

At the stop, baseline construction had processed all 754 windows for 27B, 35B and
122B, and 421/754 for 397B. The v2 prompt arm had processed 551/754, 754/754, 713/754
and 196/754 respectively. A processed window can be a recorded extraction failure;
these counts do not mean successful graphs. All four 122-window evidence-adapter
warmups completed. Saved requests/results are retained, including interrupted requests
with ambiguous outcomes and provider content-rejection errors.

Restore Alibaba account access before more remote evaluation. Reuse exact recorded
requests; keep ambiguous launches unknown and preserve the original failed ledgers.
Simply restarting a driver will replay cached billing errors. Recovery needs a new
ledger that identifies explicitly denied requests as new attempts, while retaining
their history; do not delete error artifacts or resubmit ambiguous requests.

Structural growth or source-only diagnostic results must not be reported as improved
retrieval or answer quality. The fixed semantic criteria remain unchanged, and v2
remains experimental rather than replacing the default policy.

## Reproducibility and checks

Private sources, calls, input/code snapshots and diagnostics remain outside Git under
`.cache/kg-prompt-2026-11-02/`. The `pilot`, `pilot-v2`, and `source-audit` directories
preserve both trials and the common semantic audit. `matrix-v2` contains the full fixed
evaluation; `matrix-state.json` reports its failed stage. `offline-control` contains the
three replayed control graphs; `offline-35b-diagnostic` contains the complete 35B
structural comparison and retrieval packets. Previous baseline and evidence-only
warmup artifacts remain under `.cache/kg-optimization-2026-11-02/`.

The full matrix records fresh API results separately from imported request caches.
Cached call inventories and their historical latencies are not new API usage or current
serving latency. No production datastore benchmark is claimed.

Verification: 195 focused/adjacent unit tests passed; Ruff passed; strict mypy passed
for the three changed production provider modules. Tests cover prompt routing in all
formats, unchanged source/schema/budgets, checkpoint separation, closed schemas,
per-model construction and rejection of altered evaluation conditions. Existing source,
negation, correction, endpoint and namespace regressions also pass. No storage lifecycle
behavior changed, and no new database integration suite was run for this prompt change.
The continuation also verified the saved snapshot/artifact hashes, complete planned
fact coverage in the visual audit, and unchanged dense evidence for all 23 questions.

## Follow-up: causes of erroneous and disputed claims

The follow-up inspected the saved requests as well as final accepted facts. It covered
all eleven distinct non-supported v2 fact records outside `h-124`, across 27B and 35B,
and the separate eighteen-fact PDF visual audit. This is assistant error analysis,
not an independent human annotation or a new primary score. The private
`error-diagnosis.json` preserves fact IDs, original judgments, source/code hashes,
review reasons and deterministic before/after splitting traces.

### Confirmed mechanisms

1. **Lost table alignment.** The ten visually wrong PDF relations consist of nine
   incorrect reference thresholds/ranges and one incorrect unit. OCR flattened columns
   into lists, omitted the second-row abbreviation, and provided no row correspondence.
   The default compact request supplies numbered text passages without row geometry.
   All eighteen accepted relations cite the same 511-character OCR block. A name and
   value both occurring there is insufficient evidence that they belong together.
   Every one of the eighteen claims has model confidence `1.0`, so increasing a
   confidence threshold cannot distinguish the ten wrong links from the eight correct
   ones in this example.
2. **Source splitting manufactured an incomplete name.** In `h-100`, the original
   DOCX text contains a complete analyte list. Record overflow triggers the bounded
   output-window splitter (30 entities / 40 assertions in the compact format). A saved
   child request starts with `酮、可的松…`: token-limited overlap cut into `醛固酮`.
   The model then declared `酮` as a steroid-hormone entity and linked it as a measured
   target. The source-occurrence validator cannot establish that a matching substring
   is a complete referent. This is a demonstrated input-construction defect, not simply
   a model hallucinating text absent from its input.
3. **Participant and predicate-role mistakes.** One history claim assigns Genoese aid
   to John V, although the text assigns it to Andronikos; John V later receives Ottoman
   help. Two fact records use `joined_in_rebellion_against` with an ally as the target,
   combining joining and opposition into one misleading predicate. A separate pair of
   edges uses `and` between names in a list, adding no meaningful relationship.

### The source judge also rejects supported or ambiguous relations

For the eleven non-PDF records, the assistant review found two uninformative `and`
links, one wrong aid recipient, two predicate/target mismatches and one split-created
fragment; one clear judge false negative; and four disputed judgments involving broad
or ambiguous predicate labels. These categories are descriptive, not replacement scores.

- `Jodi Kramer -> appears_in -> Dazed and Confused` is supported by the text stating
  that an actor plays that character in the film. The judge incorrectly calls this an
  actor/character reversal.
- `war -> participant -> John V` has a meaningful property interpretation; the judge
  instead assumes the predicate means `participates_in`. `has_participant` would be
  clearer, but open-schema labels do not impose that unsupported interpretation.
- `test -> uses_method -> blood draw` follows an explicit source heading. Distinguishing
  sample collection from analytical method improves the graph, but calling this broad
  label contradicted overstates what the source establishes.
- Two `actor -> portrayed_character_in -> character` records have a misleading `_in`
  suffix. The underlying actor/character association is supported. These need clearer
  predicates and explicit film scope, rather than assuming the intended association
  is factually reversed.

This is a semantic interpretation problem, not answer exact matching. Previous
automated supported/contradicted counts must therefore remain provisional. These
findings concern the source-claim diagnostic judge; they do not establish a measured
error rate for the separate, still-incomplete retrieval/QA evaluation.

### Applied fix and remaining optimization

`output-window-v4` prefers Chinese list/sentence delimiters as well as whitespace for
output-recovery splits. It aligns duplicated overlap to a complete boundary, discarding
only a partial overlap prefix already retained by the left window. It rechecks the
overlap token bound after trimming. All original characters, offsets and locations stay
intact, the two-level recovery limit is unchanged, and the version participates in
production extraction checkpoint identity. This change applies to output recovery;
it is not a general Chinese tokenizer and cannot infer word boundaries in arbitrary
separator-free text.

The regression failed before the patch because a child started with `氧皮质酮` rather
than the complete list entry. After the patch, 32 tests passed across output splitting,
record limits, chunking, extraction validation and valid-fact preservation. Ruff and
strict mypy for the affected production module passed. On the actual `h-100` input,
the frozen v3 splitter reproduced the `酮、` prefix; v4 eliminated it at both bounded
split levels with complete character coverage and exact source offsets. No new model
calls were made, so downstream extraction/QA improvement is not yet measured. Frozen
experiment snapshots and scores retain their original implementation.

Next optimizations should address the demonstrated mechanisms:

- Preserve table row, column-header, unit and value associations during parsing;
  do not ask extraction or judging models to reconstruct missing alignment by position.
  Where alignment is unavailable, omit the uncertain graph link while keeping the
  source available for text retrieval. A parser change needs a separate frozen run
  with the same parsed corpus supplied to both retrieval methods.
- Make prompt guidance concrete about argument roles: distinguish ally from opponent,
  actor from character, sample collection from assay method, and a complete entity name
  from a list fragment. Use source-supported atomic relations and specific predicates,
  without a fixed ontology, a minimum edge quota, or invented connectivity.
- Add these positive, negative and ambiguous cases to a separate judge calibration
  audit. Judge the source-supported meaning of open predicates; preserve uncertainty
  when the meaning cannot be resolved. Do not silently change the frozen primary
  rubric or retroactively relabel the original automated scores.

The provider billing block still prevents a fresh semantic comparison. The applied
patch proves the source-splitting defect is removed for the reproduced cases; it does
not prove that the overall graph, retrieval or answer quality has improved.

## Code-path audit: model extraction, regex and reusable chunkers

The subsequent audit traced the production path from ingestion through the provider
and LightRAG mapping. Entities and relations already come from the selected remote
LLM's structured response (`providers/extraction.py`). The local LightRAG callback
serializes those validated records using scoped opaque IDs; it does not run a second
entity/relationship extractor or generate relations with regex.

There were nevertheless two semantic regex interventions: `NEGATION.search` rejected
every positive claim whose cited text contained a negation keyword, and compact
evidence numbering used sentence/abbreviation regexes on paragraphs selected by that
keyword search. Both are now removed. The model interprets claim polarity and the
canonical source spans remain intact. Actual regressions reproduced rejection of
both an unrelated negative sentence and `not only` before removal. This change
eliminates those keyword false rejections; it cannot ensure model-assigned polarity
is correct. Identifier syntax, typed output, source correspondence, endpoint and
authorization checks remain in place. Compact evidence identity is `source-spans-v2`;
the production extraction revision is `v6-model-semantics`, including full-format
extraction. No accepted graph, frozen source or prior evaluation score was changed.

The input-size controls are separate: initial `_split` in `ingestion/chunking.py`,
`fit_source_chunks` in `providers/budget.py`, and output recovery in
`ingestion/output_splitting.py`. The first two still use character-position binary
search with token/UTF-8-budget checks. The earlier v4 boundary patch fixes only output
recovery. Thus boundary handling is not yet a unified library-backed solution. Input
token planning uses `cl100k_base`, while final request preflight uses a conservative
UTF-8 byte bound, not the exact Qwen tokenizer. No source text is silently truncated;
response `length` or record overflow triggers bounded source splitting.

### Structured-response failures are a distinct source of missing graph coverage

For the complete 35B graphs, 126 of 129 roles-control failed windows and 74 of 75
facets-v2 failed windows report invalid structured output; the remaining failures
are provider/transport errors. This does not mean every failure is poor knowledge
understanding. In the v2 saved API response inventory, shape-validation errors include
247 missing `assertions.qualifiers` fields and 94 invalid qualifier-name values.
These are field-error occurrences across responses (including correction/subdivision
calls), not 341 failed windows. The compact schema permits only `condition`, `time`
and `location` qualifier keys, despite open entity/predicate vocabularies. The model
must express other properties as relations. The JSON-object API mode guarantees JSON
syntax, not compliance with that schema. Missing required fields currently fail before
evidence correction. The same inventory includes 19 pure record-overflow responses;
30 entities / 40 assertions can trigger splitting even when the model finishes normally.

These findings motivate separately testing structured-output reliability and record
budget design. They do not authorize silently dropping malformed fields, inventing
qualifiers, changing frozen limits or counting malformed output as successful evidence.

### GitHub implementations inspected and bounded offline screening

Primary sources inspected on 2 November 2026:

- [benbrandt/text-splitter](https://github.com/benbrandt/text-splitter) provides hierarchical
  text/Markdown splitting, configurable token counters, overlap and source indices.
  Its [Unicode fallback implementation](https://github.com/benbrandt/text-splitter/blob/main/src/splitter/fallback.rs)
  uses ICU word/sentence/grapheme segmentation. It does not use an LLM to understand
  biochemical names, and can fall back to smaller units to satisfy a size limit.
- [Docling HybridChunker](https://github.com/docling-project/docling-core/blob/main/docling_core/transforms/chunker/hybrid_chunker.py)
  combines document hierarchy with token budgets, with table-header repetition and
  line-aware table handling described in the [official chunking documentation](https://docling-project.github.io/docling/concepts/chunking/).
  It needs structured document/table information; feeding flattened OCR into a text
  splitter cannot restore lost row alignment. The repository currently uses Docling's
  rendering layer, not this full structural chunking pipeline.
- [Chonkie](https://github.com/feyninc/chonkie) offers token, recursive and LLM-guided
  chunkers. Its existence is not proof that a default configuration preserves this
  corpus's entity or table boundaries; LLM-guided chunking also adds remote calls
  and must preserve the same source/provenance and comparison constraints.

`semantic-text-splitter==0.33.1` was installed only under the ignored research cache;
project dependencies were unchanged and no local inference model was run. A synthetic
list of twelve analyte names repeated four times tested token capacities 32/64/128/256
with bounded overlap, using the same `cl100k_base` callback. Both the existing initial
splitter and the external plain-text splitter preserved all source characters, exact
offsets and token limits, but both still cut inside complete known list items:

| Token capacity | Existing initial splitter: internal-name boundaries | External plain-text splitter: internal-name boundaries |
| --- | ---: | ---: |
| 32 | 25 | 24 |
| 64 | 12 | 12 |
| 128 | 6 | 6 |
| 256 | 3 | 3 |

Counts are boundary occurrences, including overlap starts, not entity-error rates.
When the same names were supplied as correctly structured Markdown list items, the
external Markdown splitter produced zero internal-name boundaries at all four
capacities, with full character coverage, exact offsets and bounded tokens. This
second test changes the synthetic representation; it is evidence for preserving
structure, not a paired RAG improvement. Private experiment results remain unchanged.
Artifacts are `boundary-screen.json` and `structured-list-screen.json` under
`.cache/chunking-research-2026-11-02/`.

The supported direction is model-only entity/relation semantics with a shared,
structure-aware size-control layer, preserving table rows/list items before token
packing and maintaining exact source references. The external library is not promoted
on the basis of its README or these synthetic cases. Replacing all three existing
split paths requires a separately verified integration, including metadata overhead,
multilingual boundaries, oversized units and identical parsed inputs for both RAG arms.

Verification after removing semantic regexes: 108 focused extraction, preservation,
record-limit, output-splitting and model-window tests passed. Ruff passed, and strict
mypy passed for the three affected production modules. No new storage integration
or paid semantic-quality evaluation ran. Alibaba access remains blocked as previously
recorded; these offline checks are not a measured retrieval/QA improvement.
