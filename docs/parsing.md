# Laptop parsing profile and source ledger

The default local profile uses `docling-parse 7.22.3` for native PDF text and
bounded deterministic decoders for DOCX, UTF-8 text/Markdown, inert HTML, CSV,
and JSON. Docling's [native PDF component](https://github.com/docling-project/docling-parse)
provides physical pages and text cells without OCR/layout models. The locked
environment contains no Torch/OCR model dependency or model download step.
This profile does not establish OCR/layout quality for scanned or complex PDFs.

Native PDF locations use one-based physical page numbers and immutable block
IDs. DOCX uses body paragraph indices, heading context, or table/row indices.
CSV rows are one-based records; JSON pointers escape `~` and `/` per RFC 6901.
Quotes and offsets refer to immutable decoded source blocks, preserving their
Unicode text and whitespace. They are character offsets within a block, rather
than byte positions in a PDF/ZIP container.

Each original is checksum-verified before parsing in a separate process. On
macOS that process has a network-denying kernel sandbox, can read runtime/source
code and its private temporary original, and can write only within that temporary
directory. Python networking is disabled too. Runtime, file-size and descriptor
limits apply; the parent monitors macOS RSS with a 384 MiB ceiling. Linux also
uses a 512 MiB address-space limit. These are parser bounds in addition to the
documented service-container budget, not a claim about total laptop RAM.

Inputs are limited to 50 MiB and PDFs to 500 pages. Archive member count,
expanded size, XML size, JSON depth/pointers, and output size are bounded. ZIP
traversal/symlinks/duplicates, XML entities, duplicate/nonfinite JSON keys/values,
malformed and encrypted files are rejected. HTML never executes scripts or
fetches links/images. Legacy `.doc` requires export to DOCX/PDF; no arbitrary
converter command or server path is accepted.

Image-only pages emit `OCR_REQUIRED`; the native profile returns no invented
text. Explicit OCR requests need a separately approved parser profile and are
currently refused. Heavy OCR/model quality checks are outside this laptop run.

Chunking measures `cl100k_base` tokens, targets at most 700, and permits at most
100 overlap tokens. It preserves whole small table rows and source locations,
splits long text only at Unicode character boundaries, and uses versioned stable
IDs. Newlines between separate source blocks are assembly separators; each span
still matches the exact text in its own block.

Preparation commits immutable block/chunk metadata in the selected Mongo
authority database and complete checksum-backed snapshots in S3. Large block
text stays in its artifact; bounded chunk text is indexed separately. Every
artifact/reference includes organization, KG and source-version ownership.
Resume verifies stored bytes and does not duplicate ledger records. These
records remain preparation data: only the later accepted manifest determines
which snapshots/attempts can contribute search evidence.

`python3 scripts/smoke.py --task 8 --keep-services` uses tiny public fixtures.
It verifies PDF pages, DOCX paragraphs/tables, multilingual exact spans, actual
token counts, scan warnings, malformed/encrypted/501-page input, archive/XML/JSON
rejection, kernel network denial, and simulated timeout/memory limits. Public
fixture originals and their generator are in `tests/fixtures/materials` and
`scripts/generate_test_materials.py`.
