# Full model-scale retrieval experiment

> **For agentic workers:** Use executing-plans, natively in this chat. The user's AGENTS.md forbids delegation unless requested. The user has already authorized implementation and API execution; no additional plan approval or new worktree is needed.

**Goal:** Complete the existing 1,000-question benchmark for qualified model-built, source-corrected graph/RAG knowledge bases versus a strong pure RAG baseline, using relaxed semantic evaluation. Five model sizes entered qualification; four qualified for full construction and 1.5B is reported as ineligible.

**Architecture:** Reuse immutable source-only initial graphs and cached embeddings from the complete previous construction, then run the same retrieval-assisted original-source correction with each graph's own model. Search the full common corpus through the actual production traversal/ranking/fusion implementation using offline storage adapters. Fix the reader and judge across construction models so the primary comparison isolates knowledge-base differences.

**Tech Stack:** Python, httpx, Pydantic, NumPy, existing scoped retrieval; Alibaba compatible API. No accepted store writes.

**Spec:** User instruction dated 2026-10-21; docs/design.md, docs/contracts.md, docs/revisions-and-publication.md; implementation Tasks 9 and 19.

## Global constraints

- All 6,566 existing dataset-scoped paragraphs and all 1,000 questions; HotpotQA, MuSiQue, DROP and SQuAD each 250. This is the complete existing evaluation corpus, not every example in the upstream datasets.
- Models: DeepSeek-R1-Distill-Qwen-1.5B, Qwen3.8-27B, Qwen3.6-35B-A3B, Qwen3.5-122B-A10B, Qwen3.5-397B-A17B. Total versus activated parameters and cross-generation confounds are explicit.
- Initial graphs: reuse all 938 attempted batches per model, including failures/empty graphs. Correction independently rereads original sources and may add omitted facts, at most eight per source. Each model corrects its own graph; no large model repairs small-model results.
- Construction receives no questions, answers, gold support, judge outputs or reasoning decompositions. RAG context uses source-title BM25, same dataset, two related passages capped at 2,000 characters; hints derive only from that model's source-validated initial graph.
- Correct one primary source per call using the previously tested single-source protocol. Validate each fact against its own original; record all rejected/invalid/missing output. A failed correction produces no corrected facts, not an undocumented fallback.
- Strong RAG = shared text-embedding-v4 cached exact cosine top 50 plus BM25 top 50, RRF. Graph = same branches plus production bounded graph traversal/ranking/fusion and supported qualified paths. Both: max 12 complete sources, max 14,000 content characters including path serialization; no extra graph budget.
- Primary reader Qwen3.6-35B-A3B for every arm. Original semantic judge Qwen3.5-397B-A17B is retained as sensitivity; the postregistered uniform primary re-evaluation uses Qwen3.8-2.4T-A95B (see execution record). Identical question/evidence payloads are evaluated once and referenced by every identical arm.
- Evaluate answer semantics, evidence utility/sufficiency, and citation support separately. Accept aliases, paraphrases, rounded equivalent units/dates, ordinary short inference, and any source-supported reasonable reading of ambiguous questions. Do not require exact answer/edge/reference-source matches or exhaustive reference chains. Do not accept contradicted roles, invented facts, or irrelevant evidence.
- Reference-free evidence assessment precedes reference-aware semantic checking. Original no-answer labels are diagnostics after global pooling. All 1,000 questions remain in attempt denominators; report 825 originally answerable separately plus dataset/hop breakdowns and failures.
- Fixed stratified 100-case audit across evidence variants and a second judge for disagreement checks; select by hashed IDs before outcomes, not by correctness. Native inspection checks concrete error cases.
- User lifted total call/cost ceilings. Record every immutable launch, response, token count, known list-price estimate and unknown cost. Transport retries only (429/timeouts/5xx), maximum three attempts; do not regenerate semantic failures. Rate limits and bounded per-request output remain.
- Credentials stay in a private temporary file outside Git; public originals/generated indexes/checkpoints remain in ignored .cache. No deployment, accepted KG migration, commits or Task 9 semantic acceptance claim.

