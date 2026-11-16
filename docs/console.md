# Administration console

The Sources workspace (`/admin/kgs/{kg_id}/materials`) separates Published,
Processing, Needs attention, and Removed views. Add sources opens one inline panel
with multiple-file, folder and plain-text inputs. Batch selection and outcomes
stay in this workspace. Deletion and model-backed processing use an accessible
page-contained confirmation dialog tied to the reviewed target revisions and
labels; changing the selection requires review again. Successful jobs link to their source and the shared KG;
source status pages also expose recoverable removal with a current revision guard.
Legacy folder, add and pending/removed links redirect here. Task history is a
secondary screen for source processing and other KG operations, filtered by state.

Selection now starts a metadata/local-byte preflight automatically; it never
uploads until the user chooses Upload and construct KG. A named choose button
shows the selected folder/file count instead of relying on the operating system's
file-input text. A completed review collapses selection controls to keep the
primary action visible. The default table excludes skipped/unchanged files;
counts remain visible and the Skipped view preserves each reason. Filename/path
search and 20-row pages affect display only: upload always uses the full reviewed
manifest, as its button count states. Invalid rows remain visible, including
control characters in filenames that previously invalidated the entire manifest.

Source/task lists retain their service-owned opaque cursors and offer 10/25/50
rows per page, visible-row counts, Next and First page. Changing page size starts
from the first page. An expired cursor offers a validated local restart link,
without reflecting private queries or changing the error/authorization contract.

Each new file import has a durable actor-owned run. Its URL contains `import_id`;
reloading or navigating back restores only that selection, without mixing global
job history below it. Recent run heads show timestamp and file count, while old
jobs without run records remain in Task history. Accepted jobs continue in the
background. Saved-run HTML loads only an actor-bound head (date and file count),
then the existing authorized API restores file/job details asynchronously. A
temporary failure offers Refresh status; access denial clears private details.
Completed uploads have an explicit completed label and no pause control.
Unuploaded files need the same originals reselected; an admission
whose response was lost resumes its saved command without sending bytes again.
No private filenames, source text or credentials enter localStorage.

Status polling isolates failures per file, limits concurrent status requests to
three, and reconnects after temporary failures. `repair_required` is terminal for
polling and explicitly retryable. A status failure after admission refreshes its
run instead of asking for another upload. Access changes clear protected content
and revalidate the current route; ordinary network/backend errors preserve the
current view. Batch selection drops disappeared, disabled or revision-changed
rows before submission.

Run progress no longer resolves the whole graph's source eligibility for every
item admission/read. Item reads run in groups of four and still authorize exact
job/source references, including before status-unavailable fallbacks and again
before return. Monitoring pages recheck the actual displayed metadata after
rendering; published source/evidence pages retain their graph scope fences.
Unrelated graph publication can therefore continue during progress reads, while
late source exclusion or permission revocation still denies the response.

