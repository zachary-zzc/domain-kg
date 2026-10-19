# 4B unseen-question comparison and conditional PDF construction

> Execution: native in this chat, as required by the user. No delegation, installation, model download, external model API, deployment, or corpus migration.

**Goal:** Compare pure dense retrieval with dense retrieval plus the frozen lightweight source graph on 40 new questions; construct the cached full PDF only if the user's qualification gate passes.

**Architecture:** Offline, source-scoped evaluation using the cached Qwen3-4B and existing embedding model. Both arms share source chunks, embeddings, original-text budget, answer prompt, and generation settings. Existing `name_graph`/`walk` and answer prompt remain unchanged; no tuning on the new results.

**Spec:** User request and threshold reply; `docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`, implementation Task 9. This tests retrieval-supported QA, not the semantic extraction gate.

## Protocol fixed before inference

- Forty new questions: 20 public PDF facts not used in the prior QA optimization, 16 new synthetic operational/relation questions, four unanswerable questions. Synthetic and public results remain separate in reports. Source PDF was previously read, and labels are author-agent provisional annotations, not independent expert labels.
- Neither old questions nor paraphrases of their target facts qualify. New synthetic questions include new domains, multi-hop links, explicit negatives, and lowercase names; disclose recurring chain structure.
- Arm V: cosine similarity only, no lexical fusion. Arm D: same dense ranking, same first seed, existing source-only name graph and two-step walk, dense fallback. This isolates graph augmentation; the old 8/10 comparator included BM25 and is not this baseline.
- Maximum three whole original chunks and 1,800 characters per answer; unchanged 128 output-token prompt, temperature zero, seed 19, thinking off. Never inject gold labels or auto-append citations.
- Freeze/hash code, cases, source, configuration, and graph before answer inference. Run once, alternate arm order; share only byte-identical requests within this new run. No past response reuse or score-driven retry.
- Answer accuracy: correct answers / all 40 questions, including strict refusal for unanswerable questions. Report label matching and author semantic audit separately if they differ.
- Citation completeness: all required source passages retrieved and explicitly cited, valid unique IDs, / the 36 answerable questions. Score independently of answer correctness; also report their conjunction, citation validity and unanswerable behavior. Audit response claims for unsupported additions. Publish Wilson intervals, counts, per-category and paired outcomes.
- User gate: D answer accuracy >= 85%, D citation completeness >= 90%, D answer accuracy >= V, all scheduled questions completed and semantic audit complete. Evaluate exact integer fractions; no rounding up. Failures remain in denominators. Do not replace the gate with conditional-on-correct citation rates.
- Native CPU two threads, GPU zero, one model resident; existing RSS watchdogs 3.5 GB chat / 1.25 GB embedding. Preserve <=24-token full-coverage embedding pooling from the qualified pilot and disclose baseline limitations.
- New run capped at 80 actual chat HTTP attempts, 3,600 model-stage seconds, 10,240 reported completion tokens; 120 seconds per call, two consecutive timeouts halt. No automatic retries; maximum five runtime failures before summary. At most five unsuccessful strategies, including preparation failures; no sixth attempt.
- Threshold failure stops at measured report. Threshold success authorizes a separate complete-PDF local construction using actual source coverage and durable staging. QA qualification does not authorize fabricated semantic assertions or bypass existing extraction/publication validation. Preserve and report a source retrieval graph separately from typed factual KG results.

## Review focus

- A hidden BM25 contribution must not inflate the pure-vector baseline.
- Missing evidence/citations must not disappear from denominators; correct answers with partial citations remain citation failures.
- A wrong answer mentioning an expected token must be rejected during semantic audit, with raw response preserved.
- Stale cases, changed code, incomplete execution, and model failures must not qualify full-PDF work.
- Negated/lowercase/ambiguous anchors may damage graph traversal; do not repair them after seeing scores.

## Tasks

### 1. Frozen cases, independent metrics, and gate

Files: `eval/holdout_cases.json`, `eval/holdout_pilot.py`, `tests/unit/test_holdout_pilot.py`.

- [ ] Add meaningful failing tests for pure cosine ranking, independent citation scoring, exact threshold boundary, missing/error records, and immutable freeze checks.
- [ ] Implement small evaluation helpers and 40-case dataset; verify source locations and no prior target-fact reuse.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_holdout_pilot.py -q`; inspect failure then passing results.

### 2. Bounded real-model evaluation

Files: `eval/holdout_run.py`; ignored artifacts `.cache/holdout-4b/`.

- [ ] Reuse owned native process guard, complete-coverage embeddings, unchanged answer prompt; add explicit run-local limits and immutable manifests.
- [ ] Freeze protocol, cases, source, runtime settings, code and source-only graph before inference; run prepare then evaluate once.
- [ ] Preserve every HTTP request/response, failures, usage, RSS and timing. Audit all answers and required citations against frozen source labels without model/prompt/graph tuning.

### 3. Qualification decision and conditional construction

Files: `docs/4b-holdout-report.md`, relevant status notes; optional construction files only on pass.

- [ ] Calculate both metrics and exact gate, uncertainty and paired differences; retain subset results and all failures.
- [ ] If qualified, construct every parsed PDF block under a fresh local namespace, retain evidence and distinguish source graph from typed facts; verify graph/vector readiness before any accepted publication. Otherwise record why no full-PDF build ran.
- [ ] Run unit/protocol regression, Ruff, production mypy and diff check; self-review per user native-only constraint. Confirm owned models stopped and report concrete artifacts.