## Review focus

1. Correction must not borrow quotes/facts across documents or datasets.
2. A missing/duplicate batch item must remain visible without discarding valid neighboring sources.
3. Request reuse must never rerun a successful call or conceal ambiguous launches/unknown usage.
4. RAG must be identical across construction models and share exactly the graph arm's total evidence allowance.
5. Semantically correct alternate answers and reference defects must not be turned into exact-match failures; failed judgments must not inflate scores or disappear from denominators.

## Task 1 — Immutable construction and API execution

Files: eval/scale_construction.py, eval/scale_transport.py, tests/unit/test_scale_construction.py, tests/unit/test_scale_transport.py.

Protocol repair files: eval/scale_single.py, tests/unit/test_scale_single.py.

- [x] Define and test source-isolated batch correction, per-source salvage, blinding, unknown-price accounting and resumable request identity.
- [x] Implement preparation that validates frozen originals, initial graph completeness and cached vectors; freeze inputs/prompts/configuration.
- [x] Run protocol qualification on source-only fixtures and preserve all five models' outcomes.
- [x] Correct the full corpus with all four qualified models, preserving every failure and rejection.

## Task 2 — Full retrieval and semantic evaluation

Files: eval/scale_retrieval.py, tests/unit/test_scale_retrieval.py.

- [x] Test equal context budgets, graph provenance, RAG independence, blind reader/judge inputs, relaxed semantic output validation and full denominators.
- [x] Materialize all 5,000 question/arm packets (shared RAG plus four qualified graphs) through actual scoped traversal/ranking/fusion. The unqualified graph arm remains N/A.
- [x] Run fixed-reader and reference-free evidence judgments, then reference-aware semantic judgments; reuse identical payloads and record all failures.
- [x] Run the fixed secondary audit and inspect discrepancies against original passages.

## Task 3 — Tables, uncertainty and verification

Files: eval/scale_analysis.py, docs/full-scale-retrieval-report.md; ignored machine-readable tables/checkpoints.

- [x] Test full/answerable/dataset denominators and paired confidence intervals with controlled fixtures.
- [x] Publish complete model × method tables: semantic correctness, supported correctness, evidence utility, sufficiency, citation support, gold coverage diagnostic, paired gain, graph size, failures, latency and cost.
- [x] Run relevant unit/protocol suite, Ruff, input/request hash verification and completion checks. Keep incomplete cells explicitly N/A if an external provider failure prevents completion.

## Execution record

