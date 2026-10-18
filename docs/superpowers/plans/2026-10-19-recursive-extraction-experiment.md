# Bounded iterative extraction experiment implementation plan

> Execute natively using executing-plans, test-driven-development and
> verification-before-completion. The user approved bounded recursive testing
> after discussing decomposed extraction, independent checks and targeted repair.
> User correction: optimize iteratively; stop and summarize after at most five
> unsuccessful diagnostic/change/retest attempts, counting earlier failed routes.

**Goal:** Measure whether bounded iterative optimization improves the existing
local 0.8B and 4B models, retaining failures and cost as well as successes.

**Architecture:** Add an explicit offline experiment under `eval/`, outside the
production ingestion path. Both baseline and experimental paths consume identical
source chunks and a frozen three-predicate schema. Every accepted experimental
fact must pass the unchanged source validator; model review is an additional
fallible check. No database writes, KG publication or production configuration
changes occur.

**Tech stack:** Existing Python/Pydantic/httpx, pinned llama.cpp and existing
hash-verified GGUF files; no dependencies or model downloads.

**Spec:** User request; `docs/design.md`, `docs/contracts.md`,
`docs/revisions-and-publication.md`, Task 9 of the original implementation plan.

## Global constraints

- Keep current checkout and native execution, per the user's working method.
- Only cached public NHS PDF blocks and synthetic nonprivate contrast sentences.
- 4B cap 3,500,000,000 bytes; 0.8B cap 1,250,000,000 bytes. Two CPU threads,
  one model at a time, no GPU, 4096 context; leave database limits unchanged.
- Maximum two recursive repair passes, eight candidate relations per pass,
  48 calls and 1,800 seconds per model; no unbounded retries or conversations.
- Freeze cases and provisional agent-authored reference labels before live calls.
  They are not independent clinical annotation or full Task 9 acceptance.
- Keep raw responses, source artifacts, secrets and reports outside Git.
- Distinguish baseline, decomposed, verified and each repaired round. A runtime
  error or exhausted budget is recorded, never counted as correct empty output.

## Review focus

- Source refs or reviewer IDs outside the submitted candidate set must fail closed.
- One malformed candidate must not discard independent valid facts.
- Review disagreement/uncertainty must not become majority-vote acceptance.
- Repairs cannot bypass source validation, overwrite accepted facts or loop forever.
- Same weights checking themselves are correlated; explicit failure and no-answer
  metrics must remain visible alongside precision/recall and runtime cost.

### Task 1: Frozen cases, evidence conversion and bounded recursive algorithm

**Files:** `eval/recursive_cases.json`, `eval/recursive_models.py`,
`eval/recursive_prompts.py`, `eval/recursive_loop.py`,
`tests/unit/test_recursive_extraction.py`.

**Interfaces:** `run_recursive(chunk, schema, complete, max_repairs=2)` returns
typed stage snapshots, accepted extraction content, unresolved issues and calls.
`complete(stage, payload, response_model)` returns structured data; it cannot
execute tools or choose source locations outside the supplied chunk.

- [x] Write tests for per-fact isolation, repaired refs, rejected semantic claims,
  forged/duplicate verdicts, omitted verdicts, finite repeated repairs, source
  scope integrity and no-answer/direction/polarity metrics. Observe RED.
- [x] Implement mention extraction, relation proposals, canonical per-fact checks,
  independent verdicts and at most two targeted repair rounds. Preserve raw stage
  outputs and all failures; exact duplicates are deduplicated deterministically.
- [x] Run targeted and existing unit/protocol tests; inspect source compatibility.
  Expected: all pass, no production source changes. Commit the experiment.

### Task 2: Capped real model comparison and evidence report

**Files:** `eval/recursive_run.py`, `eval/recursive_runtime.py`,
`eval/iterative_extraction.py`, `eval/iterative_run.py`,
`docs/iterative-extraction-report.md`; update Task 9 status without checking its
full-corpus quality gate.

**Interfaces:** `python -m eval.recursive_run --model {4b,0.8b}` loads the frozen
cases, checks cached source/model hashes and writes a unique ignored run folder.

- [x] Test runtime rejection, exact scoring, source loading and cost limits before
  implementing the harness; use real deterministic boundary fixtures, no model
  quality assertions based on mocked responses. Observe RED then GREEN.
- [x] Attempt baseline/decomposed comparison and later iterated strategies serially.
  Under the user correction, stop a strategy after development failure or repeated
  runtime timeout; do not spend on all six cases after a failed gate. The known
  positive/frozen contrasts run only after development passes. No strategy qualified
  for a full contrast run; retain that missing acceptance explicitly.
- [x] Inspect actual responses; any algorithm change uses development cases only,
  records its hypothesis and a new version. Do not tune on frozen contrasts.
- [x] Record measured precision/recall, no-answer mistakes, structural rejection,
  call/token/time cost, per-round changes, unresolved candidates and process exit.
  Run appropriate final checks; report an improvement or a negative result.

## Scope decisions

This pass tests the proposed workflow before integrating it into ingestion.
Automatic entity merging, corpus reprocessing, new model training/downloads and
full-document graph publication require later measured work. The existing image
and its verified production source remain unchanged by this experiment.

## Iteration stop rule (user correction)

Count complete diagnostic/change/retest attempts, not individual model calls.
Do not reset the counter when switching models or prompts. Attempt 1: 4B runtime
timeouts, no quality result. Attempt 2: 0.8B decomposed extraction plus same-model
verification loses all three development reference facts. At most three further
strategies may be tried. Every attempt is recorded with hypothesis, change,
measured result and failure reason. Stop at five unsuccessful attempts; do not
add a sixth variation. Success requires all three development reference facts
with no extra false claims, then no regression on the known positive/frozen
contrast cases. These tiny provisional references do not establish corpus quality.

## Final measured outcome

Stopped after exactly five unsuccessful strategies. Attempt 1: 4B timeout,
excluded from semantic scoring. Attempt 2: 0.8B same-weight review/repair,
0 TP / 0 FP / 3 FN on development. Attempt 3: compact protocol,
2 TP / 1 FP / 1 FN. Attempt 4: per-predicate questions, 2 TP / 3 FP / 1 FN.
Attempt 5: literal phrase copying/pattern mapping, 0 TP / 0 FP / 3 FN (abstained).
No sixth model experiment, complete frozen-set acceptance or graph publication.
All owned models stopped. The 0.8B budget totals 22 attempted HTTP calls and
494.154 s, within the 48-call / 1,800-second limits. Full unit/protocol checks:
385 passed in 13.15 s; Ruff, strict mypy (239 modules) and diff checks pass.
The durable ledger and summary are under `.cache/iterative-kg/`.
See [the full measured report](../../iterative-extraction-report.md).

Author review was native, honoring the user's no-delegation instruction. The
compact stages deliberately measure relations only; medical type inference and
general English/non-English entailment are not established. The existing image
and production source are unchanged; Task 9 remains unchecked.
