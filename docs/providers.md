# Provider profiles

For the Alibaba 35B connection, owner-only profile selection through MCP/HTTP,
and the prepared 200 MB source corpus, see [the first-test guide](alibaba-first-test.md).

`extraction_format` defaults to `full`. The laptop example opts into
`source_refs_v1`: exact source spans are sent once as numbered passages, model
entities/assertions cite `refs`, and the server supplies immutable locations,
offsets and temporary entity IDs. Names remain literal source text, with no
automatic case repair or invented aliases. Polarity and source-bound qualifiers
are retained. The same canonical validator rejects bad references, endpoints,
names, types and predicates; a valid JSON response alone is not sufficient.
Changing the format requires a new approved profile revision. Checkpoint hashes
include the serialized profile and the model-window version.

Numbered formats expose canonical source spans unchanged (`source-spans-v3-open-qualifiers`).
Entity meaning, relationship meaning and polarity are model judgments. The server
does not use negation-keyword regexes to reject claims, or regex sentence splitting
to modify evidence numbering. An unrelated `not`, or a phrase such as `not only`,
cannot invalidate a positive relation by itself. Typed polarity and qualifiers are
preserved; source offsets, endpoints, namespace, schema and authorization are still
validated. Identifier-format syntax checks remain separate from semantic extraction.
This removes a heuristic semantic gate, not the possibility of a model getting
polarity wrong: source correspondence does not prove entailment. The evidence
version and extraction pipeline revision invalidate incompatible checkpoints for
new builds. Accepted graphs and frozen evaluation snapshots are not rewritten.

Model-window-v6-unicode packs consecutive source spans under extraction and embedding
budgets, retaining a short preceding span where possible. Oversized individual
spans still split with exact Unicode offsets. The conservative context preflight
remains active; neither a context fit nor processed-page coverage establishes
semantic or clinical completeness.

New browser-created profiles and the Alibaba example use `lightrag_general_v2`.
This follows LightRAG 1.5.7's initial extraction plus one gleaning pass through the
approved provider adapter. The same working model reviews the original source for
missing facts, with a compact inventory of prior claims to avoid duplicates.
Both responses require their own endpoint records and valid source references.
The server unions validated facts, remaps local IDs and deduplicates exact claims.
Version 2 adds source-supported decomposition of compound descriptions and explicit alternate-name relations, without domain-specific types, regex entity rules or inferred links. Version 1 retains its original prompt and checkpoint identity. Existing explicitly configured policies are not silently migrated.

Each source window permits at most two model calls: an initial response followed
by either gleaning or correction. Invalid model JSON/schema receives one model
correction, rather than local regex repair; source-evidence correction uses the
same second-call allowance. Transport failures, refusal and authorization/billing
errors do not trigger an initial-response retry. Every transmitted call is separately
authorized, budgeted and metered. Gleaning that cannot fit the actual context is
skipped with `extraction_gleaning_context_limit`; an optional gleaning failure keeps
the validated initial facts with `extraction_gleaning_incomplete`. Authority,
billing and quota errors still propagate. Output-limit subdivision remains bounded
to two levels; the two-call limit applies to each attempted source window.

Entity, predicate and qualifier labels come from source meaning under open schemas.
Qualifier names are bounded strings, not a three-key enum; their values and evidence
remain source-validated. An omitted optional qualifier list means an empty list.
Closed custom entity/relation schemas retain their restrictions. The general policy
does not append the historical domain/role dictionary or require a fixed set of facets.
Per-response record limits remain unchanged; the validated union permits up to
two full responses. No model output can choose authority or database operations.
See [implementation and evaluation](general-lightrag-optimization-2026-11-02.md).

Set `DOMAIN_KG_PROVIDER_CONFIG` to an owner-readable-only JSON file outside Git.
The file has `connections` and `profiles` arrays validated by
`providers/profiles.py`. Connections contain a private `ref`, HTTPS `endpoint`
(the API prefix before `/chat/completions`), and `api_key`. HTTP is allowed only
on development loopback. Ambient proxy settings and redirects are disabled.

