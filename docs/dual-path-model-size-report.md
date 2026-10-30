# Cloud model size: actual dual-path construction and retrieval

2026-10-20. **Stopped at the requested budget; partial results audited.** All five model-dependent graphs were constructed. Formal answering attempted 8,299/20,000 model/question/arm pairs. After the internal budget stop, the user explicitly retained the RMB 100 ceiling and requested a summary. No additional cloud calls followed that instruction. No full PDF has been built and no production KG has been published by this experiment.

**Scoring clarification, 2026-10-20:** EM below is normalized exact match, not semantic content accuracy. A subsequent fixed 60-question native semantic audit of stored V6/U12 responses found substantial undercounting from paraphrases, aliases, protocol coupling, and several annotation ambiguities. On that same 60-question sample, U12 EM → confirmed content correctness was 46.7% → 71.7% (397B), 48.3% → 68.3% (122B), 40.0% → 61.7% (35B), 43.3% → 66.7% (27B), and 10.0% → 31.7% (1.5B). This is a supplemental sample, not a full semantic rescore of the 353/1,000 questions. See [the semantic review, its limits, and corrected evaluation criteria](semantic-content-review.md). Future primary answer evaluation uses semantic/factual correctness, with citations and format reported separately.

目前能支持的结论是：**1.5B 不适合当前抽取协议；27B 已能较稳定地完成结构化抽取，但整个问答链路仍未达标。27B 与 122B/397B 的结果接近，参数更大没有在这份测试里带来单调提升。7B/14B 尚未实测，不能宣称 27B 就是最小门槛。** 双路径相对 V6 有小幅收益，但相对更深的 V12 尚无一致优势。

## What is being compared

Original source paragraphs feed two indexes: a common `text-embedding-v4` vector index (1,024 dimensions), and a separate source-grounded relation graph extracted by each chat model. Questions independently retrieve vector candidates and lexical entity-seeded graph candidates. Readers receive original sources and cite them; generated graph assertions are never substituted for original evidence.

The frozen main matrix is 1,000 questions × five models × four arms = 20,000 planned answers. `V6` retrieves six vector paragraphs; `V12` is a deeper vector control; `G6` retrieves six graph paragraphs; `U12` retains the entire packed V6 baseline and appends graph candidates. A common 12,000-character source budget applies. There is no LLM filter. The same chat model constructs its graph and answers its questions, so answer differences measure practical end-to-end behavior; retrieval evidence recall is reported independently of the reader. Models also differ in generation, architecture and prompt compatibility: this is not a controlled experiment changing parameter count alone.

The original downloadable source library is 219,604,885 bytes. The actual global search corpus for these 1,000 questions includes their source and distractor paragraphs: **6,566 distinct dataset-scoped paragraphs / 3,517,255 UTF-8 text bytes**. These are different quantities. Public source attribution, licenses, and download hashes remain in `.cache/public-qa-2026-10-19/data-manifest.json` and its packaged source bundle. The builder does not receive questions, answers, supporting-fact labels, or reasoning decompositions.

Public QA has annotated supporting sources, not an exhaustive relation gold set. Exact quote/reference validation is therefore reported as grounding, never as semantic fact precision. The original 175 unanswerable labels were defined inside question-specific contexts; after pooling sources globally, refusal scores against those labels are diagnostic. Answerable-only scores over 825 questions are reported separately.

## Excluded fictional development pilot

Four newly created fictional chains contain 20 explicitly annotated target relations and 12 questions, including three-hop joins and negation. This is a small controlled development set; all four questions in a chain share sources. It is not a blind public test or a reliable estimate of real-world accuracy. A perfect 12/12 score has a Wilson 95% lower bound of about 75.8%.

Three bounded prompt/format strategies were tried before freezing the formal algorithm. They exposed batch-wide interpretation of a per-source limit, duplicate JSON keys, and difficulty copying opaque source IDs. The final profile uses exact short source references remapped by the server, a shared worked example, explicit per-source processing, strict duplicate-key rejection, and a 2,048-token answer budget for every model. These adaptations are identical across sizes; no formal answer has been used for tuning.

| Model | Target relation precision / recall | V6 answer EM | V12 answer EM | G6 answer EM | U12 answer EM | U12 gold citation completeness |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen3.5-397B-A17B | 100% / 100% | 8/12 | 9/12 | 12/12 | 12/12 | 12/12 |
| Qwen3.5-122B-A10B | 100% / 70% | 8/12 | 9/12 | 9/12 | 9/12 | 9/12 |
| Qwen3.6-35B-A3B | 100% / 90% | 8/12 | 9/12 | 9/12 | 11/12 | 11/12 |
| Qwen3.8-27B | 100% / 100% | 8/12 | 9/12 | 12/12 | 12/12 | 12/12 |
| DeepSeek-R1-Distill-Qwen-1.5B | 75% / 30% | 1/12 | 1/12 | 1/12 | 3/12 | 6/12 |

