# KG construction, model suitability, retrieval and graph delivery

> **For agentic workers:** REQUIRED SUB-SKILL: use executing-plans natively in this session. The user's repository instructions require native execution; no delegation.

**Goal:** Explain what source-corrected graph + RAG adds over direct KG construction, measure smaller models on KG construction, complete real-store tests, and deliver viewable downloadable graphs.

**Architecture:** Preserve the frozen full-scale v3 experiment. Extend it in a new ignored experiment root with immutable requests/results. Reuse its 6,566 public paragraphs, 1,000 questions, shared embeddings, fixed reader and semantic judge. Assess graph facts separately from retrieval.

**Tech Stack:** Python, existing evaluation adapters, Alibaba and Cloudflare APIs, real MongoDB/Neo4j/Qdrant/S3 smoke stack, standalone HTML/JSON graph exports, artifact-tool workbook. Local inference was canceled by the user's API-only instruction.

**Spec:** User's four requested deliverables; docs/design.md, docs/contracts.md, docs/revisions-and-publication.md, implementation Task 9/11/13/19, docs/full-scale-retrieval-report.md.

## Global constraints

- Existing work and frozen experiments are preserved; generated graphs/credentials remain outside Git.
- No exact-match answer or graph-fact scoring. Quote provenance checks remain separate from semantic entailment.
- Construction never receives questions, answers, reference inventories or judge verdicts.
- Real service accepted projections are not changed by evaluation. Public benchmark graph exports are offline proposals.
- Model access failures are not accuracy failures. Quantization, generations, Dense/MoE and sample sizes remain explicit.
- User authorizes API costs without the previous aggregate ceiling and cleanup of old Docker test data; keep finite jobs and bounded retries.
- All work remains in this chat. Existing dirty worktree is reused because outputs and experimental inputs already reside here; no commit or unrelated changes.

## Review focus

1. Matched input and output budgets: distinguish old 3-fact batched KG from matched single-source 8-fact controls.
2. Semantic relation role/direction/time errors despite genuine quotes: blind judge plus fixed native audit.
3. Missing/failed model output: retain in coverage denominators, never silently treat empty graph as success.
4. Export identity and injection: source-scoped nodes, explicit lexical links, safe text rendering, all edges and provenance downloadable.
5. Store space regressions: fresh volumes, complete regular suite and serial physical-backend drill, retain failure evidence.

### Task 1: Freeze extension and restore test capacity

- [x] Record actual 0.5B–14B API probes and the canceled local-runtime attempts; add three successful Cloudflare API probes.
- [x] Clean only identified old domain-kg-dev test volumes and unused old build cache; verify capacity and health.
- [x] Freeze model/source/method selection before construction or judging; separately freeze the late Cloudflare supplement before its corpus outputs.

### Task 2: Controlled construction and retrieval comparison

**Files:** new eval/kg_comparison.py and tests/unit/test_kg_comparison.py; new ignored .cache/kg-complete-2026-10-21 artifacts.

**Interfaces:** source-only requests, immutable output records, graph facts with source IDs; existing OfflineIndex, pack, reader_request and reasoned judge unchanged.

- [x] Test construction blindness, bounded facts, identical original sources, judge ID coverage and fail-closed checkpoint identity.
- [x] Reuse all full-corpus initial and corrected graphs for the four cloud models; evaluate direct-KG + shared RAG with the fixed reader/judge over all 1,000 questions, including old baseline vs corrected attribution limits.
- [x] Complete the fixed stratified 64-source same-protocol API comparison: DeepSeek 1.5B, four larger Qwen models, and Cloudflare Llama 1B/3B/8B. Direct extraction (8 facts), source-only correction, context-assisted correction are separate arms. Canceled local results remain outside formal comparisons.
- [x] Generate independent source inventories before viewing candidates; blind fact support/coverage judging and paired source-bootstrap intervals, plus a fixed native inspection. Reuse exact requests only.
- [x] Complete the separately frozen 3B/8B JSON Schema supplement (384 records plus primary/secondary audits), preserving original results.
- [x] Verify immutable manifests, all requested records, usage, failures and provenance; compute same-source semantic coverage, not literal triple matches. (Global replay passed: 1,920 constructions, 640 primary / 40 secondary source indexes; frozen protocol retained.)

### Task 3: Downloadable graphs

**Files:** new eval/kg_graph_export.py and tests/unit/test_kg_graph_export.py; ignored standalone HTML/JSON exports.

- [x] Test source isolation, qualifiers/polarity and raw evidence retention, safe HTML escaping and complete export counts.
- [x] Export each initial/corrected full-corpus KG and each new small-model graph; HTML contains all graph data, supports search/filter/neighborhood selection and offline opening, includes JSON download. (39 standalone HTML/JSON graphs, ZIP and all file hashes verified.)
- [x] Verify all files and full graph node/edge/source counts; 39 viewers passed DOM/canvas functional checks and embedded/download data equality.
- [ ] Inspect representative graphs in a real browser. Environment limitation: local file URLs are rejected by browser policy; no workaround was used. Static previews and functional checks passed; the report explicitly leaves real-browser visual acceptance unverified.

### Task 4: Verification and complete report

- [x] Run full regular pytest suite on real stores, then the serial physical-backend drill; fix actual failures within scope and record results. (1008 passed, 4 opt-in skips; 2 physical passed; final JUnit retained.)
- [x] Produce Chinese report and workbook containing method comparison, per-model construction and retrieval, access/qualification, costs/resources, limits and deployment recommendation.
- [x] Verify workbook values and graph download bundle; preserve old reports and link final deliverables. (8 sheets, 496 formulas, 7,441 fact rows, 10,000 question-arm rows, 6,566 original source rows; all values and caches checked.)

Final delivery boundary (2026-10-23): all frozen API experiment queues and required store tests are complete. Small models were assessed on the fixed 64-source construction cohort; full-corpus retrieval covers the four larger constructors plus shared RAG and the representative KG-only control. Real-browser visual QA remains unavailable as documented above; it is not represented as a passing test and does not require further API quota continuation.

## Execution rulings

Plan approval is already supplied by the user's instruction to continue and complete these experiments; no repeated confirmation. The native method is explicitly required by AGENTS.md. The report will not claim 27B is a universal minimum or rank API-denied models as inaccurate. The small-model comparison is a fixed construction benchmark, not a false claim that local models built the full 6,566-source corpus.

### User steering: API-only (2026-10-21)

The user explicitly stopped all local model inference due to memory use. This supersedes the native 4B/7B plan and later 0.8B supplement. Their partial attempts remain for provenance and are excluded from completed quality comparisons. All subsequent model execution uses API. Twenty-eight OpenAI-compatible small-model probes and six native DashScope probes distinguish unavailable models from measured failures. The completed five-cloud-model construction matrix remains unchanged. Model availability limits are reported rather than filled with invented measurements.

### User steering: free Cloudflare and schema adaptation (2026-10-21)

Keep Cloudflare free; no paid subscription or upgrade. Resume only within the frozen daily allowance after the UTC reset. The user authorized completion after quota recovers; daily 08:05 Asia/Shanghai heartbeat `kg` handles continuation.

Actual probes show 1B rejects JSON Schema while 3B/8B accept it. Two separate post-hoc 64-source three-arm schema configurations are frozen; preserve the original 8-model results. Report 8 models / 10 configurations, and mark the first-source probe as part of exploratory selection.

The previous tool-managed long processes exited without final summaries. Preserve interrupted logs; resume immutable API checkpoints and run the regular plus physical store suites in a detached singleton pipeline. No duplicate paid dispatch or concurrent Cloudflare construction.
