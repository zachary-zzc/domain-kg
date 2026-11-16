# Retrieval and QA benchmark after connected extraction

Tested 1 November 2026 (Asia/Shanghai). **The revised graph has not demonstrated a
retrieval or QA improvement in this experiment.** All three variants retrieved
all annotated support for the same 64 of 81 answerable questions. Revised graph
answer exact match was 36% and token F1 was 48.1%, versus 42% and 53.8% for text
retrieval. String-based scores penalize some valid paraphrases; the supplementary
semantic review below is provisional and has unresolved judgments.

## Frozen test

The benchmark selected **100 questions, 25 each from SQuAD 2.0, HotpotQA, MuSiQue
and DROP**, by SHA-256 ordering with seed `connected-qa-2026-11-01-v1`, before new
model calls. There are 81 originally answerable and 19 originally unanswerable
questions. Retrieval searches the global pool of **747 associated passages**, with
distractors; it is not supplied with each question's original evidence subset.
The same 754 source windows feed the old and revised production extractors.

Construction and answering use the configured Alibaba `qwen3.6-35b-a3b` API.
Construction retains the configured source-name/role protocol, JSON-object output,
16,384 context and 8,192 completion limits. The blind reader has a 2,048-token
output limit, temperature zero and thinking disabled. Neither construction nor
answering receives gold answers or support labels. Exact identical reader requests
reuse saved responses across arms, avoiding another paid call and random variation.

- **Fast:** cached Alibaba `text-embedding-v4` vectors plus production BM25 and RRF.
- **Old graph:** the frozen pre-change extractor, then graph-assisted retrieval.
- **Revised graph:** the current connected/open extractor, then the same retrieval.

All arms retain identical source text. A document contributes graph observations
only if **all** its windows complete successfully. This isolates the incremental
contribution of available graph facts; it does not simulate publication of a
partially ingested material. Old isolated observations remain in graph seed
selection so they are not silently removed from the baseline.

The existing offline adapter reuses production traversal, seed selection, ranking,
fusion and evidence packing. Limits are 50 candidates per branch, two graph hops,
200 nodes, 400 edges, 12 returned passages and 14,000 source/path characters.
Graph paths must have selected source evidence and preserve qualifiers. Dataset-
scoped equal-name joins are retrieval hints, not verified entity resolution.
There is no generative query expansion or reranker.

## Quality results

| Metric | Fast | Old graph | Revised graph |
| --- | ---: | ---: | ---: |
| Complete reference evidence (81 answerable) | 79.0% | 79.0% | 79.0% |
| Mean reference-support recall (81) | 89.8% | 89.8% | 89.8% |
| Answer exact match (100) | 42.0% | 37.0% | 36.0% |
| Answer token F1 (100) | 53.8% | 48.8% | 48.1% |
| Citations cover all reference support (81) | 67.9% | 70.4% | 71.6% |
| Correct abstention on originally unanswerable questions | 7/19 | 6/19 | 6/19 |
| Invalid reader responses | 2 | 1 | 0 |

No individual question gained or lost complete annotated support relative to fast.
The revised graph kept the same selected document set as fast on
87/100 questions.
Compared with fast, it gained 3 exact matches and lost 9. Some changes are output
wording, not facts: “five games” versus “five”, duplicated aliases, and “more than
450” versus “450”. Original answer aliases and scoring rules were not changed.

The multi-hop limitation is clearest on MuSiQue: all variants retrieve complete
reference support for only 7/20 answerable questions (35%). Each dataset below has
25 questions; the F1 denominator also includes its originally unanswerable cases.

| Dataset: answer token F1 | Fast | Old graph | Revised graph |
| --- | ---: | ---: | ---: |
| SQuAD | 53.2% | 54.4% | 45.3% |
| HotpotQA | 64.6% | 62.1% | 59.7% |
| MuSiQue | 37.4% | 21.5% | 33.6% |
| DROP | 60.1% | 57.4% | 54.0% |

The revised graph improves over the old graph on MuSiQue, but remains below the
text baseline on this sample. The small strata do not support broad model rankings.

There are also substantive failures. On the first-commercial-textile-device
question, fast correctly abstained; the revised graph answer was “water pump”.
The source describes the first commercial **steam-powered** device, and the added
graph paths concerned textile-museum locations. The secondary reviewer also marked
that answer unsupported. On a multi-hop geography question, the reader substituted
a district's region for a museum city's region after missing the city-to-region
source. These examples do not establish a causal effect across the entire sample.

### Supplementary semantic review

Alibaba `qwen3.5-397b-a17b` reviews meaning and source support with arm/model labels
hidden. This was added after the aggregate fast scores and deterministic spot check
exposed exact-match sensitivity; it is **not a preregistered primary metric or
independent human accuracy**. A failed preflight incorrectly claimed a graph path
when no path was supplied. The formal reviewer protocol then explicitly constrained
that output field; the failed response remains recorded. No verdict was repaired,
no gold label was changed, and formal invalid judgments were not retried.

