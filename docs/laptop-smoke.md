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

## Human projection repair

The owner Operations page can submit an accepted human revision ID against the
shown publication revision. The HTTP equivalent is
`POST /api/v1/kgs/{kg_id}/repairs/curations` with `kg_id`, `curation_id`,
`expected_published_revision` and `idempotency_key`. A changed publication is a
conflict. Cancellation/retry use the usual authorized job controls.

Process one approved curation repair natively with:

```sh
uv run --no-sync python -m domain_kg.worker --maintenance-only --once
```

This mode does not load the private provider file. It uses the accepted sealed
curation document and existing vectors, and never claims source ingestion,
material repair, backup, export or KG restore jobs. It inserts isolated graph,
vector and lexical projections, checks them, then switches one publication. It
preserves the original human actor, reason, scope and target revision counters.
The API process and this native worker remain outside the Docker database budget;
no native model is started by this command.

## Expired package cleanup

The Operations page requires an explicit choice of expired exports, backups or
both. The server freezes the policy and target list at owner admission. Configure
`DOMAIN_KG_RETENTION_CONFIG=config/retention.example.json` for the checked-in
24-hour grace, ten-target and bounded object-listing settings. This laptop
example also limits each package to 1,000 objects and 16 MiB of declared bytes;
these limits are checked before reading all retained original bytes. Oversized
packages fail closed and remain claimed for investigation. Server defaults
cover the capture bounds (100,000 objects and 2 GiB). The file is read
with a 64 KiB cap; unknown fields and unsafe bounds are rejected. Changes apply
to new jobs after restart. Existing plans retain their admitted policy.

```bash
# One owner-approved expired-package job, serially; no provider file or model.
uv run --no-sync python -m domain_kg.worker --retention-only --once
```

`POST /api/v1/kgs/{kg_id}/retention` accepts only `kg_id`,
`expected_published_revision`, `resources` (`backups`, `exports`, `both`) and
`idempotency_key`. The ordinary worker and backup/export worker skip these jobs.
A cancelled/failed cleanup keeps its exact claim: retry the original job to
resume. No automatic timer or broad bucket cleanup is enabled. Storage-profile or
configuration changes fence execution; clearing copies in an older placement
requires a separate retained-storage permit. This command does not clear failed
restore targets or refund live original-storage quota.

## Fresh KG restore controls

The sealed backup page accepts a new KG name, approved storage/provider profile
IDs and the displayed source publication revision. The allocated target stays
outside the catalog. The job page shows its new namespace and provider approval
status, without connections, credentials or copied grants. An operator must
approve that exact namespace and its restricted-material policy in the private
provider configuration; the UI does not grant approval. Restart the API/worker
with the same approved configuration. Only then run:

```bash
uv run --no-sync python -m domain_kg.worker --kg-restore-only --once
```

The equivalent owner endpoints are `POST /api/v1/kgs/{kg_id}/restores` (typed
`RestoreCommand`) and `GET /api/v1/kgs/{kg_id}/restores/jobs/{job_id}` (safe target
progress). A failed restore remains inactive; cancellation/retry use the usual
job controls. Expired package cleanup does not erase these targets or return
original-storage holds.

## Discard a failed inactive restore

The original restore job page offers **Discard inactive target** only for a
failed/cancelled tracked target with no pending or uncertain writes. This explicit
owner action permanently fences the original restore from retry. The cleanup
job can be retried independently. It deletes only recorded target objects
(including versions/uploads), scoped target projections and allowlisted target
ledger rows; it preserves shared infrastructure and the live source KG.

`DOMAIN_KG_RESTORE_CLEANUP_CONFIG=config/restore-cleanup.example.json` provides
server limits: 10,000 write records, 1,000 copied objects, 10,000 graph nodes,
10,000 vector points and 10,000 rows per collection, batches of 100. The policy
file is capped at 64 KiB. Listing uses 100 records/page, 20 pages and at most
1,000 records. Limits are frozen for each admitted job. Exceeding a limit or
finding unregistered bytes stops completion and retains quota for investigation.

```bash
uv run --no-sync python -m domain_kg.worker --restore-cleanup-only --once
```

The typed endpoint is `POST /api/v1/kgs/{kg_id}/restores/jobs/{job_id}/cleanup`
with `kg_id`, `restore_job_id`, `expected_published_revision`, `idempotency_key`.
The source and path IDs must agree. No provider file or model is loaded by this
worker mode. Quota release, completion and audit share one Mongo transaction
only after physical absence checks. A temporary commit failure uses the existing
bounded automatic retry; explicit cancelled/failed jobs use the normal retry UI.

Quiesce old restore workers before upgrading. Targets allocated before supported
write tracking, and targets with pending/uncertain writes, are deliberately not
auto-cleaned; lease expiry alone is not evidence that remote writes ended.


The default browser preview uses `tests.preview_restore_demo: false` in
`config/laptop-smoke.json`. It prepares genuine cancelled, inactive restore jobs
for inspection/cleanup and keeps the native-source/manual-edit, history and index
rebuild controls available. It skips the already-verified full restore setup on
each ordinary preview. Set the boolean to `true` only when deliberately reviewing
the complete restored-KG example; both modes use synthetic providers, one serial
worker, the same database caps and the bounded preview lifetime. A cancelled
target is never labelled as an activated restored KG. Numeric/string values for
this option are rejected. This option does not start or download any local model.
