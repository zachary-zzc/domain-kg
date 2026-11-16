# Connected, content-derived graph extraction

**Goal:** Extract specific, source-supported relations and their endpoints without
isolated automatic graph nodes or a fixed built-in type vocabulary.

**Architecture:** Extend the existing domain schema and canonical extraction
boundary. Built-in profiles become versioned open vocabularies; stored custom
schemas retain closed behavior by default. Prompts enumerate facts before nodes.
Prune standalone observations after claim validation and before LightRAG mapping.
Source chunks, independent supports, curation, permissions and immutable publication
remain authoritative. Predicate strings remain property values, never database code.

**Spec:** User request, docs/design.md, docs/contracts.md,
docs/revisions-and-publication.md, implementation Task 9.

**Execution:** Native in this chat, as requested by repository instructions.

## Review focus

- Unknown source-derived labels pass only open schemas; closed endpoint constraints remain.
- Rejected edges cannot leave automatic standalone nodes or dangling endpoints.
- Empty fact sets retain searchable source chunks; no fabricated connectivity.
- Legacy schema serialization and accepted artifact hashes remain readable.
- Prompt/schema changes invalidate extraction checkpoints without changing accepted data.

## Tasks

1. Add failing regression tests for open labels, closed restrictions, isolation,
   invalid evidence, legacy schema reads, and provider request formats.
2. Extend graph/models.py and graph/schema.py; update extraction validation and
   provider prompts/compact schemas; rotate extraction checkpoint identity.
   Route record-count overflow through bounded splitting and map native types
   opaquely so LightRAG punctuation rules cannot discard canonical entities.
3. Run focused unit tests and real-store publication/resolution/lifecycle checks;
   run Ruff and strict mypy. Fix regressions in affected behavior.
4. Parse the requested Downloads corpus with production parsers. Compare frozen
   baseline and revised extraction using the same approved provider, source windows,
   and settings. Record nodes, edges, isolates, vocabulary, rejects, usage and limits
   in ignored local artifacts. Review representative relations against their spans.
5. Document measured results and how existing accepted materials adopt the change
   through normal re-ingestion/publication; leave unrelated edits intact.

Verification: `.venv/bin/pytest tests/unit/test_open_extraction.py
tests/unit/test_extraction_validation.py tests/unit/test_compact_extraction.py -q`,
the affected extraction unit files, and the Task 9 / publication / source-removal
integration tests. `.venv/bin/ruff check` and `.venv/bin/mypy src/domain_kg`.
