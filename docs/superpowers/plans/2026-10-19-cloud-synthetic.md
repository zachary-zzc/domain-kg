# Synthetic-only cloud comparison

The original mixed public-medical/synthetic cloud run was rejected by automatic approval review before execution. Do not run it or send its medical text. This replaces that cohort with **only the 20 fictional k01..k20 passages and 12 fictional queries** authored in `eval/fusion_cases.json`. No PDF, patient material, h-prefixed passage, or private corpus is sent. `eval/cloud_synthetic.py` verifies every outgoing evidence string against these synthetic literals. No full-PDF construction or publication.

Execute natively. Use the existing tested `eval/cloud_calls.py` credential-redacting HTTPS client; no local models, downloads or containers. API credential was verified by actual HTTP 200 OK replies from qwen3.8-flash and qwen3.7-plus. Their parameter sizes are not assumed to be 7B/14B. Requested 7B/14B candidates returned 403/404 and are not eligible for accuracy scoring.

Freeze code, this protocol, all synthetic texts/questions, cached synthetic embedding hashes, retrieval IDs and exact outgoing requests before inference. Copy only synthetic chunk/query vectors locally from the prior cache, verify text identity before reuse, and build the hint graph using synthetic texts only. Vector V3 is top 3 / 1,800 characters; V6 is top 6 / 2,400; U retains V6 and appends independent graph recall, at most 12 / 2,400. No model filter. Identical within-model requests may be reused, with explicit provenance. These are previously observed, correlated diagnostic chains, not independent acceptance.

Models share temperature 0, seed 19, non-thinking, JSON object format, source-only English answers, 192 output tokens and at most 3 citations. At most 72 formal requests, one in flight, 45 s timeout, 600 s total / 13,824 output tokens. Stop on auth/access/model errors, two consecutive timeouts or five total failures; preserve failures before stop. Do not retry or tune after answers.

- [ ] Add a failing test that incomplete runs keep all planned denominators; duplicate answers fail validation.
- [ ] Implement the minimal synthetic-only exporter/runner and pass focused tests.
- [ ] Freeze the reviewable request artifact and verify all text is from the fictional passage/query allowlist before asking the execution tool to run the cloud stage.
- [ ] Run, audit each response against the synthetic truth, separate answer accuracy (12 questions) from necessary-source citation completeness (8 answerable), retain raw usage/latency/errors.
- [ ] Verify secrets absent from repository/report/artifacts, frozen hashes, production unchanged and local model ports closed. Report unsupported 7B/14B availability separately from cloud-model accuracy. No independent acceptance or KG extraction qualification.

Files: `eval/cloud_synthetic.py`, `tests/unit/test_cloud_synthetic.py`, `docs/cloud-model-comparison.md`. Private artifacts: `.cache/cloud-synthetic-models`. Keep prior rejected and interrupted attempts intact.
