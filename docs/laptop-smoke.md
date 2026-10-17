# Laptop smoke configuration

`config/laptop-smoke.json` is the checked-in, credential-free budget profile.
`scripts/smoke.py` validates it before startup and compares both Compose's
configured limits and the running containers with its per-service budgets.
The 1.5 GB ceiling covers Docker databases. Native models have a separate budget.

| Setting | Laptop value | Enforcement |
| --- | --- | --- |
| Docker total memory | 1,500,000,000 bytes maximum | Container hard limits and runner validation |
| MongoDB / Neo4j | 536,870,912 bytes each | Compose `mem_limit` |
| Qdrant / S3 | 201,326,592 bytes each | Compose `mem_limit` |
| Actual combined Docker limits | 1,476,395,008 bytes | Verified before real-store tests |
| Swap / total Docker CPU | Disabled / 2.5 cores | Compose; checked by runner |
| Ordinary test providers | Synthetic responses | No native model startup or remote calls |
| Test concurrency | One process | Serial smoke; real-store fixtures also take a file lock |
| Quick / full task test deadlines | 180 / 1,800 seconds | Runner stops its own timed-out process group |
| Temporary UI preview | 600 seconds | Synthetic pytest fixture cleans itself up |
| Native model RSS threshold | 1,250,000,000 bytes per process | Embedding smoke watchdog samples every 0.25 s |
| Native CPU / slots / GPU | Two threads / one slot / zero GPU layers | Explicit native embedding startup arguments |
| Native embedding requests | Three by default; six with `--bilingual` | Fixed tiny input set and request-count check |
| Chat smoke declaration | Two requests, 512 input and 128 output tokens each | Profile for explicit chat probes; ordinary runner never launches chat |

The RSS watchdog is a sampled process limit, not a kernel memory cgroup. A native
process may briefly exceed its threshold before the next sample. Only the process
started by the embedding smoke is terminated; another running model causes the
sequential smoke to refuse startup. The configured 1.5 GB Docker limit remains
independent. No model is downloaded or started automatically.

Pinned model files, revisions and hashes are in `config/model-runtime.proposed.json`:
Qwen3.5-0.8B Q4_0 for tiny chat checks; Qwen3-Embedding-0.6B Q8_0 for embeddings.
The chat model has not passed source-grounded extraction quality. The smoke must
not use it to mark full extraction acceptance complete. Existing source-window
logic fits each configured model context while preserving original evidence offsets.
Changing an accepted embedding space requires an explicit new generation.

```bash
# Lowest-cost check: budget validation and source-window tests; no Docker or model.
python3 scripts/smoke.py --quick --offline

# Tiny real database availability checks, still zero live model calls.
python3 scripts/smoke.py --quick --keep-services

# Opt-in real embedding smoke: only the already downloaded pinned model.
# Requires the other native model to be stopped by its owner first.
uv run --no-sync python scripts/smoke_native_embedding.py

# Full six-request bilingual comparison, only when needed.
uv run --no-sync python scripts/smoke_native_embedding.py --bilingual
```

Run changed behavior tests first, then one relevant task gate after the feature is
ready. Repeat a broad suite only for a new failure or a material change. The quick
profile checks resource and model-window boundaries; it is not evidence that
backup/restore, permissions or a complete task passed. Do not omit real-store
permission, publication or recovery checks from their task gates.

Private provider credentials remain in the owner-only local configuration outside
Git. Examples do not enable corpus access: namespace approval and data-policy
approval must be explicit. This profile does not grant either permission.

Provider request budgets are a separate `budgets` section in the approved provider
configuration. Both examples keep corpus access disabled. The laptop example caps
one KG at 128,000 input tokens, 8,192 output tokens and ten calls per UTC day; its
organization ceiling is 512,000 input tokens, 32,768 output tokens and 40 calls.
Omitting this section uses the documented webserver example limits. Use the same
approved configuration for API and worker processes; restart them after changes.
These limits do not download/start a model or change the Docker/native RAM caps.

The service reserves both organization and KG capacity in Mongo before provider
HTTP. Each embedding batch reserves at most its configured context times its
actual batch size; extraction reserves its configured context/output bounds.
Successful validated usage returns unused capacity. A denied request that has not
started returns its entire hold. A timeout, malformed response or other uncertain
attempt retains the full bound; an interrupted process leaves a conservative
pending hold. A new UTC day uses a new bucket. These are request/usage limits,
not a currency billing estimate. A request needs room for its full bound even
when the requested text is short. No prompt, source text, endpoint or credential
is stored in the budget ledger.
