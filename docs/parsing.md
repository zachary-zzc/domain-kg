# Laptop parsing profile and source ledger

The local PDF adapter uses pypdf for native text and embedded image placement.
Docling renders only pages needing OCR in a short-lived process, followed by
preinstalled local Tesseract (English/simplified Chinese). Rendering exits before
OCR starts. No model download or cloud OCR request is part of parsing. Native
Office/text decoders and sandboxed legacy conversion retain the same source
ledger; see [supported formats](console.md).

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
limits apply; the parent monitors the whole process group's RSS with a default
1024 MiB ceiling for every format. Linux also uses a per-process address-space
limit equal to the configured RSS budget plus 256 MiB. These are parser bounds in addition to the
documented service-container budget, not a claim about total laptop RAM.

Inputs are limited to 50 MiB and PDFs to 500 pages. Archive member count,
expanded size, XML size, JSON depth/pointers, and output size are bounded. ZIP
traversal/symlinks/duplicates, XML entities, duplicate/nonfinite JSON keys/values,
malformed and encrypted files are rejected. HTML never executes scripts or
fetches links/images. Legacy DOC/PPT/RTF/ODT use preinstalled sandboxed LibreOffice;
no arbitrary converter command or server path is accepted.

Image-only/image-dominant pages use local OCR when enabled, with explicit OCR
provenance and warnings. Disabling OCR may leave scanned pages with no searchable
text. Missing engines, malformed/encrypted files and resource limits remain
explicit failures; unreadable embedded PDF images can warn while preserving native
text and the complete original.

## Parser configuration

Every format defaults to **1024 MiB (1 GiB)** for the entire parser process group,
including OCR and conversion children. These settings control local parsing;
model API/context/token settings are separate.

| Setting | Default | Allowed range |
| --- | --- | --- |
| `memory_limit_mib` | 1024 | 64–8192 MiB |
| `timeout_seconds` | 120 | 1–300 seconds |
| `max_pages` | 500 | 1–500 PDF pages/presentation slides |
| `max_expanded_bytes` | 104857600 | 1–104857600 bytes; browser form uses 1–100 MiB |
| `ocr` | true | true/false |

The server's `DOMAIN_KG_PARSER_CONFIG` selects a JSON file using the schema in
[`config/parser.example.json`](../config/parser.example.json). Copy it to a private
writable directory and point both API and workers at the same absolute path.
Leaving the variable unset uses the defaults and makes the browser form read-only.
A missing file at a configured path starts with defaults; saving creates it with
mode 0600. Invalid, oversized or unreadable configuration fails explicitly.

In the local workspace launcher the path is already configured as
`secrets/local-browser/parser.json`. Open **KG settings → Advanced settings →
Document parsing** to edit it. The settings affect **all KGs on that server**, so
saving requires a human platform operator who also owns the displayed KG. API
and workers must share the same mounted file when deployed separately. The API
uses a lock, expected revision and atomic replacement; a stale conflicting save
leaves the accepted file unchanged. MCP exposes the same policy through
`domain_kg_get_parser_settings` and `domain_kg_configure_parser`.

The worker captures the current file once at the start of each job execution,
including retries. That execution keeps one immutable pipeline and configuration
hash; a save applies to the next execution without restarting the worker. Existing
jobs are not automatically retried, and saving makes no model request. Raising
the budget permits higher peak RAM use; parser and any container/host limits both
apply, so the host/container must have room for the chosen parser budget.

The JSON `target_tokens`/`overlap_tokens` fields retain the existing `ParseOptions`
contract; this form configures parsing resources. Production chunking continues
to follow the selected model's adaptive context-window policy.

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
