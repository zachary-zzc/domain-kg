# Advanced model settings implementation plan

> Implement natively in this chat. Repository authorization, current user intent and preservation of existing work override skill handoff/delegation defaults.

**Goal:** The local owner can select Alibaba, OpenAI, native Claude, Ollama, LM Studio or a compatible API; independently select embeddings; test connections before saving; see clear authentication, billing and quota failures; and retain the supplied Alibaba configuration as the default.

**Architecture:** Extend existing private provider profiles, typed setup commands, current-session policy and durable call budgets. Use the existing extraction/embedding clients for real calls, with a small native Claude wire adapter. Probe results are scoped to organization/KG and a credential-sensitive configuration fingerprint. Keys remain in the owner-only provider file and are never rendered.

**Constraints:** Current user remains an OIDC owner/operator. Existing approvals, budgets, publication and embedding-space checks remain enforced. No arbitrary server paths, credential-bearing URLs, automated recharge, corpus ingestion, model installation, or runtime start. Existing configurations retain wire/cache defaults.

**Sources:** `docs/design.md`, `docs/contracts.md`, `docs/revisions-and-publication.md`, `docs/providers.md`, implementation tasks 9 and 17. Current official OpenAI chat/embedding and Anthropic Messages/error references were fetched to `.cache/provider-docs/`.

### Task 1: Provider protocols and actionable failures

- [x] Add failing client regressions for native Claude, OpenAI embedding dimensions and safe authentication/billing/rate-limit errors.
- [x] Extend the responsible connection/profile fields with omitted historical defaults. Reuse bounded HTTP and source validation; no tools are exposed to models.
- [x] Run focused provider, budget and extraction tests.

### Task 2: Checked setup, discovery and default selection

- [x] Add failing tests for secret-preserving edits, same-organization embedding reuse, Hello checks, scoped health records, permissions, stale configuration, unchanged budgets and default retention.
- [x] Extend private configuration preparation/save and add checked-save plus explicit check/model-discovery actions. Test both chat and embedding; validate the actual vector dimension and JSON response.
- [x] Make the supplied approved Alibaba profile the explicit default without changing other accounts' permissions or existing selections.
- [x] Run real-store setup/authorization/budget regressions.

### Task 3: User-friendly advanced settings and local acceptance

- [x] Add provider presets, separate embedding selection, model list discovery, explicit key-preservation hints and test/save progress under Advanced settings.
- [x] Show latest connection results, account failures and remaining local budgets with clear recovery actions in Chinese and English.
- [x] Exercise the current local user's Alibaba configuration with only a small Hello and embedding test, keeping budgets unchanged.
- [x] Run focused tests, Ruff, mypy and browser checks; document actual limits and results.

Review focus: wrong key; provider debt versus local budget; Claude with an unrelated embedding endpoint; local service offline; key edited versus endpoint edited; stale forms; credential rotation; published index compatibility; cross-organization reuse; repeated test/save after a lost response.


## Verification and acceptance

Completed on 2026-10-29 under the existing local owner/operator account.
The supplied Alibaba profile is the explicit default. Chrome verified two small
Hello/embedding checks (four API calls total), a valid 1024-dimensional vector,
184 discovered models, preset changes, independently reused embeddings, expanded
parameters and Chinese/English labels. The latest page showed 4/12 daily calls,
66 input tokens and 10 output tokens, with no budget change or source submission.
Local service discovery showed an actionable startup message; Ollama is installed
but no local runtime was started. OpenAI/Claude use deterministic protocol tests;
no corresponding live keys were supplied.

Final verification:

- `pytest tests/unit tests/protocol -q`: 669 passed. macOS `sandbox-exec` tests
  require native execution outside the tool's outer sandbox, while retaining the
  parser's own restrictions; the native full run passed.
- Four affected real-store provider/settings/setup files: 6 passed.
- Provider reservation, embedding-space, extraction-checkpoint and transport
  authorization boundary files: 12 passed.
- `node --test tests/browser/*.test.mjs`: 14 passed. The production settings module
  regression first failed for the malformed reused embedding UUID, stale provider
  model list, unsupported native thinking override and editable inherited context,
  then passed after the responsible fixes.
- Ruff clean, strict mypy clean across 250 source files, `git diff --check` clean.

Test-only responses use the existing owner-scoped usage service so counters update
without discarding a draft. Claude cache creation/read tokens count toward input
usage. Native Claude thinking overrides are disabled and rejected rather than
silently ignored. Provider keys remain private and defaults never grant access.

Acceptance screenshot: `.cache/local-browser/advanced-settings.png`.
Sources and operation details are recorded in `docs/providers.md` and
`docs/local-browser-workspace.md`. Services remain running; no commit, deployment,
corpus migration, model installation or budget increase was performed.
