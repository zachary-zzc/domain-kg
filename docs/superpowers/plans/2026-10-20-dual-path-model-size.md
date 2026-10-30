# Cloud dual-path model-size evaluation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure model-dependent graph construction and actual vector/graph retrieval, separately from supplied-context QA.

**Architecture:** An offline, source-scoped experiment uses the existing fixed 1,000 public questions and the union of their source paragraphs, including distractors. One common cloud embedding index is shared; each of the five selected chat models extracts its own graph. Retrieval supplies original passages, never generated facts, to the reader.

**Tech Stack:** Existing Python/httpx/numpy, Alibaba compatible API, immutable JSON checkpoints. No local model, database service, or new dependencies.

**Spec:** User's current dual-path/model-size request; docs/design.md, docs/contracts.md, docs/revisions-and-publication.md, Task 9 of docs/implementation-plan.md. Existing experimental reports supply failure cases, not accepted quality claims.

## Global constraints

- Preserve all old experiment artifacts. Do not send the NHS PDF, private materials, credentials, or answer annotations to the provider.
- Existing 219.60 MB raw library is the sampling pool. Actual formal search corpus is approximately 3.51 MB of distinct source text / 6,563 paragraphs from the fixed 1,000 cases; clearly disclose this distinction.
- Models: qwen3.5-397b-a17b, qwen3.5-122b-a10b, qwen3.6-35b-a3b, qwen3.8-27b, deepseek-r1-distill-qwen-1.5b. These architectures differ; parameter count alone is not a controlled variable.
- Builder sees source IDs, titles and text only. No questions, gold answers, supporting-fact annotations, or decompositions. Source IDs are globally scoped by dataset/title/content; repeated names are retrieval hints, not asserted identity.
- Vector embedding: one supported Alibaba embedding model and fixed dimensions for every arm. Cloud smoke test must succeed before indexing.
- Graph extraction: bounded literal subject/predicate/object/polarity/source/quote records; validate references and exact quotation. Grounding is not proof of semantic correctness. Measure semantic precision/recall on a separate frozen fictional gold corpus, not pretend public QA provides exhaustive triple gold.
- Compare V6, V12 (matched larger context), G6, U12 (retain V6 then add G6). All arms have a 12,000-character source budget; U must preserve all actually packed V6 sources. No generative filter. Graph-only retrieval is independently seeded from question/entity lexical matches.
- Main matrix uses the same model for construction and reading, with common vector arms; this measures practical end-to-end performance. Do not call differences isolated construction effects. Report retrieval recall independently of the reader.
- Answer EM/F1, citation completeness, supporting-source recall, protocol success, abstention and runtime/cost are separate. Normalize exactly one complete JSON Markdown fence for all models from the outset; retain raw-format failures separately.
- Pilot: five concurrent requests. Formal: sixteen cloud I/O requests in one process, with single-thread numerical work and no local LLM; 60-second hard total deadline, 2 MiB response limit, no content-filter retries. Five unsuccessful algorithm strategies stop optimization; routine calls are not strategy attempts.
- Run excluded fictional/public pilot first. Estimate full cost from measured usage before formal execution. Hard total list-price budget RMB 100 including explicit reserve for missing paid usage; stop and summarize if exceeded. No automatic PDF build/publication.

## Review focus

- Local citation IDs collide between questions: global remapping and tests must prevent false attribution.
- Shared passages can make formerly unanswerable cases answerable globally: retain original gold and flag changed-scope ambiguity; report no-answer results separately.
- Graph candidates can displace vector evidence: test U retains the entire packed V6 set under the byte/character budget.
- A correct source quote can support a reversed or negated relation incorrectly: controlled gold tests score direction and polarity.
- A format-only failure is not a semantic failure: symmetric fence normalization, separate raw/semantic metrics, bounded 1.5B qualification.

### Task 1: Data, graph contract and retrieval

Files: create eval/dual_path_data.py, eval/dual_path_retrieval.py; tests/unit/test_dual_path.py.

- [ ] Write failing tests for global IDs, gold-free extraction payloads, quote grounding, polarity/direction scoring, fence normalization and vector-retaining fusion.
- [ ] Run tests and observe missing-interface failures.
- [ ] Implement source corpus/remapping, deterministic fictional gold cases, typed bounded extraction parser, independent graph recall and fusion.
- [ ] Run tests and Ruff; inspect size and provenance manifests.

### Task 2: Cloud pilot and bounded runner

Files: create eval/dual_path_run.py. Artifacts under .cache/dual-path-2026-10-20/.

- [ ] Add failing tests for complete-response deadlines, duplicate/resume protection, unknown usage budget and graph/reader separation.
- [ ] Implement checkpointed extraction/embedding/reading phases using existing hardened bounded_call, without changing old frozen modules.
- [ ] Freeze pilot source/prompts/settings before cloud calls. Run actual extraction for all five models and embedding smoke test with the existing key.
- [ ] Record construction precision/recall, output usability, cost estimate and qualification; at most five failed strategy changes. No tuning against formal outcomes.

### Task 3: Formal matrix and report

Files: create docs/dual-path-model-size-report.md; artifacts remain outside Git.

- [ ] Freeze corpus, 1,000 questions, prompts, retrieval settings and qualified models; preserve source hashes and licensing references from the original library.
- [ ] Build common vector index and model-dependent graphs. Run actual global retrieval and four-arm answering within the cost cap, checkpoint every response.
- [ ] Independently recompute metrics and verify unique planned pairs, reference validity and candidate preservation. Missing work stays missing, not a claimed completed accuracy.
- [ ] Report outcomes and limits, including the smallest model actually demonstrated by this experiment. Verify tests and cleanup the temporary key/background tasks.

## Execution ledger

- 2026-10-20: Existing branch and workspace reused by explicit native-execution instruction. No production integration, commits, database restart or new worktree required for this offline correction.
- Ruling: Reuse the fixed 1,000 questions to preserve the requested scope. Results are a new retrieval experiment, not a new unseen QA set; prior supplied-context answers have already been observed.
- Pilot strategy 1 archived at .cache/dual-path-2026-10-20/: all five sizes received real extraction calls. The reading phase exposed a pilot-corpus reference error before any reader call. A regression test pins rejection of graph sources outside the selected corpus; public pilot graph sources are explicitly excluded from fictional retrieval.
- Pilot strategy 2 uses the same user-message instruction and a positive/negative worked example for every model. Answer generation allows 2,048 tokens for every model; a reasoning model is not disqualified solely by the earlier 512-token budget. Strategy 1 code and plan bytes are preserved and hash-verified in code-v1-archive.json; its results are not overwritten.
- Pilot strategy 3 makes the per-source extraction limit explicit and uses short request-local numeric source references, remapped by the server to immutable corpus IDs. Duplicate JSON keys are rejected; 1.5B emitted such duplicate fields in strategy 2. Strategy 2 code and plan bytes are preserved in code-v2-archive.json. No formal outcome has been used for optimization.
- Strategy 3 pilot finished all five models, 20 reference facts and 12 fictional questions per arm. It is development calibration, not formal accuracy. All five are protocol-eligible; low 1.5B semantic performance is retained rather than excluded. No further algorithm tuning before formal execution.
- Ruling: Formal concurrency is 16 cloud network requests to shorten the multi-model matrix; this does not start local model processes. Lazy request construction and on-disk embedding checkpoints reduce local memory; the native process has one numerical thread. The RMB 100 cap includes earlier pilot usage and reserves RMB 2 for in-flight requests.
