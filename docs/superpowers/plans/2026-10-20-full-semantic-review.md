# Full completed-response semantic review implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Reassess every attempted formal response already saved, compare models' vector and dual-path content correctness, independently measure citations, and list ambiguous or erroneous reference items.

**Architecture:** Read-only replay of the frozen experiment produces source-linked answer groups without revealing model/arm names to the review view. Native semantic judgments and explicit question-quality adjudications feed reproducible paired summaries. All prior raw outputs and scores remain immutable.

**Tech Stack:** Existing Python, local JSON artifacts and native assistant review; no cloud inference, local LLM, new dependency, database restart or production modification.

**Spec:** User's complete-response semantic re-evaluation request; docs/semantic-content-review.md defines the accepted content criterion. Existing source provenance and production evidence boundaries remain unchanged.

## Global constraints

- Include all 8,299 attempted formal model/question/arm pairs, including failures and the small incomplete tail. Compare V6, V12 and U12 explicitly; retain G6 as an additional diagnostic.
- Main model/arm comparisons use a shared question cohort and shared question-quality eligibility. Publish individual coverage and all completed pairs; never compare different completion subsets as equivalent.
- Content correctness accepts equivalent entities, paraphrases, complete sentences and numeric/unit equivalence. Preserve direction, negation, dates, quantities and completeness. Partly correct answers are separate from fully correct answers.
- Cite coverage is independent of answer correctness, abstain flag and JSON schema success. Map only explicit request-local IDs; never invent or auto-add citations. Distinguish malformed or ambiguous citation fields from missing supporting evidence.
- Evaluate actual answer text even if citation schema is invalid. Inspect malformed raw answers natively; do not label wrong solely for syntax. Empty output and failed calls remain no-answer outcomes with independent failure records.
- Original no-answer labels depended on local contexts. Review their scope and do not claim validated global unanswerability without evidence. Question ambiguities, broken entity chains and reference errors have explicit reasons and source IDs.
- Reuse the 60-question judgments only when the exact question and actual answer content match. Review every remaining distinct substantive answer and every completed question's quality. No fuzzy-match threshold or token-F1 proxy replaces semantic judgment.
- Preserve all previous experiment files and reports by recording supplemental results. No paid inference, secrets or new model execution.
- Native execution in this chat follows the user's project instruction; no delegation.

## Review focus

- A valid answer with invalid citations must retain its content judgment.
- Distinct answers with opposite signs, negation or different entities must never share a deduplicated grade.
- Malformed JSON or duplicate answer fields must be explicitly inspected rather than silently repaired.
- A corrected or ambiguous reference changes eligibility equally for all model/arm pairs on that question.
- A successful transport retry must appear once in the answer matrix; original attempts remain auditable.

### Task 1: Freeze scope and independent extraction

Files: eval/semantic_review.py; tests/unit/test_semantic_review.py; private artifacts under .cache/dual-path-2026-10-20-v3a/full-semantic-review/.

- [x] Write failing tests for independent answer/citation extraction, source mapping, duplicate-field handling and coverage-only common cohorts.
- [x] Run the failing tests, implement the smallest review/replay utilities, then verify them.
- [x] Freeze all attempted logical response IDs, raw hashes, question/source hashes and content groups. Record every prior judgment reused.

### Task 2: Full native semantic and question-quality review

- [x] Inspect all completed questions and classify quality/scope issues with explicit source-grounded reasons.
- [x] Inspect every remaining distinct substantive answer, preserving correct/partial/wrong/abstain/no-answer/undetermined categories and reasons.
- [x] Inspect raw-format failures and inconsistent answer fields. Review unresolved factual issues against source passages; external sources only when needed for a stated disputed reference.
- [x] Verify 100% response coverage and reject missing or duplicated judgments.

### Task 3: Independent citation and paired reporting

- [x] Replay explicit citations against frozen source-ID mappings independently of semantic correctness; mark unscorable references separately.
- [x] Compute shared-cohort V6/V12/U12 model tables, dataset/difficulty breakdowns, paired changes and individual completion coverage.
- [x] Publish the complete ambiguity/reference-error list, including any corrected interpretation and original gold/source evidence.
- [x] Verify original response hashes/API call count unchanged, run relevant tests, and deliver a self-contained report with remaining uncertainty.

## Verified completion — 2026-10-20

All 8,299 logical responses and 2,815 content groups have judgments; all 416 questions have quality reviews. The shared comparison uses 205 eligible questions from the 414 fully shared questions. All 210 flagged questions are listed, including a separate 42-item gold/reference issue index. Two independent replay processes produced identical hashes for the summaries, CSV and reports; all 14,839 HTTP checkpoint files remained byte-identical during replay, and saved answer hashes/old scores match the inventory.

The 12 new evaluation tests and Ruff checks pass. The wider unit run had 486 passes plus one existing parser test blocked by nested macOS sandbox-exec; that test passed separately outside the outer sandbox without any production source change. No cloud inference, local LLM, model download, DB start, corpus publication or production/dependency changes occurred. Verification and hashes are in the supplemental private review directory.
