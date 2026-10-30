# Source-grounded construction API validation plan

**Goal:** Reduce unsupported graph assertions and recover useful evidence bridges, then measure evidence sufficiency with the unified retrieval implementation. Semantic equivalence, not answer exact match, is the target.

**Execution:** Native in this chat. The user authorized API use up to 5,000 attempts and RMB 500 total; these are ceilings, not spending targets. Use only existing public data and cached embeddings. No accepted corpus migration or production publication.

**Spec:** docs/design.md, docs/contracts.md, docs/revisions-and-publication.md; original Tasks 9 and 19; docs/source-role-quality-report.md and docs/retrieval-quality-report.md.

## Stages and decision boundaries

- [x] Store the supplied credential outside Git with owner-only permissions; record expanded authorization without exposing the secret.
- [x] Complete the frozen 20-source comparison without changing its prompts or hiding invalid outputs. 100 calls, RMB 0.57135 at list prices; retain strict protocol failures and source rejection diagnostics.
- [x] Add a separate source-only assertion repair experiment. A model repair is a proposal; deterministic provenance checks still apply. Never use the evaluation reference inventory or QA answer to repair candidates.
- [x] Validate parallel scheduling, cumulative budget accounting across earlier phases, idempotent checkpoints, unknown-charge handling, failure denominators, and absence of reference/answer leakage before an expanded run.
- [x] Freeze 16 unseen questions and their 228 context documents before expanded calls, excluding pilot sources. Also freeze the evidence protocol: blind reader, semantic evidence judge, shared retrieval/fusion, 12 passages/12K characters plus at most 2K path characters.
- [x] Complete the expanded construction, fixed second-model audit and evidence comparison. Construction judges must not select the graph's retrieved edges.
- [x] Complete an additional, explicitly post-registered role-defined audit of all 228 sources with the 35B model and the already fixed 16-source subset with 397B. Trigger: the original judge accepted publisher credits as authorship on `d-04cf38b4dd07fe5707d03967`. Give identical predicate definitions to every blinded arm; preserve the original results and report disagreements. This changes evaluation clarity, not candidate facts or retrieval selection.
- [x] Verify the actual production extraction client against the cloud provider. Add explicit JSON Object / thinking configuration, preserving default wire behavior, source validation, scope and cache compatibility. Compact formats passed; full-format failures remain reported. No silent fallback.
- [x] Add separate reference-free evidence review after observing reference-aware judge bias; inspect all 16 questions' relevant original passages. Preserve ambiguity, ordinary inference, missing bridges and model errors separately.
- [x] Review representative gains, losses and model-judge disagreements. Report supported conclusions and unresolved failure modes; no experimental default promotion justified.
- [x] Run appropriate tests/lint/type checks, document actual calls/cost and data/code hashes, and delete the temporary credential when the authorized run finishes.

Final verification: 76 provider/experiment unit checks and 7 real-store
checks passed; Ruff and mypy (241 source files) passed. The storage
run includes legacy, semantic-guidance and explicit JSON Object profiles, plus
independent support and homonym boundaries. Owned Compose services were stopped.

Final run: 1,813 API attempts, RMB 15.9208542 known list-price usage;
RMB 16.0681782 including full reservation for 3 unknown-usage attempts.
The temporary credential was deleted and absent from tracked/trackable files.
See `docs/source-role-quality-report.md` and `.cache/source-role-analysis-final.json`.
This completes the experiment and compatibility work, not Task 9's general
semantic-quality acceptance or deployment of the experimental repair pipeline.

## Review focus

Literal provenance checks are separate from semantic correctness. A malformed fact must not silently erase unrelated valid evidence in a new experimental salvage arm; retain rejection diagnostics. A source mention does not establish entity identity or a creator role. Additional context cannot grant permissions, select tools, or silently become the ownership source of an assertion. Correcting predicates/qualifiers must preserve the quote that actually supports the changed assertion. Report inference and missing context distinctly from contradictions. Same-family model judgments are provisional and need original-source inspection.
