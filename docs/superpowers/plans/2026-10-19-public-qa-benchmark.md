# Public QA model benchmark implementation plan

> **For agentic workers:** Use executing-plans inline in this chat, as required by the user's repository instructions. Do not delegate or start other chats. Progress is recorded separately after this plan is frozen.

**Goal:** Assemble approximately 200 MB of attributed public QA data and compare five cloud models on the same frozen, stratified 1,000-question sample.

**Architecture:** Standalone offline evaluation files under `eval/`; private source data, requests, responses and results under ignored `.cache/public-qa-2026-10-19/`. No production service, database, graph publication, local inference or model weight download. Supplied-context answering measures reading, reasoning and citation selection; it does not measure corpus retrieval, semantic KG extraction or qualify Task 9.

**Tech Stack:** Existing Python 3.12, httpx and pytest; JSON/JSONL and bounded ZIP reads. PyArrow 19.0.1 is isolated in the ignored evaluation directory solely to convert the fixed-revision HotpotQA Parquet mirror. No new production dependency.

**Spec:** User's 200 MB clarification and five-model selection; `docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`, Task 9 and `docs/acceptance.md`.

## Global constraints

- Selected models: `qwen3.5-397b-a17b`, `qwen3.5-122b-a10b`, `qwen3.6-35b-a3b`, `qwen3.8-27b`, `deepseek-r1-distill-qwen-1.5b`.
- Cloud endpoint remains the verified official Beijing compatible API. Secret stays in a mode-0600 temporary file and is deleted at completion; never log authorization headers.
- Only newly downloaded public benchmark contexts may be sent. Never import or transmit the NHS/private corpus or previous mixed medical experiment.
- Approximately 200 MB means actual uncompressed source data, not padded or repeated records; record downloaded/compressed bytes and normalized bytes separately. Retain licenses and source/revision/SHA-256 provenance.
- Official annotated development/validation splits supply the scored sample; training sources may form the rest of the reusable bundle, but no model tuning occurs on formal questions. Public benchmarks can have pretraining contamination.
- Four datasets: SQuAD 2.0, HotpotQA distractor, MuSiQue-Full and DROP. Do not also count overlapping MuSiQue-Ans as additional unique data.
- Reject and record upstream invalid support references before sampling. MuSiQue-Full answerable/unanswerable variants share upstream group IDs; assign variant-specific record IDs and sample at most one variant per normalized question.
- 1,000 questions: SQuAD 250 (125 answerable, 125 unanswerable); HotpotQA 250 (125 bridge, 125 comparison); MuSiQue 250 (75 two-hop answerable, 75 three-hop answerable, 50 four-hop answerable, 50 unanswerable); DROP 250 (125 number, 75 span, 50 date/multi-span). If a bucket is unavailable, document and freeze a replacement before any formal calls.
- Deterministic SHA-256 ordering with seed `domain-kg-public-qa-2026-10-19-v1`; deduplicate normalized question strings across all selected datasets. Pilot questions are excluded by ID and normalized question. Contexts are supplied whole, with stable source IDs and no gold annotations; skip over-budget questions before freezing and report exclusions.
- All five models receive identical messages and output contract: JSON `answer` (string or string list), `citations` (source ID list), `abstain` (boolean). Empty answer and citations when abstaining. At most 8 citations. Model-specific API switches may only disable Qwen thinking or omit unsupported fields. No gold IDs in prompts.
- Qwen non-thinking, temperature 0; max completion 512 tokens. DeepSeek may expose additional reasoning tokens; track them separately and enforce observed resource stops. No per-question repair, retries or answer-dependent filtering in the formal run.
- Cloud concurrency at most 5, each model sequential. One hard wall deadline per call of 60 seconds, response cap 2 MiB. Model stops on 401/403/404, two consecutive transport failures, or five consecutive unusable completions. Interrupted and unattempted questions remain in planned denominators, with completion coverage separately shown. A deliberate `--resume` may continue the user's already authorized comparison after a runner repair: first archive the original code/plan/stop, freeze every prior raw response hash, and skip every attempted pair including failures. Never silently resume or erase failed records. Do not retry or rewrite provider content-inspection rejections.
- Prepare cost estimate using Beijing list prices before bulk calls, without assuming remaining free quota. Proposed run cap RMB 100, 5,030 total calls including pilot and up to five excluded-question adapter probes, 30 million reported input tokens and 5 million reported completion/reasoning tokens. Stop before starting further calls when an observed limit is reached. Unknown usage stays explicitly unknown. After the first provider content-inspection failure paused the batch, accounting was revised to reserve RMB 1 per unreported priced-model request and 0.5 RMB for in-flight requests, rather than treating missing usage as zero or blocking all unrelated remaining questions. Report known token usage, known list-price cost and the additional unknown-usage reserve separately. The officially free 1.5B model's unreported usage is separately flagged. These are operational budget estimates, not guaranteed maximum provider bills.
- Preflight checks protocol usability without using answer correctness for selection: five distinct valid completions are required per model. After five unsuccessful adapter trials, do not start a model's 1,000 formal calls. Preserve its planned denominator and zero completion coverage; report accuracy as unavailable, never as a measured 0% ability score.
- Semantic answer scores use independently defined EM/token F1 against gold aliases. DROP uses numeric-aware multi-span matching; if not exactly upstream scoring, label the implementation as a local compatible metric and record differences. No LLM judge. Invalid protocol/runtime failures score zero for end-to-end results; content extracted from malformed output may only appear in separately labeled diagnostics.
- Citation metrics: HotpotQA sentence support, MuSiQue paragraph support, SQuAD/DROP passage support. Report units per dataset, precision/recall and all-required-support coverage on all answerable planned cases separately from answer correctness; refused answerable cases fail citation completeness. Unanswerable cases have separate refusal accuracy. Passage-only support does not prove numeric operands or sentence-level completeness.
- Do not pool incompatible citation units into a claim of a production 90% evidence gate. No automatic full-PDF construction or release checkbox changes.