- Ruling: reuse the completed initial source-only graphs instead of paying to recreate identical first-stage requests. Same-model correction independently adds/removes/corrects facts. Interpretation is this specified two-stage pipeline, not a pure scaling law.
- Ruling: pure RAG is one model-independent index, repeated by reference in the model table. Fixing the reader avoids attributing reader skill to retrieval quality.
- Ruling: retain the five previously usable endpoints for continuity. 1.5B lacks structured-output support; use the same JSON instructions without unsupported API flags, and expose protocol failures. Its current official page lists no public price, so report unknown cost rather than assume free.
- Ruling after protocol pilot: four-source batching caused omitted later sources (verified submitted s0–s3 versus returned s0). Stop and preserve v1 as an excluded protocol pilot; v2 uses one primary source per call. No retrieval outcome was examined to choose this change. Four fixed synthetic source tests qualify a model when at least three return nonempty grounded facts. Qwen 27B/35B/122B/397B each passed 4/4; 1.5B passed 1/4 and is reported as construction-ineligible, not assigned a fictitious graph quality score. The complete primary matrix is four sizes × two methods × all 1,000 questions.
- Task 1 code checks: 13 new unit/protocol tests passed; relevant source-repair/scoped-retrieval checks also passed. Full construction and baseline evaluation now execute independently with immutable records.
- Evaluation reliability amendment: fixed native inspection found the 397B non-thinking judge rejected the direct inference “fourth daughter → three older sisters”, and another semantic label contradicted its explanation. Preserve those original results. Add uniform reference-free then reference-aware re-evaluation of every packet with Qwen3.8-2.4T-A95B, thinking budget 4,096, output allowance 8,192. Same fixed reader and retrieval inputs; no favorable-arm selection. Its known Beijing list prices are RMB 12 input / 36 output per million tokens. Four synthetic calibration cases cover ordinals, time, aliases and semantic roles. An initial calibration annotation incorrectly called explicit negative evidence insufficient; native review corrected it (an untouched orchard before 1990 supports “no clearing in 1980”), without changing any request or response. Both calibration versions are retained; reviewed calibration passes 4/4. This is a postregistered reliability extension, not independent human gold.
- Verification: the complete non-storage suite passed 597 tests in an environment allowing native parser subprocesses; the sandbox-only run had 596 passes and one parser subprocess failure, which independently passed outside that sandbox. Ruff passed; mypy passed 241 production source files. Later evaluator additions have their own focused regression checks and will receive final suite verification.
- Later analysis/verification checks: 604 non-storage tests passed (368 deselected), including 23 scale-experiment tests; Ruff and mypy (241 production files) passed. The full-run source/request replay is implemented separately and will run after completion.
- Fixed native20 source inspection is recorded without changing any automatic score. It identifies ambiguous original-context DROP questions, semantic-role errors, directly supported aliases and arithmetic, and residual judge overacceptance of time constraints. Add post-hoc question-clarity diagnostics using the same baseline-defined case subset for every arm; keep all 1,000 cases in the primary matrix.
- Full construction finished with exit 0: each qualified model has exactly all 6,566 source IDs and frozen inputs/code still verify. Retained source-fact records: 27B 26,063; 35B/A3B 23,182; 122B/A10B 37,396; 397B/A17B 32,085. Failed sources respectively 59, 16, 8 and 10, all preserved. This is source coverage and protocol/provenance validation, not a claim of expert factual precision.

Prices checked 2026-10-21: https://help.aliyun.com/zh/model-studio/model-pricing ; https://help.aliyun.com/zh/model-studio/qwen3-8-27b ; https://help.aliyun.com/zh/model-studio/deepseek-r1-distill-qwen-1-5b .

### Retrieval-budget correction and external interruption

- The completed v2 packet audit found 5,000/5,000 provenance/budget-valid packets, but zero rendered two-hop paths in every graph arm. Production `GraphRetrievalBranch` and the offline adapter both admitted as many seeds as the entire node limit. At 200 seeds / 200 nodes the traversal cannot add an unseeded bridge. This is a retrieval-budget defect, not evidence of poor graph construction by a particular model.
- Preserve v2 packets/evaluations and a 299-file runtime snapshot. v3 reuses exactly the same completed construction, original sources, embeddings, reader and judgment prompts. The only retrieval change reserves expansion capacity: at most `max(1, max_nodes // (max_hops + 1))` ranked seeds; zero-hop retains the full node allowance. The 200-node, 400-edge, two-hop, 12-source and 14,000-character limits are unchanged. No model-specific or outcome-fitted settings.
- A synthetic regression with 300 vector-seeded distractor nodes failed before the fix: the unseeded Aster → Mira → Oslo evidence disappeared. It passes after the shared production/offline seed-budget fix. The previous source ranking and fusion remain unchanged.
- The provider then returned HTTP 400 with explicit `Arrearage`, an account billing denial. All three API/orchestration processes were stopped. 487 confirmed billing-denied launches are retained in v2 and excluded from v3 successful reuse; ambiguous launches remain immutable and are never silently retried. v3 adds an account circuit breaker that stops new launches and drains already submitted requests.
- Construction and the complete RAG assessments remain available. Do not infer model comparisons from the subset whose identical packets reuse RAG assessments. Until billing is restored and all graph assessments/audit finish, graph semantic-quality cells must read incomplete/N/A.
- Copied launch records are counted once by their full immutable record digest, including original launch time. All excluded protocol/graph diagnostic calls and billing/ambiguous records remain in the cumulative cost ledger.
- Post-fix checks: 611 non-storage tests passed (369 storage tests deselected); Ruff passed and mypy passed 241 production files. Real-store verification is blocked by Docker VM disk space reporting 0 MiB. The attempted local test services were stopped; no data was pruned. The new real-store saturation regression is pending execution.