PDF native extraction now uses pypdf without the large Docling glyph/shape
geometry allocation. Docling rendering exits before OCR starts, within the same
offline sandbox. All formats default to a 1 GiB process-group RSS limit, including
conversion, rendering and OCR children. Advanced settings > Document parsing
exposes server-wide memory, timeout, page/slide, expanded-content and OCR controls
to a human owner/operator when `DOMAIN_KG_PARSER_CONFIG` selects a writable file.
New job executions and retries load a fixed configuration; saving requires no
restart and does not change running jobs. Stale conflicting forms are rejected.
The same service is exposed through MCP `get_parser_settings` and `configure_parser`.
See [configuration bounds and deployment](parsing.md#parser-configuration).
Timeout, expanded-output bounds and group termination remain enforced.
Before this increase, two previously failed
private originals were parsed locally without model requests under the old
384 MiB bound: a 17,581,328-byte,
13-page PDF produced 549 text blocks and 21 images at 253.6 MiB peak; a
5,270,936-byte, 14-page PDF produced 222 text blocks and 22 images at 294.9 MiB,
with OCR/image warnings retained. Compressed file size alone does not predict
rendering memory; encrypted, damaged or unusually expensive documents can still
fail the resource bounds explicitly.

Extraction errors now distinguish deadline, connection failure, blocked content
and invalid structured output through allowlisted diagnostics. No provider body,
exception string or source excerpt is returned. The six historical generic
extraction errors have no saved detail from which to establish an exact cause;
they remain historical failures until an explicit retry. Validation did not make
new paid provider requests or automatically retry the user's corpus.

The 2026-10-31 verification passed 802 unit/protocol cases, 37 browser JavaScript
checks, focused real-store import/batch/job/deletion/restoration and console-role
checks, Ruff, strict mypy, and an offline wheel/source-distribution build. The local
API and static assets returned HTTP 200 after restart. Chrome acceptance was subsequently completed for OIDC login, desktop and
390-pixel layouts, PDF/DOCX/TXT multi-file upload through the browser picker,
independent empty/unsupported-file skips, persisted-run reload, batch retry,
page-contained delete confirmation, recoverable batch deletion/restoration and
shared graph ownership. The three new documents shared two grounded entities;
withdrawing two documents retained the independently supported graph. The native
directory picker could not be populated by the browser automation interface.
A subsequent manual Chrome selection succeeded: 76 files with nested paths were
reviewed as 16 eligible and 60 hidden/skipped, with zero errors. That user's
originals were not uploaded during validation. Relative folder paths, matching
and recovery also remain covered by JS/service tests.
The temporary fixture uses mocked provider HTTP and separate synthetic stores;
no private corpus was retried and no paid model request was made. See the [implementation record](superpowers/plans/2026-10-31-source-workspace.md).

The subsequent [import usability acceptance](superpowers/plans/2026-10-31-import-usability.md)
passed 833 unit/protocol cases, 42 browser JavaScript cases, 13 real-store
import/workspace/job cases, Ruff, strict mypy and offline builds. Chrome uploaded
all 46 eligible synthetic files from a paginated review and verified all 46 ready.
The final two-file preview additionally verified head-first reload, asynchronous
status restoration, completed controls and no console errors. Both previews
cleaned their synthetic namespaces. The real local workspace was restarted with
zero active jobs and its authenticated Sources page verified afterward.

Supported ingestion covers native/scanned PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, RTF,
ODT/ODS/ODP, HTML, TXT/Markdown, CSV/TSV/JSON, and PNG/JPEG/WebP/GIF/TIFF/BMP.
Text readers accept UTF-8/BOM and BOM-marked UTF-16/32; GB18030 fallback is strict
and visibly flagged. Originals retain their original bytes. Scan pages and image-dominant mixed pages use local English/simplified
Chinese OCR automatically. The console identifies recognized text with a warning
to check the original page; missing OCR dependencies and recognition failures have
actionable diagnostics. Encrypted or damaged originals remain explicit
failures, except unreadable embedded PDF images: readable text is retained with
`PDF_IMAGE_UNREADABLE` rather than discarding the whole document. A readable original can still fail later during model extraction or
publication, so parser success does not imply a ready graph.

Model output truncation now retains its specific safe diagnostic and identifies
the Advanced settings output allowance. Model windows reserve the evidence
correction prompt, and output recovery balances source tokens, including unusually
long PDF lines or DOCX paragraphs. After one evidence correction, each entity and
relation is validated independently. Valid facts can publish with
`ready_with_warnings`; rejected entities and whole relations (including invalid
qualifiers or endpoints) are omitted, without adding or repairing evidence. The
console shows the discarded counts summed across chunks and retains the complete
original. Malformed, refused or truncated responses remain explicit failures.
Historical jobs that stored only a generic code retain that history
until an explicit retry produces a new result.

The format gate in `tests/integration/test_document_formats_pipeline.py` uploads
the text/PDF/Office families through the compact extraction protocol with synthetic provider
responses, then verifies graph/vector/lexical receipts and the shared accepted KG
view in real stores. It proves the file-to-publication path, not a live provider's
quality on arbitrary private documents.

Source and job lists support bounded batch management. Editors select sources to
delete or process, restore retained removed sources, and retry or cancel selected
jobs. Selection survives job-list refreshes; select-all applies to the current
page, and retryable-only selection excludes active and completed jobs. Each batch
accepts 1–100 distinct targets. Processing and deletion review the selected names
and explain model costs or recoverable withdrawal before submission.

`POST /admin/kgs/{kg_id}/batch` uses the existing same-origin session/CSRF checks
and redirects back to Sources with `view`, `batch_id` and optional `import_id`.
The inline result panel separates submitted background jobs, completed actions,
skipped items and failures. The old batch result URL redirects to Sources. MongoDB
retains the immutable command and per-item receipts; resubmitting the same command
does not repeat completed work. Changed input with the same key conflicts. A
submission lease fences concurrent requests; interrupted batches can continue
their unfinished items. Results are visible only to their initiating actor and
are checked again against current source permissions, including their labels.

Pending-source processing resumes the source's existing recoverable job. Accepted
source reprocessing copies the authorized preserved original through the normal
owned upload/replacement flow, retaining metadata and the previous accepted
version until verification/publication. Revision guards reject metadata changes
during copying. An existing active replacement is skipped rather than knowingly
queued again. Deletion uses the existing immediate visibility fence and retention
workflow; it does not permanently purge files or remove another source's supports.

All accepted materials in one KG contribute to the same published graph view.
Grounded shared identifiers produce shared canonical entity IDs; each material
keeps its own observations and assertion supports. Equal names alone do not prove
identity, so the graph can contain disconnected components until explicit curation
merges the corresponding mentions. Batch management does not change this policy.
The current compact `source_refs_v1` and `source_names_v1` extraction contracts
return names, types and evidence references without explicit entity identifiers.
Those profiles therefore require explicit curation to consolidate cross-document
mentions; sharing one KG does not guarantee a single connected component.

The session-bound shell at `/admin` uses the configured external OIDC login.
It lists authorized KGs, accepted/retained materials, scoped search results and
source/manual citations, team/role management, curation/history and owner/operator operations. No local
password or alternative identity provider is bundled.

Current session membership and KG policy are checked before catalog access and
again before returning rendered HTML. HTML templates autoescape source-derived
labels. Pages use `Cache-Control: no-store`, same-origin content restrictions,
disabled HTMX history/script/eval behavior and a reload on restored browser pages.
Logout requires the current session's CSRF token and exact origin.

Forms are limited to 64 KiB and 100 scalar fields; ambiguous duplicate fields,
invalid encodings and unexpected command fields are rejected without echoing
private input. Files will use the existing streamed checksum-verified upload API.
The console adds no Docker service or frontend build process.

Jinja2 is pinned to 3.1.6. HTMX 2.0.11 is served locally; its published SHA-384 was
verified and its upstream license retained in `console/static/vendor/`.

The shell and existing signed HTTP/MCP regression passed four real-store checks
in 26.43 seconds. The full unit/protocol suite passed 173 tests, including 11
console form cases; Ruff and strict mypy passed. Representative browser flows and
external OIDC acceptance remain later gates.

Material and search screens pin the shared accepted read scope before service
reads and revalidate it after HTML rendering, alongside the current session and
policy. Explicit unverified opt-in retains its provenance labels. Reader removal
forms are hidden and forged submissions receive the same denial as the JSON API.
The read/navigation/permission case and withdrawal-after-search race passed two
real-store checks in 34.92 seconds. Editor upload/replacement/restoration workflows
and the complete browser acceptance gate remain in progress.

The job list filters each receipt through current source access before applying
its visible page limit. Opaque cursors pin principal, policy, visibility and the
published view. Job reads verify the canonical version's namespace and material
ownership; missing or mismatched references fail with a consistency error.
Editors can cancel/retry through the shared durable queue; readers cannot.
Status refresh uses local HTMX with fresh authorization. Active lists poll every
five seconds and job details every three seconds. Temporary failures preserve
context; authority changes clear protected content and reload the current route. The six-case job
service/browser gate passed in 17.86 seconds; durable recovery and signed HTTP/MCP
regression passed 14 cases in 53.58 seconds.

Editors can submit uploaded files or inline text, replace the current active
version and edit metadata through explicit typed forms. The file picker computes
SHA-256 and sends bytes to the existing owned-upload API; no filesystem path is
accepted. Replacements retain the prior accepted source until publication, and
metadata-only changes avoid model calls. Forged actor fields are rejected before
job admission. The source form flow passed its dedicated 63.02-second check and
a four-case source/workspace/job regression in 79.09 seconds. The JavaScript
syntax check passed; the full browser acceptance remains separate.

Editors also have explicit pending/removed material metadata views. These views
check current KG role and material ACL, validate canonical ownership, exclude
purged records, and return no original text or graph evidence. Their cursors pin
the current actor, state filter and accepted visibility/view. The shared JSON
management endpoint enforces the same permissions. Restoration uses the existing
lifecycle service: a retained ready source can reopen immediately; an unpublished
source stays hidden while its reconstruction job verifies and publishes.
The five-case management/restoration/workspace gate passed in 110.02 seconds;
the dedicated browser/JSON parity and reconstruction check passed in 51.93 seconds.

Organization administrators can admit external human/service identities, manage
groups and membership, change organization roles and suspend accounts. No
credentials or invitations are generated. KG owners manage role grants and
material ACLs with current policy revisions; material ACLs still narrow KG
membership. Owner forms use validated subject identifiers and do not bypass the
administrator-only organization directory. All actors and organizations come
from the current server-side session.
The team/identity/grant regression passed 20 real-store checks in 26.47 seconds,
including malformed input, service suspension, last-admin/owner safeguards and
revisioned grants. The unit/protocol suite passed 174 tests; Ruff and strict mypy
passed.

Node inspection now follows source/version and unverified filters, with bounded
visible neighborhoods and support-qualified relationship links. Editors load the
current revision for an exact source scope, or an owner explicitly selects a
public override. Typed node/relation fields submit immutable pending revisions;
accepted fields remain unchanged until publication. History renders escaped
before/after values and provenance, distinguishes pending from accepted work,
and lets owners append compensating rollback commands under current authority.
The five-case curation/scope/read-fence/competing-publication gate passed in
221.99 seconds. The unit/protocol suite passed 174 cases in 6.54 seconds, and
Ruff/strict mypy passed. Docker hard limits remained 1.476 GB with swap disabled.
Identity/visibility previews and the operator console are still being added.

Identity and visibility forms now preview the actual typed merge/split/suppression
impact before submission. Merge/split retains explicit original IDs, disjoint
partitions and each original's revision guard. Visibility restoration is available
from authorized suppressed history; public changes require an owner. These
metadata/visibility operations and relationship tags require no model calls.
History's explicit `include_unverified` opt-in is shared by HTTP and MCP and bound
to its cursor, while default imported facts remain excluded.

Owner operations measure graph/vector readiness against accepted receipts and
queue real source repair jobs. The separate operator view lists registered backend
IDs and performs selected capability checks with current CSRF and operator access;
it displays no connection strings, credentials or KG evidence. The four-case
import/identity/visibility/operator/history regression passed in 220.59 seconds.
The unit/protocol suite passed 181 cases in 5.49 seconds; Ruff and strict mypy passed.

Browser forms now stop consuming the request stream at the 64 KiB ceiling instead
of buffering oversized bodies first. Invalid encodings fail before consuming the
body. The bounded-reader and existing form regression passed 13 checks in 0.06
seconds. Owner readiness also includes accepted unverified imports; checking their
projection integrity does not change their fact status.

The repeatable Task 17 laptop gate is:

```bash
uv run python scripts/smoke.py --task 17 --keep-services
```

It uses synthetic fixtures and the same four capped databases, with no added
model or frontend service. `tests/evaluation/test_console_flows.py` exercises five
current roles, including an organization administrator without a KG grant and an
operator without KG access. `tests/evaluation/test_console_browser.py` is an
explicitly opt-in temporary browser fixture: it runs only with
`DOMAIN_KG_BROWSER_PREVIEW=1`, binds loopback port 28080, uses synthetic namespaces
and real session records, and disappears with the pytest process. Its helper
routes are not part of the installed application. It does not establish real
external OIDC acceptance.


The complete Task 17 runner passed 43 checks in 354.84 seconds; 183 unit/protocol
checks passed in 5.60 seconds. Ruff and strict mypy passed. The real laptop browser
verified catalog, keyboard search, source citation navigation, source-qualified
node/edit forms, owner readiness, administrator controls, reader denials and
logout followed by Back. Tab first focused the visible skip link, whose activation
moved focus to the main content. The optional browser file-picker walkthrough
remains unverified: a long browser interaction outlasted the first fixture, and
the controller then timed out on recovery. That fixture reported a timeout
failure; its corrected restart stopped cleanly. API upload, checksum verification
and publication passed their integration flow. No real external OIDC browser
acceptance is claimed.

Owner operations now include retained backup admission with an accepted-revision
check and bounded retention. A completed backup job links to manifest/part
inspection and current-owner-only attachment downloads; damaged bytes are denied
before HTTP success. Run the model-free native worker with `--backup-only`.
The focused backup/operations/jobs/worker regression passed four checks in
55.68 seconds. The synthetic preview also completed a real tiny queued backup
through its dedicated backup worker, without a model call. Restore remains pending.

Owners can also queue a scoped JSONL, CSV or GraphML export from operations.
Material selection constrains every field; imported-unverified evidence requires
an explicit checkbox. A completed export job links to the scope summary and a
checksum-verified attachment. The native `--export-only` worker calls no model.
Access, visibility and publication changes invalidate older files. The dedicated
export/backup/owner transport regression passed four checks in 42.33 seconds;
source filtering, withdrawal and import trust were verified separately.

The synthetic laptop preview now uses one `--operations-only` worker for both
backup and export. Both controls progress to their result pages without a model
call; ingestion work is left for a separate worker. The preview remains a bounded
synthetic fixture and expires after ten minutes.


Advanced settings include **Image understanding** and an image token reserve.
Enable capability only when the chosen chat model accepts images. Known OpenAI
and Claude presets declare image input; arbitrary/local model names do not imply
capability, and existing profiles keep it disabled. Enabling it approves sending
isolated previews to the selected chat endpoint. Each image has a source/version/
profile-bound checkpoint; retries reuse completed descriptions. Billing, revoked
access, profile changes and invalid/truncated outputs cannot publish partial work.

Materials show an **Image sources** gallery with parent part/page/slide locations
and original downloads. Citations show the referenced preview and original link.
Image descriptions are labeled **Model interpretation** in context, search,
citations and the graph legend. These are model observations, not source-verified
truth. Native text and local OCR remain distinguishable from visual interpretation.

Office macros and external links are never executed or fetched. Formula text and
stored cached values are source data. Encrypted/damaged/over-budget files fail with
a safe diagnostic. Vector-only embedded graphics, multimedia and complex layout
are not promised as complete visual transcriptions; unsupported embedded images
are explicitly flagged and the complete original is retained. Current previews
are bounded to 1024 pixels; multi-frame raster sources retain all original bytes
but image understanding uses the first frame.

Legacy XLS reads stored cell values. To recognize its embedded pictures as separate
image sources, export it to XLSX before importing; the complete XLS original is
still retained.

For repeatable browser ingestion acceptance, use the explicit synthetic-only mode:

```bash
DOMAIN_KG_BROWSER_PREVIEW=1 DOMAIN_KG_BROWSER_IMPORTS=1 \
DOMAIN_KG_BROWSER_PREVIEW_SECONDS=1800 \
.venv/bin/pytest tests/evaluation/test_console_browser.py -q -s
```

It serves the current working source at `localhost:28080`, enables a fixture-only
mock-provider ingestion worker and two cancelled retry demonstrations, and
expires within 1–1800 seconds. The default preview remains operations-only.
`/__smoke__/start/owner?lang=zh-CN` creates a synthetic browser session;
`/__smoke__/finish` stops the preview and its pytest cleanup removes the synthetic
namespace. These helpers are never installed in the production application.
