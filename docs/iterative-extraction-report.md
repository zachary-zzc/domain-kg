# Bounded iterative extraction experiment

Reviewed 19 October 2026 (Asia/Shanghai). **Stopped after five unsuccessful
attempts, as requested. No sixth model experiment was run.** All owned model
processes stopped; no graph was published or production ingestion code changed.

## Method and stopping criterion

An attempt is one diagnostic/change/retest strategy, not an individual HTTP
request. Earlier failed routes count toward the same limit; changing the model
does not reset it. The persisted controller ledger rejects a sixth attempt or
overwriting an existing one.

The frozen cases comprise two previously diagnosed public-PDF development
snippets, one known positive regression and three new synthetic contrasts
(passive direction, negation and unrelated entities). The relation vocabulary
is limited to `is_a`, `produces` and `uses`. The development reference contains
three target relations. References are provisional, authored by this agent,
and are not independent clinical annotation. The case SHA256 is
`837e2d8aa35ce545f0392e86ba5dfe0b7e171859d4984e2a4b41f6ee5506d810`.

The fixed gate requires all three development target relations and no extra
incorrect claims, followed by passing the known-positive/frozen contrasts.
Failed calls remain explicit and cannot count as correct empty answers. Once a
strategy fails development, further contrast spending is stopped. No strategy
qualified for a complete frozen-set evaluation.

## Observed outcomes

| Attempt | Strategy | Development result | Outcome |
| --- | --- | --- | --- |
| 1 | Existing 4B: baseline versus decomposed extraction | No completed response for semantic scoring; the first baseline and mention requests each reached 120 s | Runtime failure, excluded from semantic conclusions |
| 2 | 0.8B: mentions → relations → same-weight independent review → bounded repair | Final accepted: 0 correct, 0 incorrect, 3 missed | Failed; empty output does not pass |
| 3 | 0.8B: minimal flat relation JSON, finite source refs and program-generated identities | 2 correct, 1 incorrect, 1 missed | Best result, still failed |
| 4 | 0.8B: ask one predicate at a time, clarify passive direction and “needed for” | 2 correct, 3 incorrect, 1 missed | Worse precision; failed |
| 5 | 0.8B: copy literal source verbs, then use narrow auditable clause patterns | 0 correct, 0 incorrect, 3 missed; both responses were empty | Failed; stop rule reached |

In attempt 2's first development snippet, the draft contained one correct and
four incorrect distinct relations. The reviewer marked wrong relations as
supported, marked the correct production relation unsupported, and supplied
incomplete decisive refs. Canonical ref checks rejected the invalid approvals;
the result nevertheless lost the correct fact. Repair repeated existing bad
candidates. This is evidence against treating role separation with the same
0.8B weights as an independent correctness oracle.

Attempt 3 recovered the category relationship and the coeliac category statement.
It still missed the cross-line passive production relationship and incorrectly
converted a need/function statement into a `uses` relation. Attempt 4 introduced
additional unsupported classification/production claims. Attempt 5 reduced the
allowed output space but the model abstained completely; it did not demonstrate
better extraction.

Attempts 3–5 evaluate relations only. Entity types are explicit `concept`
placeholders, not inferred medical labels. Their results cannot establish
complete schema-driven KG quality. The final clause mapper is a limited English
heuristic, including an immediately preceding subject/pronoun pattern; it is
not general coreference or semantic entailment. It is not enabled in ingestion.

## Cost and operational evidence

| Attempt | Script wall time | Attempted local HTTP calls | Peak sampled native RSS |
| --- | ---: | ---: | ---: |
| 1 | 372.367 s | 12 | 2,102,427,648 bytes |
| 2 | 395.765 s | 12 | 773,947,392 bytes |
| 3 | 22.594 s | 2 | 850,477,056 bytes |
| 4 | 61.653 s | 6 | 893,370,368 bytes |
| 5 | 14.142 s | 2 | 802,865,152 bytes |

Script times include startup and, in attempts 1–2, baseline and interrupted
regression work. They are not equal-workload latency comparisons. Attempt 1's
12 HTTP attempts include shutdown/connection failures; **zero completed model
responses were available for semantic comparison**. Its original report's
`completed_experiment` value is not a success signal; the separate assessment
explicitly invalidates it for quality comparison. Later runners record case
failures and halt after two consecutive timeouts.

The 0.8B runs total 22 attempted HTTP calls and 494.154 seconds of script runtime,
within the inherited 48-call / 1,800-second ceiling. Both cached model hashes
were checked. Runs used two CPU threads, no GPU, one model at a time and 4,096
context. RSS limits stayed at 1.25 GB for 0.8B and 3.5 GB for 4B. There were no
new weights/dependencies, external API calls, database writes or graph publication.

Final engineering checks: **385 unit/protocol tests passed in 13.15 s**; Ruff,
strict mypy for 239 production modules and `git diff --check` passed. This proves
the tested program boundaries, not model semantic accuracy. Production source,
Docker image and the earlier platform acceptance are unchanged. Storage
integration gates were not rerun for this standalone, nonpublishing experiment.

## Artifacts and conclusion

Ignored local artifacts:

- `.cache/iterative-kg/iterations.json`: the five-attempt ledger.
- `.cache/iterative-kg/summary.json`: consolidated outcomes and hashed reports.
- `.cache/iterative-kg/attempt-{3,4,5}/`: pinned manifests, requests, responses and reports.
- `.cache/recursive-kg/20261018T193214-4b-f7e8f653/`: attempt 1 and its invalidation assessment.
- `.cache/recursive-kg/20261018T194008-0.8b-a4a57819/`: attempt 2, including interruption.

The useful measured change is reducing protocol/output burden; it improved the
small development result. Adding same-model judging or more predicate questions
did not reliably improve it. Under this budget and these provisional cases,
the 0.8B pipeline remains unqualified. The 4B experiment establishes a runtime
limitation on this run, not a semantic quality verdict. Further model/independent
verifier work requires a new explicitly scoped experiment; no automatic loop is
left running. Task 9 and full-document extraction remain open.
