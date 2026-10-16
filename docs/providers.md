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
