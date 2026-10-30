# Office and image ingestion implementation plan

**Execution:** Native in this chat, as required by AGENTS.md. Continue through
implementation and verification without a separate approval handoff.

**Goal:** Accept DOC/DOCX, XLS/XLSX, PPT/PPTX, RTF and ODT/ODS/ODP alongside
existing text/PDF formats; retain independently addressable image sources and
use the selected approved multimodal model to produce clearly identified image
interpretations for the same KG.

**Architecture:** Extend the existing parser, source ledger, provider boundary,
and atomic publication path. Modern Office readers preserve paragraphs, cells,
slides and notes. A single sandboxed LibreOffice conversion normalizes legacy
formats; no macros, external refresh or document-selected executable/path.
Image originals remain separate immutable objects owned by the parent source
version and its ACL. Image descriptions are derived evidence, never literal
original-text quotations or human curation. Explicit model capability in Advanced
settings controls image transmission; text-only profiles retain local OCR.

**Spec:** User request in this chat; docs/design.md, docs/contracts.md,
docs/revisions-and-publication.md and original implementation Tasks 8–10.

**Stack:** Existing Python/LightRAG/MongoDB/Neo4j/Qdrant/S3; openpyxl and python-pptx
for modern Office, installed LibreOffice for conversions, existing Pillow and
Tesseract for bounded image decoding/OCR. OpenAI-compatible and native Anthropic
image messages use approved existing endpoints and credentials.

## Invariants and review focus

- Preserve current namespaces, authorization before/after provider calls and
  source reads, immutable originals, publication receipts and curation history.
- Bound ZIP/XML expansion, spreadsheet dimensions, slides, pixels, images,
  subprocess memory/time/output and model context. Never evaluate uploaded
  formulas/macros or retrieve external relationships.
- Keep source provenance through converted files, hidden sheets, slide notes,
  image extraction and chunk overlap. Do not silently lose unsupported media.
- A model/permission change invalidates image work and prevents publication.
  Cache each completed interpretation against image hash and exact profile.
- Image retrieval checks the parent's currently accepted version and ACL;
  deletion/replacement/retention/backup apply to image assets with the source.
- Omit default model fields during serialization to keep old sealed payloads
  readable. Do not relabel image interpretations as source-verified facts.
- Use synthetic fixtures and mocked provider responses. No corpus migration,
  mass retry, provider switch, or further paid diagnostic request.

## 1. Format readers and upload boundaries

Files: ingestion/office.py, ingestion/archives.py, ingestion/formats.py,
ingestion/models.py, ingestion/parsers.py; storage/blobs.py; browser file type
helper and import flows; Dockerfile and dependency locks.

- [x] Write and run failing tests for XLSX sheet/cell/date/formula, PPTX text,
  table/notes, DOC/XLS/PPT and open-format conversion, disguised/encrypted/bomb
  archives, Unicode/encoding and canonical front/back format acceptance.
- [x] Add bounded modern readers and a shared safe archive guard. Convert
  legacy/OpenDocument inputs inside the parser sandbox with a private profile.
  Preserve formulas as source data, never execute them.
- [x] Extend location types and upload magic checks; share browser extension
  inference between folder and single-file upload. Keep unknown files explicit.
- [x] Pass parser/upload/front-end tests and strict static checks.

## 2. Independent image evidence

Files: ingestion/images.py, ingestion/models.py, ingestion/pdf.py,
ingestion/formats.py, jobs/queue.py, ingestion/pipeline.py;
materials/image_sources.py and protected HTTP/console source views.

- [x] Write failing tests for standalone and embedded images, exact parent/page/
  slide association, source-object checksums, retrieval denial across KGs/ACLs,
  replacement/deletion and immutable historical payload compatibility.
- [x] Extract bounded raster sources, preserve their bytes as separate scoped
  artifacts, and retain immutable references in parsed/chunk evidence. Add a
  protected source listing/download path that resolves IDs, never arbitrary paths.
- [x] Expose original image links and source origin in citations/material views.
  Use existing artifact retention/backup ownership rather than new storage.

## 3. Selected-model visual interpretation

Files: providers/profiles.py, configuration.py, completions.py, vision.py,
service.py; model settings UI; ingestion image preparation; support/citation/
retrieval provenance and schema validators.

- [x] Write failing tests for capability-controlled OpenAI/Claude image payloads,
  local OCR fallback, bounded context/output, billing/error redaction, profile/ACL
  withdrawal and checkpoint reuse without repeat calls.
- [x] Add explicit image capability in Advanced settings; known presets can
  declare capability, arbitrary model names do not establish it.
- [x] Produce bounded descriptive sentences through the existing authorization,
  HTTP and usage-budget boundary; record selected model and immutable image
  reference. Validate graph extraction against those stored sentences while
  tagging supports/citations as model interpretation.
- [x] Preserve distinct native/OCR/vision provenance through projection,
  retrieval, graph display and source citations; test rejection of forged status.

## 4. End-to-end proof and local delivery

- [x] Publish synthetic examples of every added document family plus standalone
  and embedded images into one isolated KG with real stores and mocked models.
  Exercise permission, checkpoint/retry, removal and retained-original behavior.
- [x] Run unit/protocol, focused integration, browser, Ruff and strict mypy gates.
- [x] Build and exercise the bounded offline application image with legacy
  conversion, modern Office and image/OCR synthetic samples.
- [x] Update user-facing support/limits documentation, restart idle local services
  after verification, and report any unverified live-model behavior accurately.


Implementation findings: Debian LibreOffice 7.4's PPTX exporter fails on the
synthetic legacy PPT even without inner confinement. Its native ODP export
succeeds. Read ODP directly (text, notes, tables and raster sources) and normalize
legacy PPT to ODP. The Linux image uses a private /run/domain-kg tmpfs, read-only
public temporary directories, native soffice.bin and one bounded first-profile
restart. Additive typed backup/restore transforms retain image hashes and rewrite
raw/preview ownership; per-image caches never establish a complete source stage.


Final verification:
- Unit/protocol: 748 passed; browser logic: 16 passed.
- Final real-store format/image publication gate: 2 passed (17 document samples
  share one KG; image source/checkpoint/ACL/citation/deletion and typed backup/
  restore exercised with synthetic data and mocked completions).
- Graph/citation/material/import/restore regressions passed; the topology fixture
  pins a large context so overlap recovery does not change its support counts.
- Ruff clean; strict mypy clean across 265 source files; distribution inspected.
- Final offline/read-only/non-root/512-MiB container gate passed: 11 Office/image
  cases plus native/scanned PDF, tokenizer, native child isolation and HTTP/MCP.
- Local API/workers restarted. Health 200; anonymous image listing 401; upload
  type helper loaded; no active jobs. Existing 2 ready/12 failed jobs preserved.
- Selected Alibaba profile remains qwen3.6-35b-a3b, image capability disabled;
  local OCR is available. No extra real model diagnostic request was sent.

Known boundaries: raster previews use the first frame (warned for multi-frame
originals); SVG/vector objects and rich multimedia are not guaranteed visual
transcriptions. Legacy XLS reads stored cell values; separate embedded picture
recognition requires XLSX export. Live private-image model quality was not tested.
