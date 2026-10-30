# Cloud model comparison Implementation Plan

> **For agentic workers:** Execute natively in this chat, as required by the user. No local model, download, container startup, deployment or publication.

**Goal:** Verify the supplied Alibaba Cloud credential and compare answer and citation performance of models that actually respond.

**Architecture:** A bounded, credential-redacting HTTPS evaluator consumes frozen original evidence. Reuse the interrupted experiment's corpus/embeddings, recompute deterministic V3 / V6 / U contexts, and freeze all 24 questions before cloud answers. User authorized testing other models after five 7B/14B availability probes failed. Qwen3.8-Flash and Qwen3.7-Plus each returned HTTP 200 and OK; their parameter counts are not assumed to be 7B or 14B.

**Tech stack:** Existing Python/httpx, existing evidence ranking and audit functions, official Alibaba Cloud Chat Completions.

**Spec:** `docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`, Task 9 in the implementation plan; latest user requests for cloud model testing and credential validation.

## Global constraints

- Preserve all prior frozen code, results, production source/configuration, databases and local shutdown state.
- Credential from a 0600 temporary file outside Git, sent only as an Authorization header to an official verified HTTPS endpoint; redact it from every saved response/error. Never print it or put it in a request URL/command line.
- Models: qwen3.8-flash and qwen3.7-plus; non-thinking, temperature 0, seed 19, 192 maximum output tokens, identical source-only prompt and JSON object format. Same three-citation limit. No generative evidence filter or repair agent.
- Same 24 diagnostic questions: 12 public PDF facts, 4 three-link, 4 two-link, 4 unanswerable. Labels/earlier responses were observed; author-agent annotations and correlated synthetic chains preclude an independent acceptance claim.
- V3: 3 whole chunks / 1,800 characters. V6: 6 / 2,400. U retains selected V6 evidence and appends independent graph recall, at most 12 / 2,400. Identical requests reused only within this run/model, with provenance.
- At most 144 new formal HTTP attempts, one request in flight, 45 s timeout, 900 s total, 27,648 output-token maximum. Halt on auth/access/model errors, two consecutive timeouts, or five total runtime failures. Save failures before stopping; no silent retries or tuning after answers.
- Include errors/missing results in scheduled denominators. Semantic answer accuracy and necessary-source citation completeness (20 answerable questions) separate; audit every response against originals and hash-bind decisions. Report latency and usage; provider precision and alias snapshots are not under local control.
- No full-PDF build or Task 9 qualification from this diagnostic; original 85%/90% gate remains unfulfilled.

## Review focus

- Unknown citation IDs and malformed/extra answer fields must be rejected.
- Model/tool output cannot start tools or be executed.
- Errors, echoed credentials and transport exceptions must not expose the key.
- Provider access failure must stop before another model/question is charged.
- Partial runs must remain visibly incomplete; keyword matching is not the final semantic audit.

## Task 1: Bounded cloud calls

**Files:** `eval/cloud_calls.py`, `tests/unit/test_cloud_calls.py`.

- [ ] Write failing behavior tests for foreign endpoints, malformed/actionable/truncated responses, secret redaction and stopping after access failure.
- [ ] Implement the minimal HTTPS-only client, local answer-shape validation and bounded stop policy; use an HTTP test transport without real model calls.
- [ ] Run focused tests and Ruff.

## Task 2: Frozen paired benchmark

**Files:** `eval/cloud_compare.py`, `docs/cloud-model-comparison.md`; private artifacts `.cache/cloud-model-comparison`.

- [ ] Validate parent artifact/source hashes, freeze cases/evidence/code/plan and record the successful credential smoke separately from accuracy.
- [ ] Run both available models on identical contexts, rotating order. Reuse byte-identical requests explicitly; log usage/raw provider answers without authorization headers.
- [ ] Review every answer against original claims, calculate separate metrics and a complete/incomplete decision; compare cloud models without claiming an isolated size effect.
- [ ] Verify focused and unit/protocol tests, Ruff, mypy, frozen hashes, secret absence and unchanged production/local shutdown state. Record unrelated verification limitations; do not restart databases for offline work.

Progress is kept outside this frozen plan in private artifacts.
