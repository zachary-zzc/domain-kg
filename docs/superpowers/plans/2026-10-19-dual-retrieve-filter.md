# Separate retrieval, merge, and 4B evidence filtering

Execution is native in the current chat per the user. This is offline evaluation;
no production changes, deployment, migration, downloads, or external model APIs.
Use writing-plans, test-driven-development and verification-before-completion.

**Goal:** Test independent dense and source-graph recall followed by a bounded 4B
filter, preserving candidate provenance and distinguishing retrieval, filtering,
answering and citation failures.

**Spec:** User request; design/contracts/publication protocol; implementation Task 9.
Existing factual extraction and publication gates remain in force.

## Frozen protocol

- V retrieves six cosine-ranked chunks; graph independently uses the top two
  lexical query seeds and at most two hops of the existing source-only graph.
  Return six graph candidates; explore at most 200 nodes/400 edges. Graph recall
  does not consume the vector ranking. These are hint edges, not verified facts.
- Merge/deduplicate at most twelve IDs, retain V's original three-chunk/1800-char
  answer context in the filter input, then add candidates within the exact
  conservative model request-size limit. Preserve branch ranks and omissions.
- 4B filter returns zero to three source IDs only, ordered for answering. No gold,
  extracted fact, graph summary or rationale enters the answering context.
  Validate IDs, uniqueness, selected-count and 1800-char budget; invalid, empty or
  failed selection falls back to V. This protects structural validity, not semantic
  correctness; valid but wrong filters remain measurable failures.
- Keep the existing 4B answer prompt, three whole chunks/1800 chars, 128 output
  tokens, temperature zero/seed19/thinking off. No automatic citation completion.
- Development: replay eight previous problem cases (p15, two_hop1/2/3 and all four
  three_hop cases). Old scores remain untouched and development is not held-out.
- Then freeze one strategy and test 24 new questions: twelve new PDF target facts,
  eight new synthetic two/three-hop questions, four unanswerable questions. Same
  PDF was previously read; author labels/audit are provisional. Chain templates
  recur, and questions within a chain are correlated. No tuning on new responses.
- Compare pure-vector final top-three with merged/filtered final top-three. Also
  report V-six vs graph-six vs union vs filtered evidence coverage, so gains from
  a larger vector pool/filter are not mislabeled as graph-only gains. This is an
  end-to-end comparison, not a complete graph/filter ablation.
- Preserve raw filter/answer responses, candidates, exact-request cache reuse,
  failures, latency, usage, memory and false candidate drops. Reuse only exact
  successful byte-identical requests with hashes and stated origin.
- Score semantic answer accuracy over all questions, citation completeness over
  every answerable question, conjunction separately. Existing 85% answer/90%
  citation/no-answer-regression-vs-V gate applies only to completed new-question
  evaluation after semantic audit. At n24/answerable20 this needs >=21 answers
  and >=18 citation-complete cases. Failure means no full-PDF construction.
- Up to 96 new chat HTTP attempts and 3600 seconds measured request/stage budget,
  12288 reported output tokens; each call120s, two consecutive timeouts halt,
  five runtime failures halt. Up to five unsuccessful development strategies,
  no sixth. No automatic score-driven retries. Resource limits unchanged: chat
  3.5GB watchdog, embedding1.25GB; CPU2/GPU0/single slot/one resident model.
- Embeddings retain complete-coverage <=24-token window pooling and 1024 dims.
  Reuse exact old source embeddings by text/hash only; disclose baseline limits.

## Tasks and review focus

1. Write failing tests, implement narrow helpers in `eval/fusion_pilot.py` and
   `tests/unit/test_fusion_pilot.py`: independent graph seeds, bounded traversal,
   union keeps vector candidates, unauthorized/out-of-pool filter IDs rejected,
   no partial evidence truncation, conservative prompt sizing and fallback.
2. Implement explicit development/prepare/evaluate/audit stages in
   `eval/fusion_run.py`; create `eval/fusion_cases.json` only after the strategy
   is decided. Freeze/archive imported code, sources, settings and graphs before
   held-out inference. Use ignored `.cache/dual-retrieve-filter/` artifacts.
3. Run real native 4B development, then fresh-question comparison once; inspect all
   responses and report negative findings as well as gains. Preserve prior runs.
4. Verify unit/protocol, Ruff, production mypy and artifact integrity; author
   self-review per native-only instruction. Record in `docs/dual-filter-report.md`
   and status notes; retain original quality gate. Stop owned models and confirm
   ports closed. Construct the full PDF only if the new gate actually passes.

The plan is kept immutable once frozen; the report/ignored ledger records task
completion. No checkbox is used to imply model quality before actual results.
