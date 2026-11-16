# Confirmed semantic evaluation matrix

User confirmed the metric proposal and six model sizes on 2026-11-02, then required
all model inference to use APIs. This supersedes v2's four arms and fixed reader.

## Fixed scope

- Exactly `vector_rag` (dense only) and `domain_kg` (existing complete algorithm).
- Six cloud working models: 1.5B, 7B, 27B, 35B, 122B, 397B. Per model, construct a
  separate graph and use that same model for answering and any generative retrieval.
  Existing retrieval has no generative LLM; shared API embeddings are separate.
- Same 397B judge for every cell; semantic retrieval at 1/3/5/10/12 and semantic QA.
- Required-information coverage, answer completeness and unsupported-claim rate
  added to the existing sufficiency/correctness/grounding/citation metrics.
- Literal IDs/quotes validate provenance only, never answer/retrieval quality.
- Exact Alibaba HTTPS endpoints only. No localhost, local model library, model
  downloads, substitute model, or fallback. Local indexing/statistics are permitted.
- Preserve v2 artifacts; new runs use a new protocol version and separate directory.

## Implementation and proof

- [x] Add failing regression tests for cloud endpoint enforcement, model propagation,
  dense-only baseline, same-model construction and semantic metrics.
- [x] Extend the existing evaluation contract and runner, retaining graph analysis,
  immutable requests, source authorization and production extraction validation.
- [x] Add immutable API calls and per-model construction at the responsible boundary.
- [x] Freeze question-only requirements and update judge calibration fixtures.
- [x] Verify tests, lint, offline matrix replay and API model availability; report
  exactly which API capabilities and evaluation phases actually ran.

Not included: production publication, changing retrieval/construction algorithms,
local model serving, or treating a small development sample as a held-out benchmark.

## Verification outcome (2026-11-02)

58 focused/regression tests pass; Ruff passes. Six-by-two synthetic replay writes
12 packets and keeps all unjudged quality values unknown. Final frozen input is
`.cache/kg-quality-v3-frozen`; final fixture replay is `.cache/kg-quality-v3-fixture-frozen`.

Cloud capability probe: 35B/122B/397B and shared embedding pass; 1.5B has empty
final content, 7B returns model-not-found/access 404, and 27B extraction violates
the production schema. The full matrix is correctly blocked; no model substitutions.

The 12 synthetic judge cases pass the final semantic parser using saved real API
responses. Earlier failures remain archived. Calibration was iteratively refined
(prompt separation and primitive-label aggregation; partial source relevance counts
as useful, never as sufficient). This is development calibration, not independent
validation or evidence of real-corpus judge accuracy. Full matrix execution and
algorithm-quality conclusions remain out of this completed protocol implementation.

## User-authorized bbio amendment (2026-11-02)

After the Alibaba 1.5B/7B failures, the user explicitly allowed the existing bbio
Ollama server and provided SSH authentication. This supersedes the Alibaba-only
endpoint restriction for those two rows; local inference remains prohibited.

- Reuse remote Ollama 0.5.7 and the existing Qwen2.5 7B Q4_K_M weights; download
  Qwen2.5 1.5B Q4_K_M only on bbio. Pin both model digests, keep the service private,
  and use an SSH tunnel without persisting the login password.
- Extend the evaluation transport, retaining messages, 16K context, output budgets,
  semantic metrics, independent per-model graphs, shared embedding and fixed judge.
  Record native replies and verify weights before/after calls. No cloud credential
  is sent to the SSH-forwarded service.
- Remote CPU calls are serial and bounded to 600 seconds. The operator profile's
  upper timeout bound is extended to 600; its 60-second default is unchanged.
  Existing Alibaba time limits remain unchanged and are disclosed in comparisons.
- New v4 freeze: `.cache/kg-quality-v4-frozen`. Preserve the v3 artifacts and failed
  requests; no relabeling of older models or reuse of their graphs.
- Code proof: 116 focused/adjacent tests, Ruff and the profile type check pass.
  Live readiness probes are separate from retrieval/QA scoring; see
  `docs/bbio-small-model-readiness-2026-11-02.md` for their outcome.

This amendment does not resolve the independently diagnosed whole-span negation
validation bug or authorize claiming that the full six-model matrix passed.