- v3 offline verification completed: 26,264 construction source records, 5,000 packets and 31,567 call references verified. All call ledgers including excluded v2 billing failures passed integrity/cost replay; no paid processes remain. The complete RAG assessments were reused with identical evidence. The four graph reasoning caches only cover 27/25/28/28 identical-to-baseline packets, so no full graph quality estimate is published.
- Delivered a clearly marked progress report and a seven-sheet Excel workbook: 5,000 question/arm rows and all 6,566 original passages verified against the exported XML; formula recalculation, copied-call cost reconciliation and visual checks passed. The final comparison/audit and real-store verification remain unchecked because of the external billing/disk blocks.

- User-requested provider recheck initially returned HTTP 401 invalid_api_key. After the user supplied a replacement credential, the fixed reader and primary reasoning judge both succeeded. The judge probe first rejected disabled thinking; the required formal thinking parameters succeeded without any protocol change. All diagnostic attempts are retained in the immutable ledger. Resumed v3 checkpoints with concurrency 16; final result checkboxes remain pending.

- Native scheduling incident: replacing the serial supervisor also caused the execution environment to terminate its API child. Twelve launches have no stored response (six original blind judgments, five original semantic judgments, one reader). All launches are preserved and are not automatically resubmitted. The restarted scheduler overlaps original and reasoning stages without further process adoption. A secondary analysis will exclude the union of reader-execution failures uniformly across all arms, while leaving the primary all-case inventory unchanged.

### Final offline completion — 2026-10-21

- All five unique retrieval arms finished 1,000 original and 1,000 reasoning assessments plus the same fixed 100-case secondary audit. Failed responses remain unresolved. Four graph models each used all 6,566 sources; pure RAG remains a shared model-independent baseline.
- Full replay passed: 26,264 construction records, 5,000 packet references, 52,156 call references, 4,882 unique original/reasoned records each and 494 unique audit records. Additional verification passed inherited file hashes and all old/new call ledgers, including excluded billing failures.
- Deduplicated total: 53,789 request attempts, RMB 892.9199748 known list-price estimate, 651 attempts with unknown cost. All API phases ended; the temporary credential was removed. The estimate is not an account invoice.
- The original 611 non-storage tests, Ruff and mypy checks remain valid for unchanged frozen production/evaluation code. This continuation changed only reporting helpers and documentation. `git diff --check` passed. Real-store saturation regression remains pending because the Docker VM disk had zero free space; this offline completion is not production integration acceptance.
- Final seven-sheet workbook independently verified all 5,000 case rows, all 6,566 complete sources, headline/dataset rates, paired intervals and cost totals against exported XML. Formula recalculation, shared-baseline references and all sheet previews passed. Intentional empty answer strings export as blank cells; unknown values remain explicit n.a. Long text is retained in cells and can be expanded in the formula bar.
- Four deterministic same-source cross-model construction inspections and one deterministic disagreement variant per arm supplement the prior native reviews without altering automatic scores. The union of two reader-call failures was excluded uniformly only in a secondary sensitivity table; the main matrix is unchanged.
- All twelve paired 95% intervals for useful evidence, sufficient evidence and supported correctness cross zero. This run does not demonstrate stable overall graph gains or monotonic model-size benefits. Source review finds role, type, event-state and polarity errors despite valid quotations, as well as missing reference bridges and judge inconsistencies. These limit any claim of KG factual accuracy.
- Final deliverables: `docs/full-scale-retrieval-report.md`, the verified `retrieve-quality.xlsx`, machine-readable CSV/JSON, and two standalone PNG/SVG figures. The earlier progress report/workbook remain marked historical.
