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
A generative LLM is not automatically a suitable
embedding model. The embedding endpoint/model must be configured separately;
split provider connections are part of the next retrieval implementation step.
The existing [provider boundary](providers.md) continues to validate structured
output and source spans independently of any model's claims.