## Review focus

- A bad source reference or gold annotation accidentally entering the prompt invalidates the comparison.
- Public no-answer records must not silently become answerable by filtering missing support or by leaking the gold answer.
- Runtime/JSON/citation failures must not disappear from denominators or silently turn into correct refusals.
- Number normalization must preserve numeric value, sign, dates and unordered multi-span answers.
- Resume must reject changed code/data/prompts/model settings and duplicate rows.

### Task 1: Acquire and freeze public data

**Files:** Create `eval/public_qa_data.py`, `tests/unit/test_public_qa_data.py`.

**Interfaces:** `normalize_squad`, `normalize_hotpot`, `normalize_musique`, `normalize_drop` yield normalized question records; `select_cases` creates deterministic disjoint pilot/formal samples. CLI download/prepare records provenance, sizes, exclusions and hashes.

- [ ] Write and run failing tests for gold/support separation, no-answer preservation, invalid support rejection and deterministic disjoint selection.
- [ ] Implement bounded data acquisition, adapters and manifest creation. Preserve upstream authorship/licenses without vendoring full datasets into Git.
- [ ] Run the tests, inspect actual schema/counts, acquire natural source files totaling about 180–220 MB, and freeze the sample and all request payloads before formal execution.

### Task 2: Score and safely call cloud models

**Files:** Create `eval/public_qa_metrics.py`, `eval/public_qa_run.py`, `tests/unit/test_public_qa_metrics.py`, `tests/unit/test_public_qa_run.py`.

**Interfaces:** `parse_completion`, `score_case`, `aggregate`; runner CLI `pilot`, `run`, `report` uses immutable manifests and append-only raw response/checkpoint files.

- [ ] Write and run failing tests for alias/numeric/multi-span scores, all-required-support citations, invalid format, full planned denominators, hard deadlines and budget/stop conditions.
- [ ] Implement strict parsing, independent metrics, bounded concurrent cloud requests, redaction, content hashes, token/cost accounting and resumable checkpoints.
- [ ] Pilot at most five excluded questions per model. Fix shared protocol/runtime bugs before freezing, stop after five unsuccessful repair attempts, and never tune on formal answers.
- [ ] Freeze inputs/code/settings; run 5,000 planned formal responses within resource caps and report any stop explicitly.

### Task 3: Verify and deliver

**Files:** Create `docs/public-qa-benchmark-report.md`; ignored evidence includes raw sources, manifest, request/response ledger, per-question and grouped results.

- [ ] Run targeted tests, `ruff check .`, `mypy src/domain_kg`, and existing unit/protocol suite if new changes justify it. Do not restart databases for integration tests.
- [ ] Independently verify source/response hashes, dataset totals, all 5,000 planned denominators, model/data/difficulty breakdowns, protocol/refusal/runtime failures, costs and completion coverage. Audit a deterministic subset manually.
- [ ] Confirm no local models or weight downloads started, no credential in artifacts, and temporary credential removal. Report answer EM/F1 and citation support separately, with scope/contamination/scoring limitations.
