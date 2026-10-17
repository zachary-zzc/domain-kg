# Real medical PDF extraction and graph

Execute inline in the existing feature branch. User authorizes implementation,
native execution of the already approved models, stage commits and local display.
A larger model requires explicit confirmation before download or execution.

Spec: docs/design.md, docs/contracts.md, docs/revisions-and-publication.md,
implementation-plan Task 9 and the user request. Originals, raw outputs, caches
and credentials stay outside Git. No fixture data may enter the resulting KG.

## 1. Model protocol and source windows

- [x] Add an opt-in `source_refs_v1` extraction format to approved profiles.
  Send numbered exact source spans and a compact approved domain schema; model
  entities/assertions cite only those indices. Server creates local IDs, physical
  locations and offsets. Preserve polarity and qualifiers. Unknown references,
  fabricated names, invalid types/predicates and malformed output fail closed.
  Retain the full-format default, usage billing and before/after authorization.
- [x] Pack consecutive source spans under both configured model budgets before
  splitting an oversized individual span. Preserve exact Unicode coverage,
  deterministic IDs and bounded overlap; avoid one request per visual PDF line.
- [x] Verify protocol validation, context rejection, cross-namespace refs,
  revocation, negation and source coverage with focused tests. Include format in
  extraction provenance and hashes. Commit after verification.

Stage 1 evidence: 12 new protocol checks passed after failing for the missing
profile option. The new visual-line packing check failed at 20 requests before
the fix. Unit plus affected real-store ingestion/resolution cohort: 331 passed
in 40.01 s. After removing repeated unrestricted endpoint-type lists from the
prompt, the focused protocol/window/provider cohort passed again. Ruff and strict
mypy (232 modules) passed. Actual Docker hard caps: 1.476 GB, swap disabled.
Protocol is opt-in; profile serialization and model-window-v2 bind checkpoint
hashes, and unchanged full-format profiles remain supported.

## 2. Bounded real-model quality check

- [x] Reuse pinned approved native runtime/models; one model at a time, two CPU
  threads, no GPU/OCR, sampled RSS watchdog. No download or native autostart.
  Run at most three representative short PDF windows initially; validate through
  the real client and canonical extractor, report usage and exact rejected cases.
- [x] If inadequate, propose a pinned larger model with download size, licence,
  separate native RAM budget and a tiny check. Wait for user approval before
  its download/run; continue independent runner/page preparation meanwhile.
- [x] Commit the measured findings and configuration, keeping quality gates open
  until evidence supports them. No repeated full-document failed-model runs.

0.8B compact-protocol check: all three cases rejected (12.462 / 29.246 / 7.888 s),
including one 1024-token truncation. Raw provider totals: 964 input + 1434 output
tokens. Native peak RSS 822870016 bytes; owned server stopped, zero publications.
User explicitly approved official Qwen3-4B Q4_K_M download and three-passage check,
then full-document use only after qualification. Pinned metadata and separate
3.5 GB native cap are in config/medical-model.approved.json; Docker stays 1.5 GB.
Download is in progress, not a successful model-quality gate.

Prepared operational support: per-chunk scoped/validated checkpoints survive
interruption; partial chunk checkpoints cannot satisfy the final extraction hash.
Only typed output truncation permits at most two source-splitting levels; evidence
rejections are never silently repaired/retried. Native owned-process cleanup and
RSS watchdog have real-child lifecycle tests. Truncated responses now record their
actual usage. These preparations do not establish model quality.
Stage 2 support verification: 332 unit/recovery checks passed in 22.07 s with
the actual 1.476 GB Docker hard cap, swap disabled. The earlier ordinary-sandbox
unit invocation had one native-parser isolation failure; rerunning outside that
parent sandbox passed, without changing parser isolation. Ruff and strict mypy
(235 modules) passed. Larger-model quality remains pending its verified download.

## 3. Real full-document run and display

Prepared follow-up (not full-corpus acceptance): `source_names_v1` removes
model-generated entity indices, the medical schema can express anatomy/substance
and produces/is_a, and an explicitly separate format example teaches field
semantics. The example is never source evidence; canonical validation remains
strict. A scoped graph overview includes disconnected nodes, at most 200 nodes /
400 edges per page; the existing node view retains 25/40. API private-file
selection fails closed without approval or when its namespace is outside scope.

Native evidence: Qwen3-4B file length/hash verified; increasing CPU microbatch
32 -> 128 improved observed prefill from ~4 to ~16 tokens/s, without raising two
threads or the 3.5 GB RSS watchdog. Diagnostic runs found self references, absent
endpoints and incorrect semantic relations. Final named-example-v2 gate: only
thyroid passed (15.808 s); cholesterol/coeliac failed (53.383/60.739 s). Peak
3081830400 bytes; owned process stopped. There were 11 actual 4B requests across
the initial checks, instrumentation and prompt fixes, not a full-PDF run. No facts
were published. Stop further native retries; request a reliable approved API.

The source preflight with current medical prompt covers all 228 blocks in 29
128-token windows. This is input coverage only. `config/medical-pdf-live.json`
sets 48 windows maximum, 1800 s whole-run timeout, and separate memory profiles.
The opt-in actual-store runner supports genuine native/API calls, then sequential
approved embeddings; it is gated on a matching successful quality report. It has
not run end to end, and persistent retention/reopening remains to be finalized
before serving a durable actual KG. Do not equate skipped opt-in collection with
successful corpus processing.

Preparation verification after API wiring: 338 checks passed and the real corpus
run correctly skipped (34.93 s); actual Docker hard cap 1.476 GB, swap off. The
four JavaScript contracts passed (0.177 s); Ruff, strict mypy (235 modules) and
offline wheel/sdist inventory checks passed. These results verify software
boundaries, not the incomplete real-PDF outcome. The requested API connection
remains unconfigured, and its private template is disabled by default.

- [ ] Build a resumable, bounded native run over every parsed PDF source span.
  Persist hashes/profile/model/protocol, real raw results, validated batches,
  per-window status and usage outside Git. Empty results count as processed, not
  as demonstrated semantic recall. No simulated/fallback model responses.
- [ ] Run extraction then approved embeddings sequentially. Publish only validated
  source-bound results via Mongo authority and verified Neo4j/Qdrant publication.
  Failed/incomplete work cannot replace accepted results. Keep Docker hard total
  below 1.5 GB and verify it before each storage phase.
- [ ] Show the actual KG with bilingual coverage/model/rejection totals, source
  navigation and bounded neighborhoods. Distinguish full input coverage from
  clinical or semantic completeness. Check real HTTP permissions and evidence
  links; record browser automation limitations honestly. Commit the stage.

Review focus: malformed refs, duplicate entity names, line-fragment context loss,
incomplete windows marked complete, cached fixture output passed off as real,
stale profile cache reuse, overlapping native models, source permissions and
publication fencing. Use existing tests for unchanged lifecycle boundaries.