| Review count | Fast | Old graph | Revised graph |
| --- | ---: | ---: | ---: |
| Valid judgments / 100 | 99 | 94 | 86 |
| Supported-correct answers found / 81 | 52 | 50 | 46 |
| Unresolved judgments among those 81 | 1 | 4 | 11 |
| Supported-correct on 67 mutually judged answerable cases | 42 | 43 | 44 |

Unresolved judgments are unknown, not semantic errors or successes. Counts of
supported answers over all 81 are observed positives, not full-sample accuracy.
The common subset removes protocol-missing cases and is therefore conditional;
these small differences cannot establish a general QA advantage.

## Performance and construction reliability

| Measurement | Fast | Old graph | Revised graph |
| --- | ---: | ---: | ---: |
| Local retrieval core median (ms) | 2.47 | 33.49 | 40.00 |
| Local retrieval core P95 (ms) | 3.24 | 46.63 | 93.75 |
| Alibaba answer median (s) | 0.83 | 0.91 | 0.98 |
| Alibaba answer P95 (s) | 1.32 | 1.28 | 1.42 |

Local retrieval timing uses three complete replays after construction stopped,
300 timing samples per arm, with exactly unchanged returned evidence. It excludes
query embeddings, cached dense ranking, database/network I/O, index initialization
and answer generation. API answer time is measured separately. These are not
end-to-end service latency or throughput guarantees. Twenty fresh production-client
Alibaba embedding calls measured median **0.304 s** and
P95 **1.586 s**; quality comparisons share the existing,
hash-verified 1,024-dimensional vector cache.

| Construction measurement | Old | Revised |
| --- | ---: | ---: |
| Node observations | 5835 | 4460 |
| Isolated node observations | 1728 | 0 |
| Relationship observations | 3372 | 4382 |
| Distinct literal predicate labels | 6 | 1958 |
| Failed source windows / 754 | 10 | 109 |
| Documents with complete graph extraction / 747 | 737 | 638 |
| Build wall time, five workers per arm (minutes) | 18.1 | 20.1 |

The two builds ran concurrently. Revised relationship observations increased
30.0% and isolates fell to zero, while failed windows rose from
10/754 (1.3%) to 109/754 (14.5%). Missing required qualifier fields and unsupported
qualifier names were major failure causes. Literal predicates include synonyms,
phrases and spelling variants; 1,958 labels do not establish 1,958 useful meanings.
Counts above exclude all graph observations of any failed document; including
successful windows of those documents would give 4,384 revised relationships
instead of 4,382, but would not match the benchmark's document boundary.

## Usage, limits and reproducibility

| Stage | New API responses/calls | Input tokens | Output tokens | Unknown usage |
| --- | ---: | ---: | ---: | ---: |
| baseline | 1,096 | 1,599,460 | 1,096,405 | 0 |
| revised | 1,173 | 1,415,048 | 1,223,383 | 0 |
| answer | 284 | 786,865 | 20,170 | 0 |
| judge | 285 | 970,381 | 72,991 | 0 |
| embedding | 20 | 409 | 0 | 0 |

Judge usage includes the single failed preflight. Shared response replays and
historical document/query embeddings are not charged again in this accounting.
No money figure is inferred from historical list prices; this is reported token
usage, not a provider invoice. The repeated local timing checks make no API calls.

This is a reused public development sample, not an unseen holdout. Public model
training contamination is unknown. Some SQuAD/DROP questions depend on their
original paragraph context, and originally unanswerable questions can have alternate
answers elsewhere in a pooled corpus. Reference evidence coverage and exact match
are diagnostics, not proof of semantic correctness. The 12-case native spot check
and changed-answer review leave the reference labels unchanged. No hormone-specific
QA accuracy or online permission/publication performance is claimed by this test.

All 100 planned reader and reviewer records per arm are accounted for, including
failures. Frozen dataset/code definitions and reused embedding hashes verified;
all three timing replays returned identical evidence. The 15 existing scoring and
retrieval-adapter regression checks passed. Production source still matches the
verified implementation snapshot from the preceding task; no accepted KG was
published or modified by this benchmark.

Raw dataset records, calls, timings, source snapshots and scoring artifacts remain
in ignored `.cache/connected-qa-2026-11-01/`. Key artifacts are `definition.json`,
`qa-protocol.json`, `summary.json`, `scored-rows.json`, `paired-changes.json`,
`semantic-paired.json`, `accounting.json` and `verification.json`; local scripts
in that directory reproduce the run. Dataset origins, licences and original source
hashes are documented in [the public QA corpus report](public-qa-benchmark-report.md).

The next useful targets are extraction output-schema compliance, retrieval of
missing intermediate evidence, and relevance of the graph paths shown to the
reader. Increasing graph size alone did not improve this benchmark.
