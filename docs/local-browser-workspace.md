# Local browser workspace

This runs the actual Domain KG application and background workers on this Mac.
A separate Keycloak realm provides OIDC login. MongoDB, Neo4j, Qdrant and object
storage retain materials and accepted projections across application restarts.
Alibaba `qwen3.6-35b-a3b` performs source-grounded extraction and
`text-embedding-v4` supplies 1,024-dimensional embeddings. No local model is needed.

## Start and sign in

From the repository root, with the pinned `.venv` and the private Alibaba key
already configured:

```bash
.venv/bin/python -m scripts.local_workspace setup
.venv/bin/python -m scripts.local_workspace start
```

Setup registers one isolated organization, KG and approved provider. Repeating
setup uses the same identities and idempotent commands. It submits no source
material. `start` runs the API, source worker and backup/export worker together.
Open [the browser console](http://127.0.0.1:8000/admin). Choose **Sign in with your
organization** and use the username/password in `secrets/local-browser/login.txt`.
The login uses authorization code, PKCE, nonce validation and the normal session
and permission checks. There is no application authentication bypass.

Private files live in the ignored, owner-only `secrets/local-browser/` directory.
Application logs and browser acceptance artifacts live in `.cache/local-browser/`.
The launcher overrides the authority database to `domain_kg_browser`, leaving the
ordinary `.env` unchanged. `.env.browser` documents these overrides; use the
launcher so the API and all workers receive the same settings.

This is a workstation development deployment. Its addresses bind to loopback;
Keycloak runs in development mode with its own persistent volume. The four
existing databases retain their 1,408 MiB limit. The optional identity container
adds 512 MiB: a combined 1,920 MiB (2.013 GB) Docker limit, with swap disabled.
Python processes and the browser use additional host memory. Shared or public
hosting needs HTTPS, production IdP configuration and the deployment release gates.

## Create a KG and configure API or local models

The local `owner` account is both an organization administrator and a platform
operator. Open **Create KG**, enter a name, choose a domain schema and storage
profile, and optionally select the existing Alibaba model configuration. Selecting
it explicitly approves the new KG under that configuration's current data policy
and budget. Creation makes you the KG owner and opens **KG settings**.

For an existing unconfigured KG, open **KG settings → Use an existing model
configuration**, select Alibaba, check the approval box and save. No model call
is made by creation or configuration.

To use another API or a local model, open **Add model configuration**:

1. Enter a configuration name, the OpenAI-compatible API base URL, key, extraction
   model name and embedding model name. Alibaba's base URL is
   `https://dashscope.aliyuncs.com/compatible-mode/v1`; the existing configuration
   already contains its key, so reuse it when possible.
2. For a local server, first start it and load its models. Typical URLs are
   `http://127.0.0.1:11434/v1` for Ollama and `http://127.0.0.1:1234/v1` for
   LM Studio. These addresses are relative to the API/worker host. Leave the key
   blank if the server requires none. The form does not install or launch models.
3. Expand **Separate embedding API** if embeddings run elsewhere. That endpoint
   uses its own key; a blank key sends no Authorization header. This deployment
   requires **1,024-dimensional embeddings**. Match the actual model context
   limits under **Model compatibility**. Alibaba Qwen uses **JSON object** with
   **Thinking mode → Disabled** for the tested extraction configuration.
4. Approve sending the KG's source text to the endpoints and save. Restricted
   materials require the separate explicit checkbox. Import a tiny public file
   first, then inspect its job, graph and RAG evidence to verify model compatibility.

Configuration is available only to accounts that are both KG owners and platform
operators. Other KG owners can select profiles already approved for that KG.
Keys are saved in the existing owner-only provider file and never returned in
HTML or audit records. API and worker processes reload changes automatically;
repeating local setup retains browser-added profiles. Saving does not test network
connectivity or extraction quality. Once evidence is published, changing embedding
space requires a new generation; use a new KG to test a different embedding model.

The model-setup change was verified on 2026-10-29 with 37 unit checks and 11
real-store integration checks, including retries, concurrent private-file writes,
cross-organization denial, operator permissions, embedding-space restrictions,
and usage budgets. Ruff and strict mypy passed. A live HTTP acceptance used real
Keycloak login to configure the existing “测试” KG, checked both languages and
logout, and preserved credentials and budgets without making model calls. Its
report is `.cache/local-browser/model-settings-acceptance.json`. Browser visual
automation was unavailable for this change; local-model protocol behavior used
mocked endpoints, and no local model runtime was started.

## Import or update a folder

1. Open **Local source knowledge**, then **Import folder**.
2. Choose a directory and click **Review files**. Review the relative filenames,
   sizes and Add/Replace/Unchanged decisions.
3. Click **Upload and construct KG**. Upload progress and background processing
   appear separately. Each file is submitted as its own durable job; parsing one
   file does not hold subsequent uploads. Bad files show their own reason and
   recovery action. **Pause uploads** stops after the current submission; accepted
   jobs continue. Keep this tab open only while files still need uploading.
4. Open **Graph** to pan, zoom and inspect nodes and source-supported relations.
   Open **RAG evidence** for fast or deep retrieval, then **Inspect citation** to
   see the source. Retrieval returns cited evidence; it does not generate an answer.

Supported folder files are TXT, Markdown, PDF, DOCX, HTML, CSV and JSON, each
nonempty and at most 50 MiB. Hidden files and unsupported formats are skipped.
Start with `.cache/local-browser/browser-example/` after the acceptance run, or a
small folder of public text. The prepared 200 MB corpus is at
`.cache/alibaba-35b/corpus/prepared/documents/`; it has not been ingested in full.

Folder matching uses the material source label `folder:<relative path>`, including
the selected root directory's name. Reselect the same root to skip identical
checksums and replace changed files under their existing material IDs. Changes
retain the old accepted version until publication. Renames become new materials;
missing files are never removed automatically. This is an explicit browser upload,
not a background filesystem watcher. Use Materials for removal/restoration and
individual replacements. Ambiguous matching paths fail rather than choosing a
material. Do not run simultaneous imports of the same new folder as different
users. Review reconnects an identical file to its existing incomplete job rather
than creating another material. A changed file with an incomplete job must be
resolved first; unrelated pending or failed materials do not block new files.

**Recent import jobs** and **Activity & jobs** show source names, newest first,
with processing stages, percentages, failures and links to explicit retry controls.
They survive leaving the page and service restarts. Browser file bytes and paths
are not saved in local storage: reselect the same folder to resume files that were
never submitted. Empty, hidden and unsupported files remain visible as skipped
rows, while oversized or unreadable files have individual errors.

The existing provider policy allows public KG-visible sources only. The local
organization and KG daily call/token ceilings have been explicitly disabled;
usage counters and reservations remain active. Provider billing, credit and rate
limits still apply. A submission-time quota error pauses further
uploads; already accepted jobs retain their own results. Worker failures explain
known parser limits, storage quotas, model context limits or provider budgets
separately. Older jobs retained only an error code and cannot recover a precise
historical cause. Full-corpus ingestion requires a separately chosen cost
budget and capacity work: unfiltered retrieval currently caps eligible bindings
at 1,000 and sealed rows at 20,000. Preparing 200 MB does not prove that entire
corpus can be queried at once. The launcher never raises these limits silently.

## Exercise the real MCP endpoint in the browser

1. Generate a short-lived token:

   ```bash
   .venv/bin/python -m scripts.local_workspace token
   ```

2. Open the KG's **Test MCP** page. Choose the reported
   `secrets/local-browser/mcp-token.txt` file, then **Connect and list tools**.
3. Choose `domain_kg_search`, review the prefilled arguments, enter a question
   about the imported text, and click **Run tool**. The page displays the actual
   MCP structured result, including source citations and graph paths.
4. To test ingestion through MCP, choose `domain_kg_add_material` and use its
   displayed input schema. The source worker processes the returned job. Follow
   it with `domain_kg_get_job` before searching the accepted material.

The browser sends `initialize`, `notifications/initialized`, `tools/list` and
`tools/call` directly to [the MCP endpoint](http://127.0.0.1:8000/mcp), with protocol
negotiation and bearer authentication. Tokens are held only in tab memory and
cleared on navigation or Disconnect. No token is put in a URL or browser storage.
The local MCP service identity has read/write scopes and editor access only to
this KG; it cannot administer the organization or configure the provider. Its
token expires after five minutes. The browser session alone cannot authorize MCP.

Other Streamable HTTP clients can use that same endpoint and token. This panel
proves the live MCP path; it does not establish acceptance for every remote client.

## Stop and verify

Ctrl+C in the launch terminal stops its API and workers. To stop the local
containers too, without deleting stored data:

```bash
.venv/bin/python -m scripts.local_workspace stop-stores
```

Focused checks:

```bash
.venv/bin/pytest tests/unit/test_local_identity.py tests/unit/test_operation_worker.py -q
node --test tests/browser/folder-plan.test.mjs
.venv/bin/pytest tests/integration/test_browser_tools.py \
  tests/integration/test_console_material_commands.py \
  tests/integration/test_console_overview.py \
  tests/integration/test_transport_authorization.py -q
```

The original real-browser acceptance script is `tests/evaluation/local_workspace_browser.mjs`.
It is opt-in via `DOMAIN_KG_BROWSER_LIVE=1`, requires an already installed Playwright
package and Chrome, and makes a few paid calls using one tiny public synthetic
file. It exercises OIDC login, folder ingestion, unchanged detection, replacement,
graph rendering, deep retrieval/citations, real MCP calls and logout denial.

## Verification on this workstation

On 2026-10-29 the real Chrome acceptance passed all nine checks, including real
Keycloak login, folder upload, unchanged detection, replacement publication,
interactive graph, cited deep retrieval, browser MCP discovery/tool execution,
unauthenticated MCP denial and logout denial. The final source contains four
entities and three relationships. One material has two accepted/retained versions;
repeating setup kept one organization and one KG.

The combined live verification used six Alibaba calls: two extraction calls and
four embedding calls, reporting 2,269 input tokens and 576 output tokens. No
200 MB corpus ingestion was started. The report is
`.cache/local-browser/browser-acceptance.json`; screenshots are `graph.png`,
`rag.png` and `mcp.png` in the same directory, with `usage.json` recording usage.

The five real-store browser/upload/graph/transport checks and all sixteen identity
checks passed. The final focused Python unit/protocol run passed thirteen checks;
four browser file-planning checks passed. Repository Ruff, strict mypy across
242 source files and `git diff --check` passed. The graph-navigation adjustment
also passed the two affected real-store checks. Local loopback Qdrant checks emit
the existing HTTP API-key warning; shared deployment still requires TLS.

A subsequent API/worker restart retained the four-node, three-relation graph.
The browser rechecked folder checksums, workflow navigation and the expanded MCP
argument editor without additional model calls. The services were left running.

## Rebuilt console navigation

Open `/admin` for the knowledge graph catalog and **Create KG**. Creation asks for
an identifying name, a domain schema, and an organization-approved storage profile,
then opens **KG settings**. Operators can explicitly approve an existing model
during creation or configure an API/local model in settings as described above.
The existing local source KG retains its current model selection and usage limits.

The sidebar separates:

- **Workspace:** sources/folder import, graph explorer, RAG search, activity,
  MCP playground, access roles, settings, and maintenance for the selected KG.
- **Organization:** people, groups, and service identities.
- **Storage profiles** and **Database connections:** logical placement bundles
  versus physical services. Creating a KG uses existing services; it does not
  provision new database servers.

Groups contain people; **Access & roles** assigns a group its role in each KG.
Group management uses member names and keeps identifiers available in details.
The mobile navigation collapses behind a keyboard-accessible button. English and
Chinese interface labels remain available in the header.

For a browser verification using existing data and no model calls, refresh the
local token and run the UI acceptance script with your installed Playwright:

```bash
.venv/bin/python -m scripts.local_workspace token
DOMAIN_KG_BROWSER_UI=1 NODE_PATH=/path/to/node_modules node tests/evaluation/console_redesign_browser.mjs
```

It checks real OIDC login, the create form, workspace/team/storage pages,
graph rendering, lexical retrieval and citations, a real MCP material-list call,
390-pixel mobile layouts, Chinese labels, and logout denial. Screenshots and its
report are saved under `.cache/local-browser/redesign/`. The separate live
extraction test remains opt-in and incurs model calls.

## Advanced models and API checks (2026-10-29)

Use **图谱设置 → 高级设置** in the existing owner account. The supplied Alibaba
profile is now the explicit default for new authorized KGs:
`qwen3.6-35b-a3b` chat and `text-embedding-v4` embeddings at 1024 dimensions.
Existing KGs retain their own selections and the daily limit remains 12 calls.
The saved key stays in `secrets/local-browser/providers.json` with owner-only
permissions; it is not rendered or stored in the browser.

Chrome acceptance confirmed that Alibaba model discovery loads 184 account models,
provider presets fill OpenAI/Claude/Ollama URLs and models, chat can retain the saved
Alibaba embedding connection, and advanced parameters expand without submitting.
The discovered list is cleared when changing its provider. An off-by-one prefix
removal in the reused embedding ID was reproduced by executing the production
browser module and fixed; the resulting UUID is validated again by the backend.

Two small real Hello/embedding checks completed in the browser session (four API
calls total). The latest chat check took 750 ms and embedding took 634 ms; both
passed, including the actual 1024-vector check. The page immediately showed
4/12 calls, 66 input tokens and 10 output tokens. No source documents were sent
and no budgets were raised. Model-list requests did not generate text or vectors.
OpenAI and native Claude protocols have deterministic HTTP regression coverage;
no live keys for those providers were supplied or tested.

Ollama is installed, but neither the Ollama (11434) nor LM Studio (1234) service
was listening during acceptance. Local selection is available; the user must
start the runtime and load a suitable model before connecting. A local chat model
may continue to use Alibaba embeddings. No runtime was installed/started and no
corpus was ingested as part of model setup.

## Local cap removal and evidence diagnostics (2026-10-29)

The user explicitly authorized removing local daily API call and input/output
Token caps. Both organization and KG limits in the private provider file are now
null. API/worker restart loaded this policy; the existing “测试” KG's Advanced
settings page showed all three resources as **不限额**, with its existing counters
retained. Provider charges and provider-side limits remain independent.

A read-only inspection found 11 failed imports in that KG: eight with the local
usage-budget error and three with `EVIDENCE_VALIDATION_FAILED`. Job
`ab6e8946-6ea5-4ba5-85cd-418c2e7ce47d` had 14 persisted source chunks; the first
chunk's empty extraction had already completed. The old failure record did not
retain a validation reason or rejected model payload, so the exact failing check
cannot be established from that record alone.

New validation failures distinguish source quotation/location mismatch, missing
entity names, unsupported types/predicates, relation endpoints, negation,
qualifiers and malformed references. Only fixed allowlisted messages enter the
durable job record and translated console; source text and raw provider payloads
remain private. Canonical source validation is unchanged.

Automatic approval review rejected a proposed minimal live reproduction because
it would resend private source text to Alibaba and incur a model charge without
explicit approval for that replay. The separate request for at most three small
reproduction calls remains pending. The failed source has not been resent or
retried, and its extraction defect is not yet claimed fixed. Original task and
source records are preserved.


The user subsequently approved up to three small source replay calls. A live
reproduction showed that the second chunk (133 characters) had relations citing
only one endpoint's passage. All 14 persisted windows fit the configured chat and
embedding contexts. One corrected request passed the unchanged validator with six
entities and three relations. The three diagnostic calls are exhausted; the
original failed jobs have not been batch-retried. The new extraction client can
attempt one evidence correction, with fresh authorization, an actual-prompt budget
check and separate usage settlement. A failed correction remains unpublished.
