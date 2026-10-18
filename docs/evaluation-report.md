# Local platform evaluation

The local evaluation runner is available as:

```sh
TIKTOKEN_CACHE_DIR="$PWD/.cache/tiktoken" uv run --no-sync python -m eval.run \
  --config eval/pilot.toml --output .cache/evaluation
```

Start the existing four smoke stores and verify their replica set first. The
runner verifies their configured hard memory/swap limits and never starts a
model or service. It freezes the case/config hashes and writes the command log
and report outside Git. A failing run cannot retain an earlier report as its
current result. Unit tests exercise duplicate-source ranking, no-answer handling
and invalid cutoffs.

## Measured local run, 19 October 2026 (Asia/Shanghai)

The first explicit opt-in run passed in 89.86 seconds: 12 questions (two
development, ten held out) over three tiny synthetic materials, each exercised
in vector, lexical, graph, fast and deep modes. All 41 returned citations resolved
to exact source spans, no hidden-material result was returned, and both absent
filter scopes and hidden-only scopes remained empty. There were 30 synthetic
query embeddings, zero generative query calls and zero live model calls.

A repeat against committed application revision `f461531` passed in 139.69 seconds
with an empty application diff, the same corpus/config hashes, 41 valid citations
and the same ranking metrics. Mean search wall times were 1.032 s (vector),
0.871 s (lexical), 0.792 s (graph), 1.416 s (fast) and 1.885 s (deep). Timing is
reported per run; the result does not establish a production latency target.

The final current-configuration run passed in **112.83 seconds** against
verification revision `0856abee7440be344b096d316b0fc75db35bc174`, with an empty
working-tree diff. Its application source tree is identical to image revision
`02a23d8`. The case/config hashes, ranking metrics, 41 exact citations and zero
unauthorized results were unchanged. The current report is
`.cache/evaluation/report.json`; its log and a preserved report copy are under
`.cache/verification/`. The following timings are from this final run.

These are source-level metrics at **top_k=1** using controlled three-dimensional
embeddings and a deterministic extractor. They do not measure real semantic
embedding/extraction quality, clinical accuracy, production throughput, or the
acceptance document's 100,000-chunk workload.

| Mode | Answerable-source recall@1 | No-answer false positives | Mean search wall time |
| --- | ---: | ---: | ---: |
| Vector | 0.714 | 1 | 0.807 s |
| Lexical | 0.857 | 0 | 0.737 s |
| Graph | 0.429 | 0 | 0.739 s |
| Fast | 1.000 | 1 | 1.318 s |
| Deep | 0.857 | 1 | 1.657 s |

Graph-only retrieval cannot recover plain text without graph assertions. The
unfiltered no-answer query also demonstrates that similarity/rank alone does
not establish supporting evidence. Relevance cutoffs require separate real
development data; these test vectors must not tune production thresholds.

## Open evaluation gates

The local report is an executable platform regression, not full Task 19
acceptance. The 120 independently checked pilot questions, real-model corpus
quality, same-budget reranker/upstream comparisons, multi-user team pilot and
large-workload measurements remain separate. Real corpus evaluation depends on
Task 9; external identity/client acceptance and deployment are deferred in the
current local-first scope.
