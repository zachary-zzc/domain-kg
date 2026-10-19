# Real medical PDF run / 真实医学 PDF 抽取

A subsequent independent-recall/4B-filter experiment improved answers on eight
development problems from3/8 to5/8 without a citation gain. Its24-question fresh
comparison stopped at two consecutive timeouts after only five completed pairs;
it does not qualify full-PDF work. Models stopped; see
[the filter report](dual-filter-report.md). Production implementation unchanged.

The subsequent frozen 40-new-question 4B comparison is complete: pure-vector
and dual-path answer accuracy both reached 34/40 (85%) after semantic audit;
citation completeness was 31/36 (86.1%) and 29/36 (80.6%). The user-approved
90% citation threshold was not met, so no full-PDF construction followed.
See [the new-question report](4b-holdout-report.md). No production code changed.

The full run is **not complete**. The approved 4B model passed only one of three
final short cases; its complete-PDF run was correctly withheld. The software
preparations and scoped graph overview do not establish extraction quality.

整篇抽取仍未完成。4B 最终仅 1/3 短样本通过；未发布不合格结果，也未使用模拟图替代。

19 October 2026: the bounded iterative experiment stopped after five unsuccessful
strategies. The best 0.8B compact protocol found two of three provisional
development relations with one false claim; same-weight review was unreliable.
The new 4B trial timed out and supplies no semantic score. No full-document run
or publication followed. See [the complete iteration report](iterative-extraction-report.md).

A separately authorized 4B offline hybrid-retrieval pilot subsequently completed
with five bounded strategies. The exploratory name/structure graph improved
answer matching on ten provisional questions from 8/10 to 10/10, but complete
support citations remained 8/10. This uses only eight PDF excerpts plus synthetic
chains, includes author-agent relation review, and does not qualify full-PDF
extraction or publication. See [the hybrid report](hybrid-retrieval-report.md).

## Provider configuration

Private file: `/Users/zachary/.cache/domain-kg/providers.medical-api.local.json`.
It was created with mode 0600, with the existing local embedding key preserved
privately. Set the remote connection's endpoint/key and the profile's extraction
model. `data_policy_approved` stays false until the operator approves using this
public NHS PDF with that provider. Do not send API keys in chat or commit them.

API endpoint 和模型名可以在聊天中说明；key 只填上述私有文件。修改其 `medical-api`
连接和 `extraction_model`，确认只处理这份公开 NHS 文件后才启用
`data_policy_approved`。服务端会把模板绑定到本次固定哈希 PDF 的单个 KG；空的
`allowed_namespaces` 从来不是共享服务中的通配授权。

Select the file explicitly; no remote API is auto-selected and no disabled API
silently falls back to the native model:

```bash
DOMAIN_KG_MEDICAL_API_CONFIG=/Users/zachary/.cache/domain-kg/providers.medical-api.local.json \
  uv run --no-sync python -m scripts.medical_extract
```

This makes at most three real short calls, records actual output/usage outside
Git, validates evidence/schema and invokes pinned LightRAG. Inspect the semantic
meaning as well as source correspondence. An invalid or unqualified result must
not trigger the whole run. Remote weights cannot be checksum-verified locally;
the report identifies the approved endpoint/model/revision through a fingerprint
and does not claim that a local GGUF hash describes the remote model.

## Resource and completion boundaries

- Docker databases: actual combined hard limits 1,476,395,008 bytes, swap disabled;
  `config/laptop-smoke.json` remains authoritative.
- Native 4B: separately approved 3.5 GB sampled RSS watchdog, two CPU threads,
  no GPU, one slot, 4096 context; all owned model processes have stopped.
- Embeddings: approved Qwen3-Embedding-0.6B Q8_0, 1024 dimensions, native 1.25 GB
  watchdog, two threads; run after extraction, never alongside a native chat model.
- Current input preparation: all 7 PDF pages / 228 source blocks can fit 29 windows.
  Source offsets remain exact. This is not proof that every clinical fact was found.

`tests/evaluation/test_real_medical_kg.py` is an opt-in runner prepared for real
HTTP model/embedding calls and genuine MongoDB/Neo4j/Qdrant/S3 publication. Its
quality fingerprint, full input coverage and projection receipts must pass.
It has **not yet completed a corpus run**. Its local authentication is a test
session, and its present fixture lifecycle is temporary; persistent retention
and reopening must be finalized before claiming a durable KG is available.

The ordinary small verification suite intentionally skips this expensive opt-in
run. This does not count as full-document acceptance. No external OIDC, clinical
accuracy, or observed browser-canvas interaction is claimed.
