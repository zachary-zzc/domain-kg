# Approved small model and provider configuration

Proposed model: **Qwen3.5-0.8B, Q4_0 GGUF**, published by the llama.cpp
organization. The pinned file is 563,036,064 bytes (563 MB). Its publisher SHA-256
and repository revision are in [the runtime proposal](../config/model-runtime.proposed.json).
The model is Apache-2.0 licensed. Sources: [Qwen model card](https://huggingface.co/Qwen/Qwen3.5-0.8B)
and [publisher GGUF file](https://huggingface.co/ggml-org/Qwen3.5-0.8B-GGUF/blob/8fea620810c4afa23dd6443f999a48574c1611a3/Qwen3.5-0.8B-Q4_0.gguf).

This Apple Silicon laptop has 16 GiB RAM and already has llama.cpp server build
10050 installed. The proposal uses text only, CPU inference, two threads, one
slot, 4,096 context tokens, bounded batches and disabled thinking. The approved
file was downloaded outside Git and its SHA-256 verified. The first startup
exceeded the 1.25 GB RSS guard and was stopped. Disabling weight repacking and
warmup reduced sampled peak RSS to **831,127,552 bytes** (about 793 MiB).
Two tiny schema-constrained extraction cases passed, in English (1.198 seconds)
and Chinese (1.063 seconds), each using 41 prompt and 19 completion tokens.
These measurements describe those smoke cases, not corpus performance or KG
extraction quality. The full evidence-grounded provider gate remains open.

The subsequent full source-span contract smoke rejected both English and Chinese
outputs with `EVIDENCE_VALIDATION_FAILED` (24.381 and 20.349 seconds; 934/940 input
tokens and 258/263 output tokens). No graph was published. The server's initial
large-string grammar incompatibility was resolved with a 512-character transport
schema cap; the outputs still did not meet grounding/reference validation.
The model is usable for lightweight background prototyping, but has **not**
passed the KG extraction quality gate. Use a suitable approved provider before
relying on automatic ingestion. The local profile remains disabled for corpus
access until its namespace/data-policy settings are explicitly configured.

The four database services already reserve hard limits totaling 1.476 GB.
The user explicitly confirmed that the 1.5 GB ceiling applies to the Docker
databases and approved a separately measured native LLM. The model is running
on `http://127.0.0.1:29500/v1`, named `domain-kg-small-llm`, behind an owner-only
API key in `/Users/zachary/.cache/domain-kg/llm-api-key`. An owned-process
watchdog samples RSS and stops it above 1.25 GB. This is a sampled guard, not
a kernel memory reservation. Automatic startup on login or reboot is disabled.

The [webserver profile example](../config/providers.webserver.example.json)
matches the current application schema. Copy it outside Git, make it owner-only
(`chmod 600`), and replace the endpoint, model names, embedding dimensions,
organization/KG allowlist and private API key. Set `data_policy_approved` only
after approving that endpoint for the intended data. Then point
`DOMAIN_KG_PROVIDER_CONFIG` to the private copy; a KG owner selects the profile.
The example is disabled by its empty namespace list and false approval flag.
Never place an actual key in the tracked example.

The local runtime file records the approved launch settings and measured smoke;
it is not an application provider file or an automatic startup command.
A generative LLM is not automatically a suitable embedding model. Separate
approved chat and embedding connections are implemented, with embedding-space
fingerprints that prevent incompatible query vectors from reaching a published KG.
The existing [provider boundary](providers.md) continues to validate structured
output and source spans independently of any model's claims.

The user also approved **Qwen3-Embedding-0.6B Q8_0**, a 639,150,592-byte file from
[Qwen's official GGUF repository](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF).
The exact revision and SHA-256 are recorded in the runtime file. Its publisher
documents multilingual input and up to 1,024 output dimensions. The pinned file
was downloaded and verified outside Git. Six tiny native CPU requests passed the
real embedding-client contract and four bilingual semantic comparisons in 0.688
seconds, with sampled peak RSS **965,410,816 bytes** (about 921 MiB). These are small
smoke measurements, not retrieval-quality benchmarks.

The embedding process used two threads, one slot, a 2,048-token context and last
pooling. It ran after the chat process was paused and stopped after the smoke.
The chat background process was then restored; its repeated tiny fact cases passed
with sampled peak RSS 817,528,832 bytes. The embedding endpoint on port 29501 is
currently stopped. No automatic model switching or login startup is configured.
To repeat the embedding check, pause the chat model first and run
`uv run --no-sync python scripts/smoke_native_embedding.py`; the script refuses
overlapping listeners, verifies the checksum, measures its owned process and stops
it after the tiny check.

The requested private application profile is
`/Users/zachary/.cache/domain-kg/providers.local.json` (owner-only). It now names both
local models and their deployed context limits, while preserving the empty
namespace allowlist and disabled data-policy flag. Review the safe tracked
[laptop profile example](../config/providers.laptop.example.json) and
[environment settings](../config/laptop.env.example). A fresh local KG must use
1,024 dimensions; changing an existing published generation requires migration.
The [model-specific window implementation](model-windows.md) fits source inputs
to both model limits and preserves citation offsets.

Embedding acceptance does not resolve extraction quality. A separate two-case
prototype with compact ordinal span references reduced input to 212/213 tokens,
but still failed grounding or omitted the required relationship. That prototype
is not enabled in the application. No graph was published and the extraction
provider acceptance gate remains open.
