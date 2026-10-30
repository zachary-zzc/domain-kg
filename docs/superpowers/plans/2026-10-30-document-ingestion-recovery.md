# Document ingestion recovery

The reported PDF, DOCX and HTML jobs passed parsing and failed in extraction.
Offline parsing of the 11 preserved originals confirmed readable text in each;
one PDF also has pages requiring OCR, which is not configured locally.

Approved live diagnosis used exactly six requests. A 357-token PDF recovery window
still hit the old 2,048-output-token ceiling. DOCX/HTML returned relationships
whose cited passages omitted an endpoint; some indexed DOCX endpoints also did
not identify existing entities. A named-endpoint DOCX response contained nine
entities and six relations, four independently valid and two ungrounded.

The user explicitly chose to retain independently validated facts, discard invalid
facts and show counts, rather than reject the entire file. No evidence is added,
no qualifier is removed to force a relation through validation, and originals
remain intact. Malformed, refused or truncated output remains a failure.

Implemented:

- Reserve the complete one-shot evidence correction prompt during model-window
  preparation; verify both actual requests before sending.
- Balance output recovery by source tokens and split a dominant long passage;
  preserve locations, exact offsets, overlap and the two-level request bound.
- After one correction, validate each entity and whole relation independently.
  Discard ambiguous entities, dependent relations and other ungrounded claims.
- Preserve server-generated rejection counts through LightRAG, checkpoints,
  resolution, output splits and atomic verified publication. Show translated
  counts under `ready_with_warnings`; model output cannot set these warnings.
- Use existing named endpoints for new browser configurations and 8,192 output
  tokens in the Alibaba example. The local selected/default profile was updated
  through the owner/operator setup service, retaining its embedding space,
  credentials and unlimited local daily budgets. A private configuration backup
  is retained outside Git; other accepted views were not rewritten.
- Persist an allowlisted output-limit diagnostic separately from generic provider
  failures. Translate OCR and discarded-fact warnings.

Verification:

- 702 unit/protocol tests passed.
- 17 real-store integration tests passed: extraction checkpoint recovery,
  independently validated partial publication, seven-format publication in one
  KG, adaptive windows and scoped job diagnostics.
- Three additional real-store settings/format tests passed after the named
  protocol change. The earlier parser/chunk/window/output baseline passed 32.
- Ruff and strict mypy passed (253 source files); diff whitespace checks passed.
- The saved live DOCX response was replayed offline through the production client,
  LightRAG and unchanged canonical validator: nine entities, four relations and
  two rejected relations. This made zero additional API calls and published no
  user corpus graph.

The seven-format gate covers PDF, DOCX, HTML, TXT, Markdown, CSV and JSON using
synthetic provider responses and real MongoDB, Neo4j and Qdrant readiness checks.
The old failed jobs were not automatically retried. Full private-file live reruns,
including the PDF with the increased output allowance, were not performed within
the six-request diagnostic authorization. Scanned/image-only text still needs OCR.

Commands:

```bash
.venv/bin/python -m pytest tests/unit tests/protocol -q
.venv/bin/python -m pytest tests/integration/test_extraction_checkpoint.py tests/integration/test_document_formats_pipeline.py tests/integration/test_model_windows_pipeline.py tests/integration/test_job_inspection.py -q
.venv/bin/ruff check src tests/unit/test_compact_extraction.py tests/unit/test_extraction_correction.py tests/unit/test_extraction_warnings.py tests/integration/test_extraction_checkpoint.py
.venv/bin/mypy src/domain_kg
git diff --check
```
