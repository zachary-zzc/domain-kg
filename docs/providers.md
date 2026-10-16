# Provider profiles

Set `DOMAIN_KG_PROVIDER_CONFIG` to an owner-readable-only JSON file outside Git.
The file has `connections` and `profiles` arrays validated by
`providers/profiles.py`. Connections contain a private `ref`, HTTPS `endpoint`
(the API prefix before `/chat/completions`), and `api_key`. HTTP is allowed only
on development loopback. Ambient proxy settings and redirects are disabled.

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
fingerprint covers the embedding endpoint, model, dimensions and weight revision.
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

Extraction uses strict structured Chat Completions output with `store:false`, no
tools, bounded completion tokens, a wall-clock deadline, a 2 MiB response limit,
and current access checks before transmission and after completion. Source text
is supplied as untrusted JSON data. Refusals, truncation, tool requests, unknown
fields, and invalid evidence fail closed. Usage counters contain namespace,
profile and token counts; source bodies and raw provider errors are not logged.

The server validates exact block locations, character offsets and quotations;
names, aliases, identifiers and descriptions remain extractive. A verified
support establishes correspondence to the source, not truth. Conflicting and
negative claims keep distinct assertion identities. Grounded explicit
identifiers can unite entities across materials; equal names alone cannot.
Ambiguous homonyms remain separate until an authorized curation merges them.

LightRAG 1.5.7 processes server-generated opaque engine names under the source
namespace. Its output must preserve every expected entity, endpoint and source
mapping. Canonical IDs and database operations are constructed by the server.
Display observations, spans and supports remain source-owned in MongoDB.
Preparation never makes records searchable by itself.

Run `python3 scripts/smoke.py --task 9 --keep-services` for the laptop check.
It uses tiny public synthetic source fixtures, real local stores, and deterministic
HTTP provider responses. It verifies mapping fidelity and rejected fabricated
claims without a model download or paid request. An actual approved-provider
quality/usage smoke remains an external acceptance gate until configured; this
local check does not establish a provider model's extraction quality.