The browser's **KG settings** also supports adding OpenAI-compatible API/local
connections and approving an existing organization configuration for another KG.
These CSRF-protected commands require both current KG configuration permission
and platform-operator authority. They edit only the server-configured owner-only
file, using a process lock and atomic replacement; secrets never enter MongoDB,
HTML responses or audits. Blank keys omit the Authorization header for keyless
local servers. API and worker registries reload file changes before resolving a
profile. MCP and public JSON endpoints still accept approved profile IDs only.
File approval and MongoDB selection are separate operations: if selection fails,
the previous selection remains active and the new profile may remain unused in
the file. No transaction across those stores is claimed. See the
[browser guide](local-browser-workspace.md#create-a-kg-and-configure-api-or-local-models).

Each profile specifies an ID, revision, connection reference, explicit extraction
and embedding model names, embedding dimensions, approved organization/KG
namespaces, and an affirmative data-policy approval. Restricted materials require
the additional `allow_restricted` approval. No profile or model is selected by
default. A KG owner selects an approved profile through the shared configuration
service. Embedding dimensions must match its provisioned generation; changing an
accepted embedding model requires a new generation.

`embedding_connection_ref` optionally selects a separate approved connection;
otherwise embeddings use `connection_ref`. This lets the small local chat model
and an embedding API use different endpoints and credentials. `embedding_revision`
(default `"1"`) identifies an operator-pinned weight revision. A vector-space
fingerprint covers the embedding endpoint, model, dimensions, weight revision,
input window, query prefix and window-pooling version.
Credential rotation and chat-only profile changes preserve it; a changed space
requires a new generation once evidence is published. Both publication and query
embeddings verify the selected fingerprint. Legacy artifacts without one remain
readable for graph inspection and require migration before vector retrieval.

For a constrained grammar server, `schema_string_limit` may tighten existing
transport string maxima to 64–1,000 characters. The approved local profile uses
512 because its grammar compiler rejects an 8,000-repeat description rule.
This changes only the request's generation schema; canonical evidence/storage
limits and source-grounding validation remain intact. Provider/validation errors
are handled before entering LightRAG's callback so upstream exception-prefixing
cannot erase the service's safe domain error code.

The approved small-model settings and measured smoke are documented in
[the model setup](model-proposal.md). The tracked
[webserver example](../config/providers.webserver.example.json) is deliberately
disabled until its private copy has approved namespaces and real model settings.

Extraction defaults to strict JSON Schema Chat Completions output with `store:false`, no
tools, bounded completion tokens, a wall-clock deadline, a 2 MiB response limit,
and current access checks before transmission and after completion. Source text
is supplied as untrusted JSON data. Refusals, truncation, tool requests, unknown
fields fail closed; unsupported evidence is never accepted. Usage counters contain namespace,
profile and token counts; source bodies and raw provider errors are not logged.

For an approved model that supports JSON Object but not strict JSON Schema output,
set `extraction_response_format: "json_object"`. The server includes the same
server-defined schema in the system prompt and still parses the output with the
same strict types and canonical source validators. This is an explicit profile
choice, never an automatic retry after a failed request. The schema in the prompt
counts toward the context budget, including when `schema_in_context` is false.
`extraction_enable_thinking: false` explicitly selects a non-thinking mode on
providers that support that option; omission preserves the provider's default.
The transport still sends `max_completion_tokens`. Test compatibility with the
chosen provider/model before changing an approved profile revision. Historical
defaults keep the previous extraction checkpoint identity; either non-default
option rotates extraction checkpoints without changing the embedding space.
Provider support is model-dependent; see
[Alibaba structured output](https://help.aliyun.com/zh/model-studio/qwen-structured-output)
and [Chat Completions parameters](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions).

The server validates exact block locations, character offsets and quotations;
names, aliases, identifiers and descriptions remain extractive. A verified
support establishes correspondence to the source, not truth. Conflicting and
negative claims keep distinct assertion identities. Grounded explicit
identifiers can unite entities across materials; equal names alone cannot.
Ambiguous homonyms remain separate until an authorized curation merges them.

Built-in general, medical and scientific schemas now use revision 2 with an open
vocabulary: the model derives types and predicates from the supplied content.
Open requests omit preset label lists to avoid anchoring the model to them.
Labels remain bounded
property values; they never become database commands or permissions. Existing
stored custom schemas without `vocabulary` remain closed and retain their endpoint
restrictions. Extraction, curation and imports apply the same schema policy.

Open-profile prompts extract supported relations before their endpoint entities,
including facts expressed by lists and tables. After evidence validation and any
corrective response, automatic extraction keeps only entities used by accepted
relations. Standalone mentions remain in the original and searchable source chunks.
It never adds an edge just to connect a node. Explicit imported records and manual
curation are preserved. Extraction checkpoint identities change with this algorithm;
existing accepted graphs change only through normal re-ingestion and verified
publication, never by an in-place cleanup of accepted projections.

Responses exceeding the format's entity/assertion count limits use the same
bounded source-splitting path as a token-truncated response. Each smaller source
window must still pass all validation; arrays are never silently truncated. Other
malformed fields remain provider failures.

An approved profile may opt into `extraction_semantics: "source_roles_v1"`
(default `"legacy"`). This adds shared source-role guidance to all three extraction
formats: check active/passive direction, creator versus performer/owner, event-date
meaning, and necessary temporal/conditional qualifiers. It describes only predicates
already allowed by the selected schema; it does not extend or migrate that schema.
When changing a deployed profile, increment its revision through the existing
configuration workflow. The policy participates in extraction checkpoint identity;
legacy full-format profiles retain their previous identity and embedding spaces do not change.
Accepted projections and manual curation remain unchanged until explicit normal
re-ingestion/publication. This is prompt guidance, not a semantic acceptance gate or
a claim of measured improvement. The source-only comparison and current status are
recorded in [the source-role experiment](source-role-quality-report.md).
The extra instructions count toward the existing conservative context budget. A
4K full-format profile can reject even minimal input with this policy; select an
actually supported larger model window or retain the legacy policy. Do not raise
the configured limit above the provider's real limit or bypass the budget check.

Two experimental open-schema prompt policies are available for measured comparison:
`source_facets_v1` asks for source-stated properties, measurements, methods, requirements,
outcomes and limitations as well as named-entity relations; `source_facets_v2` additionally
requires every relation endpoint, including literal values, to have an entity record.
They encourage reusable labels, preserve event roles and qualifiers, and forbid invented
links. These reminders do not define a fixed vocabulary or a minimum edge count.
Closed schemas retain the existing vocabulary and source-role instructions.

Both policies are opt-in through `ProviderProfile.extraction_semantics`; the browser
configuration default is now `lightrag_general_v2`. The policy is part of checkpoint
identity. Existing sources, validators, output limits, correction/splitting limits,
permissions and publication behavior remain in force. The policies do not repair
fragmented OCR or establish semantic accuracy. See
[the prompt experiment](graph-prompt-optimization-2026-11-02.md) before adoption.

LightRAG 1.5.7 processes server-generated opaque engine names under the source
namespace. Its output must preserve every expected entity, endpoint and source
mapping. Canonical IDs and database operations are constructed by the server.
Native entity types also use opaque identifiers because LightRAG rejects type
punctuation such as `Test/Service`; canonical content-derived labels stay intact.
Display observations, spans and supports remain source-owned in MongoDB.
Preparation never makes records searchable by itself.

Run `python3 scripts/smoke.py --task 9 --keep-services` for the laptop check.
It uses tiny public synthetic source fixtures, real local stores, and deterministic
HTTP provider responses. It verifies mapping fidelity and rejected fabricated
claims without a model download or paid request. An actual approved-provider
quality/usage smoke remains an external acceptance gate until configured; this
local check does not establish a provider model's extraction quality.

On 2026-10-21, a separate budgeted cloud smoke exercised the actual extraction
client, canonical source validator and LightRAG mapping with a tiny public fixture.
Both `qwen3.6-35b-a3b` and `qwen3.5-397b-a17b` passed with `source_refs_v1` and
`source_names_v1`, explicit `json_object`, and `extraction_enable_thinking: false`.
Their tested strict JSON Schema requests returned HTTP 400. Full-format JSON
Object responses failed response/source validation and are not established as
usable by this smoke. These results are scoped to the tested fixture and settings;
general semantic-quality acceptance remains open. See the detailed
[source-role report](source-role-quality-report.md) for outcomes and costs.

## Browser advanced settings

Owners who are also platform operators can open **KG settings → Advanced settings**
to configure model endpoints without editing a JSON file. Presets cover Alibaba
DashScope, OpenAI, native Anthropic Messages, Ollama, LM Studio and another
OpenAI-compatible endpoint. Model discovery uses the selected account's `/models`
endpoint; names can also be entered manually. Local runtimes must already be
running with a model loaded. This console does not download models or start them.

Chat and embeddings can use different connections. An approved embedding profile
from the same organization can be reused without revealing its key. Native Claude
requires a separate embedding connection; its thinking override is disabled because
this adapter consumes text-only structured extraction responses. The interface
also exposes structured output, compatible-provider thinking, context limits,
maximum output tokens, request timeout and the embedding dimensions parameter.
The running deployment fixes the actual vector dimension (1024 in the browser
workspace). A published KG cannot switch embedding space through settings; use
an explicit new generation or a new KG with a compatible deployment.

**Test connection** sends a small Hello JSON request and one embedding request.
It checks authorization, structured output and the actual vector dimension;
failed chat skips embeddings. **Test and save** requires source-data approval,
then persists only after both tests pass. A recent passing check of the identical
draft is reused for 15 minutes when saving. Explicit test buttons run a new check.
Tests count toward durable existing daily budgets, and the page updates its
counters after they finish. Native Claude input usage includes both cache creation
and cache reads. These counters are token/call limits, not a currency estimate.

Keys remain in the configured owner-only provider file. The browser receives no
saved key; blank chat keys can reuse the saved credential only for the same
endpoint and protocol. Changing to another remote endpoint requires that
endpoint's own key. URLs cannot embed credentials or query parameters. Default
selection is a hint for authorized new-KG creation and never grants access to
another organization or changes an existing KG.

Authentication rejection, an explicit billing problem, provider allowance
exhaustion, provider rate limits and this service's local daily budget each have
separate recovery messages. The console links to provider billing pages; it does
not claim a universal balance API or that an exhausted allowance always means
unpaid bills. A successful check is a point-in-time result, not a promise of
future credit or availability. Raw provider error bodies are not returned.

### Unlimited local daily usage

An operator can set either `budgets.organization` or `budgets.knowledge_base` to
JSON `null` to disable that scope's daily call and input/output token caps. The
historical limits remain the defaults for omitted fields. Both must be `null`
when neither scope should impose a daily cap. Apply a file policy change by
restarting the API and workers. Source-data approval, authorization, request
context/output bounds and provider-side quotas still apply.

Unlimited usage still creates and settles the same durable reservations and
organization/KG counters. The usage endpoint returns `limits: null` and
`available: null` for an unlimited KG; the console displays **Unlimited** and
hides the finite-budget progress bar. Zero continues to mean a zero allowance.
Existing finite-policy responses are unchanged.


### Evidence correction and model-sized source windows

Every ingestion source is sealed into windows that fit the selected extraction
and embedding profile. Chat sizing includes source JSON, prompt instructions,
structured-output schema where applicable, the maximum completion and framing
margin. Embedding inputs are bounded separately. No model-name heuristic raises
the configured context above the loaded model's actual capability.

A source-evidence validation failure permits one corrective completion containing
its safe reason. The service asks for all required passage references or omission
of unsupported claims. It checks the actual corrected request size before sending,
revalidates current source access and selection, and books each call separately.
The reply still passes the same canonical source/schema checks. Valid first-response
relations and their endpoints survive a correction that omits them. The service
unions independently validated facts, remaps temporary IDs, and deduplicates exact
facts without counting a changed confidence estimate as additional evidence.
After the one correction, independently valid entities and relations can continue
with server warnings counting discarded entities and relations. Duplicate/ambiguous identities,
unsupported spans, invalid endpoints or predicates, and invalid qualifiers never
become accepted facts. Reject an entire relation when any of its required fields
fails; do not remove a qualifier or append evidence to make it pass. The model's
wire schema cannot supply these server-generated warnings. The complete original
is retained. Malformed, truncated or refused responses are not salvaged.
Provider/auth/billing failures do not
trigger this correction loop. A successful validated chunk is cached for retry;
partial document work does not establish publication. The existing two-level
output-ceiling splitting also retains exact source offsets.

New browser configurations use the existing `source_names_v1` contract to avoid
confusing source-passage numbers with entity-array positions. The Alibaba example
reserves 8,192 output tokens; the previous 2,048 allowance truncated a dense PDF
even after source splitting. The local selected profile was updated through the
normal owner/operator configuration service, retaining its embedding space and
unlimited local daily budgets. Existing accepted projections are unchanged.


### Image capability

`supports_images` defaults to false and `image_input_tokens` to 4096. Existing
profiles omit both defaults on serialization, preserving historical cache seals.
The Advanced settings checkbox is an explicit capability/data-transmission choice,
not a model-name guess. The same selected extraction model and approved chat
connection perform image interpretation. OpenAI-compatible requests use inline
`image_url` parts; native Claude uses base64 `image` blocks. No image URL is fetched.

Only parser-isolated, checksum-verified PNG previews up to 1024 pixels/4 MiB are
sent. Reserve the provider's input token bound plus instructions, framing and up
to 2048 output tokens inside the configured context. Each request uses the normal
current-source/restricted clearance, selected profile checks, usage reservations,
provider billing diagnostics and revocation recheck. A Hello test validates chat
and embedding connectivity; it does not establish image capability or guarantee
future account credit. Live multimodal quality on private documents requires its
own approved diagnostic; this change uses synthetic/mocked model validation.


Deep retrieval with `rerank=true` sends complete original candidates to the selected
working model, under its configured context window. It interleaves independent recall
branches before the 50-source cap, then uses the validated semantic selection as the
final order. It does not truncate each source to an equal excerpt or count the reranker
as another RRF vote. Oversized requests fail with `QUOTA_EXCEEDED`; configure a provider
window sufficient for the pool. JSON-object providers use the same local schema and
subset validation as JSON-schema providers. Fast retrieval never invokes this model.