These are final strategy-3 pilot values. Earlier strategies remain separate immutable artifacts. In particular, 1.5B is now actually evaluated for both construction and answering rather than being excluded solely for Markdown formatting. Target-relation recall and final QA accuracy remain poor in this profile. Neither this pilot nor parameter count establishes a universal minimum model size; 7B/14B quality remains unmeasured under this key's currently accessible model IDs.

## Execution and limits

All inference is via the user-authorized Alibaba API. There is no local LLM, model download, storage-container restart, or production schema change. Numerical work is limited to one thread. Request construction is lazy and embedding checkpoints are read in small blocks to keep local memory bounded.

The budget is RMB 100 at documented list prices, including all three pilots and explicit reserves for missing usage. These are estimates, not an account bill or an assumption about free quotas. The initial formal embedding burst triggered HTTP 429 and stopped before any formal answer. Transport recovery preserves every attempt, reuses all successful requests, throttles requests per model, and retries only 429/timeouts/5xx up to twice. Content-filter, semantic and output-format failures are not retried. For embedding-only missing usage, RMB 0.05 per call upper-bounds the endpoint's documented ten inputs × 8,192 tokens × RMB 0.5 per million tokens; unknown usage is not treated as zero.

Original question, corpus, prompt and algorithm hashes remain unchanged. A separate transport manifest records the rate/retry repair. [Alibaba error documentation](https://help.aliyun.com/zh/model-studio/error-code) identifies `limit_requests` as request-rate limiting and recommends controlling call frequency.

## Completed construction: all 1,000 questions' source corpus

Every model attempted all 938 planned construction batches over the same 6,566 paragraphs. Semantic/protocol failures are retained; completed attempts do not mean a qualified graph. Exact source quotes and references are validated, but correctness, entity identity and completeness are not established by that validation.

| Model | Batches with valid graph JSON / 938 | Grounded relation records retained | Paragraphs with at least one retained relation / 6,566 |
| --- | ---: | ---: | ---: |
| 397B-A17B | 926 | 14,538 | 6,293 (95.8%) |
| 122B-A10B | 935 | 6,068 | 3,798 (57.8%) |
| 35B-A3B | 930 | 8,739 | 5,572 (84.9%) |
| 27B | 937 | 9,291 | 6,213 (94.6%) |
| 1.5B | 320 | 21 | 21 (0.3%) |

“Valid graph JSON” includes an empty facts list and outputs whose individual records all fail grounding. The 1.5B result therefore cannot be interpreted as a 34.1% usable graph: only 21 paragraphs ultimately have any accepted source-grounded record. A larger number of relations is also not automatically better; public QA lacks exhaustive semantic relation gold. This experiment does not qualify the production Task 9 gate.

## Comparable partial answers: the same 414 questions

The budget stop left 414 questions with all five models and all four arms attempted. This **post-hoc coverage-only intersection** includes errors in its denominators and does not select for correctness. It contains HotpotQA 98, MuSiQue 108, DROP 106 and SQuAD 102 questions. Of these, 353 are answerable under the original labels and 61 were originally unanswerable. The common cohort is not the originally planned 1,000-question completion and its strata are not exactly balanced.

The primary table below uses the **353 answerable questions**, exact-match answer accuracy, and gold supporting-source citation completeness. EM is strict: it requires a short answer to match one of the fixed normalized gold alternatives; F1 is reported separately. These values are not directly comparable to the former supplied-context benchmark or to the 12-question fictional pilot.

| Model | V6 answer EM | V12 answer EM | G6 answer EM | U12 answer EM | U12 answer F1 | U12 citation completeness |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 397B-A17B | 42.2% | 43.1% | 25.2% | **45.9%** | 56.5% | 53.3% |
| 122B-A10B | 43.3% | **45.9%** | 13.9% | 44.2% | 54.2% | 53.0% |
| 35B-A3B | 33.1% | 35.4% | 16.4% | **36.0%** | 46.9% | 50.7% |
| 27B | 41.9% | **45.6%** | 24.9% | 45.0% | 54.6% | 52.7% |
| 1.5B | **9.3%** | 6.2% | 0.0% | 8.8% | 14.6% | 20.1% |

The answerable U12 numerators are 162, 156, 127, 159 and 31 out of 353, respectively. Citation completeness is separate from correctness: a wrong answer can cite all annotated supporting sources. Citation metrics measure coverage of dataset gold supports, not expert verification that the answer is entailed or that no other valid supporting source exists.

For 397B, U12 has 18 newly correct answers and five regressions against V6; 27B has 14 gains and three regressions. Their unadjusted paired exact p-values are 0.0106 and 0.0127. Multiple models/comparisons and a partial completion cohort make these exploratory findings. Against V12, none of the five models has an unadjusted paired p-value below 0.05. A statistically supported monotonic model-size ranking has not been established.

### Retrieval coverage independently of answering

Retrieval was computed for **all 1,000 questions** after construction, so the following table uses all **825 answerable questions**, rather than the smaller answered cohort. “Complete” means the packed original-source context includes every annotated supporting unit. This is not final answer accuracy.

| Model-dependent graph | V6 complete evidence | V12 complete evidence | G6 complete evidence | U12 complete evidence |
| --- | ---: | ---: | ---: | ---: |
| 397B-A17B | 67.5% | 74.3% | 33.2% | 71.2% |
| 122B-A10B | 67.5% | 74.3% | 16.1% | 69.8% |
| 35B-A3B | 67.5% | 74.3% | 28.0% | 69.9% |
| 27B | 67.5% | 74.3% | 30.1% | 70.3% |
| 1.5B | 67.5% | 74.3% | 0.0% | 67.5% |

U12 preserves V6 evidence in every planned pair. This prevents the earlier candidate-eviction bug; it cannot guarantee unchanged answer accuracy. Extra irrelevant or conflicting evidence can still distract a reader. The deeper vector control retrieves complete gold evidence more often than U12 under this configuration. The graph recall is a deliberately bounded lexical/entity traversal, not a generative query planner; equal entity labels are retrieval hints rather than verified identity merges. The extraction schema and three-facts-per-paragraph limit also constrain graph coverage. These results concern this implemented configuration, not all graph RAG methods.

### Difficulty and dataset differences

U12 EM on the common cohort's answerable questions:

| Dataset | Answerable questions | 397B | 122B | 35B-A3B | 27B | 1.5B |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| SQuAD | 60 | 68.3% | 65.0% | 60.0% | 63.3% | 13.3% |
| HotpotQA | 98 | 57.1% | 56.1% | 49.0% | 56.1% | 10.2% |
| MuSiQue | 89 | 28.1% | 23.6% | 14.6% | 28.1% | 1.1% |
| DROP | 106 | 37.7% | 38.7% | 28.3% | 38.7% | 11.3% |

For 27B specifically, the MuSiQue U12 breakdown is 14/36 on two-hop, 10/32 on three-hop and 1/21 on four-hop questions. Its citation completeness falls from 50.0% to 9.4% to 0%. Multi-hop evidence completion and citation chains remain a material limitation even when extraction JSON is stable.

The 61 original no-answer cases remain separate diagnostics because pooling the corpus can expose additional evidence. U12 abstention correctness against those original labels is 26.2%, 32.8%, 29.5%, 31.1% and 9.8% in model-table order; these values are not validated global-corpus unanswerability accuracy.

## Final accounting, verification and status

Known usage across all three pilots and formal HTTP attempts is **RMB 60.3196562** at the recorded list prices, with **RMB 41.00 reserved** for 856 attempts lacking usage. Of those missing-usage calls, 720 are the listed-free 1.5B endpoint, 100 are embedding calls reserved at RMB 0.05 each, and 36 are paid-model calls reserved at RMB 1 each (including earlier pilots). The stop threshold was crossed by requests already in flight; known cost plus reserve is RMB 101.32. This is an internal conservative accounting value, not a bill or evidence that the account was charged over RMB 100. No new request was launched after the user's instruction to retain the ceiling and summarize.

The main run directory contains 14,839 HTTP attempts and 14,036 logical requests after selecting transport retries. Adding the 325 earlier-pilot calls gives 15,164 total HTTP attempts. Formal construction attempted all 4,690 planned model/batch pairs. Formal answering attempted 8,299 of 20,000 pairs (41.5%). The original 1,000-question accuracy remains **N/A**; unattempted answers are not silently included as completed results. The 414-question tables above are explicitly supplemental partial-cohort analyses.

Independent replay verified 12,989 original formal request hashes (4,690 construction plus 8,299 answers), all 8,299 answer scores, graph output parsing, source scope, gold-support recall, duplicate protection, and V6 preservation in every U12 context. The original frozen corpus, questions, prompts and algorithm hashes still match. The full unit/protocol suite passed **486 tests**; Ruff and strict production mypy checks passed. Credential/process cleanup is recorded in the delivery artifacts.

The earlier 1,000-question supplied-context QA results remain in [their own report](public-qa-benchmark-report.md); they must not be presented as this construction/retrieval benchmark. This experiment is an offline algorithm comparison, not the production LightRAG/Neo4j/Qdrant integration acceptance. No deployment, migration or schema upgrade was performed.
