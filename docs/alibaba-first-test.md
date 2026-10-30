# Alibaba 35B: first MCP test and 200 MB source corpus

The existing production algorithm is available through `domain_kg_search`:
`fast` fuses filtered vector and lexical retrieval; `deep` adds scoped LightRAG
graph exploration and source-supported paths. Source-role extraction uses the
canonical evidence validator before publication. The offline experimental REL
planner is not enabled. The new `domain_kg_set_provider` tool and equivalent
`PUT /api/v1/kgs/{kg_id}/provider` expose the existing owner-only profile selection
service, so setup no longer requires an internal Python call.

The Alibaba profile uses `qwen3.6-35b-a3b` for extraction, explicit JSON Object
output and disabled thinking. It uses `text-embedding-v4` at its default 1,024
dimensions, with batches of at most ten texts. Generative extraction and embedding
are separate APIs. This initial profile caps each KG and organization at 12 calls,
200,000 reserved/spent input tokens and 8,192 output tokens per budget period.
These are first-test limits, not full-corpus ingestion settings.

Official references checked on 2026-10-28:
[35B model](https://help.aliyun.com/zh/model-studio/qwen3-6-35b-a3b),
[embedding API](https://help.aliyun.com/zh/model-studio/embedding),
[structured output](https://help.aliyun.com/zh/model-studio/qwen-structured-output),
and [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).
The installed SDK is 2.3.0; the test uses its `Client` and Streamable HTTP transport.

## Repeat the first test

Run these commands from the repository root with the existing pinned environment:

```bash
docker compose up -d --wait mongo neo4j qdrant object-store
uv run --no-sync python scripts/init_replica_set.py
uv run --no-sync pytest tests/evaluation/test_alibaba_mcp.py -q
```

The default run uses real stores and deterministic remote-model responses. To use
the actual Alibaba API, set an owner-only key file or `DASHSCOPE_API_KEY`:

```bash
DOMAIN_KG_ALIBABA_LIVE=1 \
DOMAIN_KG_ALIBABA_KEY_FILE=secrets/dashscope-api-key \
uv run --no-sync pytest tests/evaluation/test_alibaba_mcp.py -q -s
```

If the key requires a workspace/region endpoint, also set
`DOMAIN_KG_ALIBABA_ENDPOINT` to that official Alibaba OpenAI-compatible base URL.
Credentials are never MCP arguments. No local model download is needed.

The test selects the approved profile as a signed owner through MCP, verifies an
identical HTTP replay and rejects stale revisions and editor configuration. It
ingests `Alpha uses Beta. Beta is part of Gamma.` through MCP, runs the production
worker, verifies publication, retrieves the original source with citations in both
modes, and rejects a signed identity without a KG grant. Search adds only embedding
calls. A small public synthetic source makes failures easy to diagnose.

The report is `.cache/alibaba-35b/live-mcp-smoke.json`. The test cleans up its own
KG, database and projections; the local database containers remain running until
stopped. The signed issuer is a test fixture. This proves the application path,
not an external IdP deployment or broad extraction quality.

## Use the profile in your own KG

Use a fresh KG provisioned with `DOMAIN_KG_EMBEDDING_DIMENSIONS=1024`. Obtain the
organization and KG IDs through the authenticated catalog. Then create the
private configuration for that public-data namespace:

```bash
uv run --no-sync python -m scripts.configure_alibaba \
  --org-id "$ORG_ID" --kg-id "$KG_ID" \
  --secret-file secrets/dashscope-api-key \
  --output secrets/providers.alibaba.json
```

The command refuses to overwrite files. The tracked example remains disabled;
the private copy approves only the supplied namespace, with restricted materials
still disallowed. Both API and worker must load the same configuration:

```bash
export DOMAIN_KG_PROVIDER_CONFIG="$PWD/secrets/providers.alibaba.json"
export DOMAIN_KG_EMBEDDING_DIMENSIONS=1024
uv run --no-sync python -m domain_kg
```

With the normal verified owner credential, call `domain_kg_set_provider`:

```json
{
  "kg_id": "YOUR_KG_UUID",
  "org_id": "YOUR_ORGANIZATION_UUID",
  "command": {
    "profile_id": "908fbbfa-e1bc-4dbc-8339-e05f023e2f05",
    "expected_revision": 0,
    "idempotency_key": "alibaba-first-selection"
  }
}
```

`expected_revision` is the provider-selection revision, initially zero. HTTP takes
the command object as its body. Selection returns revision 1. Existing accepted
embedding spaces cannot be changed by selecting an incompatible profile; use the
existing generation-rebuild workflow for that case.

Next call `domain_kg_add_material` with a small inline source, run
`uv run --no-sync python -m domain_kg.worker --once` with the same environment,
and poll `domain_kg_get_job`. Once ready, call `domain_kg_search` with:

```json
{
  "org_id": "YOUR_ORGANIZATION_UUID",
  "command": {
    "kg_ids": ["YOUR_KG_UUID"],
    "query": "What does Alpha use?",
    "mode": "deep"
  }
}
```

Inspect original evidence and citations before expanding the input. The service
returns evidence; a generative answer reader is not part of this search endpoint.

## Prepared source corpus

The prepared corpus is in `.cache/alibaba-35b/corpus/prepared/`: **200,011,011 UTF-8
text bytes in 11,066 complete unique articles**. This is approximately 200 decimal
MB (190.75 MiB), excluding JSON metadata and download overhead. It has no QA labels
or repeated-article padding. Ninety-one exact duplicate articles were omitted.

Source: [Salesforce WikiText-103 raw dataset card](https://huggingface.co/datasets/Salesforce/wikitext/blob/b08601e04326c79dfdd32d625aee71d232d685c3/README.md),
revision `b08601e04326c79dfdd32d625aee71d232d685c3`, first training shard.
The dataset is English Wikipedia text under its upstream CC-BY-SA-3.0/GFDL terms.
`NOTICE.txt` preserves attribution; `materials.jsonl` records title, article link,
path, byte count and SHA-256 for every file. Article links are title-based, not
pinned Wikipedia revisions. Upstream punctuation tokenization is retained.

The publisher's shard SHA-256 is
`74da360f23826045b3e6ac6375411fdb15f003030aa74f2596ed08b857cb9212`.
The prepared `materials.jsonl` SHA-256 is
`5e4d3c37edfda4cb7f82d5db882217e42bc15941136d7bbbfbd4bcfe0bd48f6e`.
`manifest.json` records the preparation, and `verification.json` records independent
per-file verification. No corpus material has been sent to Alibaba or ingested.

To reproduce using the already installed isolated Parquet reader:

```bash
PYTHONPATH=.cache/public-qa-2026-10-19/reader \
uv run --no-sync python -m scripts.prepare_wikitext \
  --parquet .cache/alibaba-35b/corpus/raw/train-00000-of-00002.parquet \
  --output .cache/alibaba-35b/corpus/reproduced
```

The input download URL is in `manifest.json`; the preparer checks the publisher
hash before reading. It refuses to overwrite an output directory and stops at a
whole-article boundary after reaching 200 million text bytes. The optional Parquet
reader is preparation tooling, not a server dependency.

Start corpus testing with one small article and manually inspect extraction and
citations. The current retrieval implementation has explicit bounds of 1,000
eligible bindings and 20,000 sealed rows; preparing 200 MB does not establish that
an unfiltered query over the entire corpus fits these limits. Full-corpus ingestion,
capacity measurement, cost sizing and semantic quality evaluation remain separate.
The existing 219.6 MB public-QA bundle includes questions and repeated contexts;
it is retained separately and is not counted as this source-only corpus.
