# Integrated local PDF parsing

Interpret “整合进去” as integrating mature local PDF/OCR into normal ingestion.
The current PDF path has two complementary packages, not duplicate extraction
frameworks: pypdf validates originals; Docling extracts native text. Scanned pages
currently produce `OCR_REQUIRED`, and explicitly enabling OCR always fails.

Reuse Docling's installed renderer and local Tesseract with English and simplified
Chinese data. Keep PDF ownership in one adapter; no new Python PDF/OCR framework,
cloud OCR, runtime model download or paid model request. Native text stays exact.
OCR runs automatically on scans and image-dominant mixed pages, with native text
masked before recognition to avoid duplicate evidence. Preserve physical pages,
source namespaces, originals and canonical span validation. Mark recognized text
and warn that it needs review. A missing/broken engine must give an actionable
failure, not silently publish a blank scan. Explicit `ocr=False` remains available.

Implementation and proof:

- [x] Add failing scan/mixed/native regression cases and OCR provenance checks.
- [x] Consolidate the PDF adapter, enable bounded offline fallback and invalidate
  old parsing checkpoints. Bound rendered pixels and OCR subprocess output/time;
  monitor child memory and terminate the whole process group on cancellation.
- [x] Provision Tesseract and language data in the application image and extend
  its offline synthetic runtime probe. Document native prerequisites/limits.
- [x] Run parser/chunk/source-snapshot and format-publication checks, static
  checks, and the bounded image runtime gate if the local build is available.

Do not retry or migrate user materials, change provider settings, or make further
paid diagnostic calls. The earlier six-request authorization is exhausted.

Verified:

- Reproduced empty scanned-PDF output and the missing OCR branch before the fix.
  Real local OCR now recognizes the public bilingual fixture. Mixed-page tests
  preserve both native lines exactly once and scan text at physical page 2.
- 711 unit/protocol checks passed, including missing/failed OCR, bounded output
  and timeout, child memory accounting, cleanup after parent exit, legacy source
  serialization, source-span offsets and translated diagnostics.
- 23 focused parser/chunk/source-snapshot checks passed. The format publication
  gate then passed with eight documents (native PDF, scanned PDF, DOCX, HTML,
  TXT, Markdown, CSV and JSON) in one KG, real MongoDB/Neo4j/Qdrant readiness
  receipts and simulated model responses. The scan retains OCR provenance and
  publishes as `ready_with_warnings`.
- Ruff, strict mypy (254 source files), diff whitespace and wheel/source package
  boundary checks passed. No Python PDF/OCR dependency was added. A small typed
  stub describes the installed Docling native renderer's public API.
- Built `domain-kg:pdf-ocr-local` and passed `scripts/check_image.py`: network none,
  read-only root, UID/GID 10001, 512-MiB memory/swap ceiling, 128-MiB temporary
  filesystem. Native PDF citations, offline bilingual scan OCR, worker CLI,
  liveness and unauthenticated MCP rejection passed. Report:
  `.cache/pdf-ocr-image-report.json`; image
  `sha256:a41295978b3522e7de4f931665200eea5c7b5c40257656b562474b797f869219`.
- Restarted the existing local API/workers after confirming no active jobs;
  `/healthz` returned 200. Failed jobs were not retried. A normal OIDC-authorized
  read of the existing accepted view succeeded; it currently has zero visible
  nodes, so that read is not evidence of private-corpus extraction quality.

OCR output is approximate and requires checking against the original page.
Automatic OCR targets image-only and image-dominant mixed pages; rich diagrams,
equations and arbitrary image understanding remain outside this adapter. Native
text remains exact. Engine/language-data upgrades require a parser-profile bump
and explicit reprocessing; accepted original/ledger/projection artifacts remain
immutable. No additional paid API request or user-material publication occurred.

Commands:

```bash
.venv/bin/python -m pytest tests/unit tests/protocol -q
.venv/bin/python -m pytest tests/integration/test_parsers.py tests/unit/test_chunking.py tests/unit/test_parser_source_snapshot.py -q
.venv/bin/python -m pytest tests/integration/test_document_formats_pipeline.py -q
.venv/bin/ruff check src tests/unit/test_pdf_ocr.py tests/integration/test_parsers.py tests/integration/test_document_formats_pipeline.py scripts/check_image.py scripts/image_probe.py scripts/check_distribution.py
.venv/bin/mypy src/domain_kg
uv build --offline
.venv/bin/python scripts/check_distribution.py dist
docker build --memory=1g --cpu-quota=100000 -t domain-kg:pdf-ocr-local .
.venv/bin/python scripts/check_image.py domain-kg:pdf-ocr-local --output .cache/pdf-ocr-image-report.json
git diff --check
```
