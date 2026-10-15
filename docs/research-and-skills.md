# Domain KG research and planning skills

## Skills used during initial planning

Reviewed on 16 October 2026. These skills informed the initial planning structure. The later requested installation is recorded below. The accepted architecture was revised to LightRAG and NoSQL stores on 16 October 2026.

| Skill | Source and use | Status |
| --- | --- | --- |
| `skill-installer` | [Local skill](/Users/zachary/.codex/skills/.system/skill-installer/SKILL.md); discovery and installation procedure | Available in this session; used to guide discovery |
| `writing-plans` | [Superpowers source](https://github.com/obra/superpowers/blob/main/skills/writing-plans/SKILL.md); precise tasks, interfaces, verification, and self-review | Reviewed during planning; subsequently installed below |
| `mcp-builder` | [Anthropic source](https://github.com/anthropics/skills/blob/main/skills/mcp-builder/SKILL.md); tool design, structured results, discoverability, and protocol testing | Reviewed during planning; subsequently installed below |
| `brainstorming` | [Superpowers source](https://github.com/obra/superpowers/blob/main/skills/brainstorming/SKILL.md); optional future design discussion | Found during search; not invoked |

The useful combination is `writing-plans` for engineering delivery and `mcp-builder` for the MCP surface. The system-design and project-kickoff templates present locally target formatted Word or presentation artifacts; repository-native specifications better fit this request. The Pages writing guidance was consulted for document clarity while preserving the user's requested repository destination.

The MCP skill's Python examples use older FastMCP APIs, while the current official SDK README documents `MCPServer`. Prefer the official release documentation and Task 1's compatibility probe for imports and transport/authentication integration. Skill preferences for TypeScript do not outweigh the Python reuse and parsing requirements here.

## Installed implementation skills

Installed on 16 October 2026 at the user's request using the built-in skill installer. The 12 skills live in `/Users/zachary/.codex/skills/` and become available on the next turn. GitHub API star counts below are repository counts at review time, not ratings of individual skills.

| Repository | Stars at review | Pinned commit | Selection |
| --- | ---: | --- | --- |
| [obra/superpowers](https://github.com/obra/superpowers) | 299,745 | [`bb92a77741419a4ab5f06e711a283343f1ada0c3`](https://github.com/obra/superpowers/commit/bb92a77741419a4ab5f06e711a283343f1ada0c3) | Planning, native execution, tests, debugging, verification, and execution dependencies |
| [anthropics/skills](https://github.com/anthropics/skills) | 181,365 | [`dbd4588f9e1033efb41dad4bef2f7947c8993d44`](https://github.com/anthropics/skills/commit/dbd4588f9e1033efb41dad4bef2f7947c8993d44) | MCP tool design and evaluation guidance |
| [wshobson/agents](https://github.com/wshobson/agents) | 40,848 | [`044b4b21f083948e69ec878a7b07c8d0951a6ffa`](https://github.com/wshobson/agents/commit/044b4b21f083948e69ec878a7b07c8d0951a6ffa) | Architecture boundaries and Python testing patterns |

| Installed skill | Purpose |
| --- | --- |
| `writing-plans` | Turn specifications into tasks with exact interfaces and verification commands |
| `executing-plans` | Execute the implementation plan in the current session with a progress ledger |
| `test-driven-development` | Prove behavior changes through failing and passing tests |
| `systematic-debugging` | Reproduce failures and trace their causes before changing code |
| `verification-before-completion` | Require observed verification before claiming completion |
| `architecture-patterns` | Keep domain logic behind typed boundaries and separate transport adapters |
| `python-testing-patterns` | Organize pytest fixtures, asynchronous tests, and integration checks |
| `mcp-builder` | Design typed, discoverable tools and evaluate MCP behavior |
| `using-git-worktrees` | Workspace guidance referenced by the execution workflow |
| `finishing-a-development-branch` | Completion guidance referenced by the execution workflow |
| `requesting-code-review` | Review template referenced by the execution workflow |
| `subagent-driven-development` | Supplies workspace, task-brief, and review-package scripts also required by native execution |

The last four skills complete the upstream execution dependencies. Seven platform reference documents are included under `using-superpowers/references/`, without installing its global bootstrap `SKILL.md` or hooks. Each installed skill has a `SOURCE.json` containing its source commit and upstream file hashes, together with its upstream license notice. Upstream contents are unchanged; executable permissions on six shell helpers were restored after ZIP extraction.

Use `writing-plans` when refining a design into implementation steps, then `executing-plans` when implementation is requested. This project's `AGENTS.md` governs execution: work natively unless the user explicitly requests delegation, follow task dependencies, and mark checkboxes only after their checks pass. Architecture examples using in-memory adapters apply to isolated unit tests; permission scopes, coordinated publication, concurrent revisions, source deletion, and repair require real MongoDB replica-set, Neo4j, and Qdrant integration tests under the revised NoSQL design. The MCP skill's SDK examples still require the official-version verification described above. Its optional evaluation harness needs separate Python dependencies and Anthropic credentials; those were not configured or exercised during installation.

Verification passed for all 12 skill manifests, 45 upstream file hashes, license notices, six shell syntax checks, and two Python syntax checks. A temporary Git repository smoke test verified task extraction, failed checks remaining incomplete, passing checks entering the ledger, review-range guards, and missing-task rejection. Installation did not execute the Domain KG plan or check off its tasks.

## Construction and retrieval approaches

These are capabilities from primary project sources and design judgments for this service; none establishes expected performance on the proposed work corpus.

| Approach | Relevant capability | Fit for Domain KG |
| --- | --- | --- |
| Schema-constrained assertions with source supports | Typed facts with explicit evidence ownership and controlled entity resolution | Canonical MongoDB source/curation ledger; projects accepted observations and supports into Neo4j/Qdrant |
| LightRAG | Dual-level graph retrieval, vector-based modes, incremental updates/deletion, multiple parsers | Primary construction/retrieval integration; extend source-scoped filters, persistent edits, and publication readiness |
| Graphiti | Temporal graph ingestion, hybrid retrieval, and an existing MCP surface | Consider for evolving work events and relationships; still needs the material preservation and team control plane |
| Microsoft GraphRAG | Entity/claim extraction, community hierarchy, and local/global retrieval | Consider community summaries for broad questions after source scopes and invalidation are reliable |
| Graph traversal plus vector and lexical fusion | Bounded relationship exploration with complementary ranked evidence | Fast vector/text fusion and deep graph exploration, sharing one accepted and authorized read scope |

The accepted stack is LightRAG with MongoDB authority, Neo4j graph/full text projection, Qdrant vector projection, and original-file object storage. The graph uses typed entities, source observations, assertions, and support edges; Qdrant's internal ANN graph is not the knowledge graph. No relational database or SQL persistence layer is part of this design.

Upstream LightRAG already supports entity/relation editing, merging, and document deletion. Its inspected Qdrant query method filters by workspace, so general material and permission filtering must be added to the scoped integration. Efficient raw-block deltas, persistent curation overrides, and verified cross-store publication are service features to implement and test rather than assumed upstream guarantees.

## Accepted design changes

- LightRAG is integrated from Task 1, with source mapping and adapter concurrency checks before the main implementation.
- MongoDB replica-set transactions/CAS protect the authority ledger. Projection acknowledgements and immutable accepted references coordinate Neo4j/Qdrant updates without a distributed transaction.
- Fast retrieval avoids generative query/keyword LLMs and reranking. Deep retrieval adds graph exploration through the same read scope.
- Node/relation edit history, rollback, suppression, material replacement/removal, and artifact reuse have explicit publication and acceptance tests.
- Storage profiles register approved backend bundles; multiple KGs may share infrastructure or use independent graph/vector endpoints.

## Primary references

- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) and [getting started](https://py.sdk.modelcontextprotocol.io/get-started/) for the selected server/client API.
- [MCP Streamable HTTP](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http) for remote transport. Compatibility must follow the actual client and SDK versions.
- [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization) for resource-server behavior, discovery, and access-token validation.
- [MongoDB transactions](https://www.mongodb.com/docs/manual/core/transactions/) for authority-side atomic updates; no claim extends them to other databases.
- [Qdrant filtering](https://qdrant.tech/documentation/search/filtering/) and [payload indexing](https://qdrant.tech/documentation/manage-data/indexing/) for scoped vector candidates.
- [Neo4j full text indexes](https://neo4j.com/docs/cypher-manual/current/indexes/search-indexes/full-text-indexes/) for keyword recall; the service still enforces source scope before final top-k.
- [Docling](https://docling-project.github.io/docling/) for structured document conversion, layout, and OCR. The parser's broad capabilities do not automatically become supported release formats.
- [LightRAG backend support](https://github.com/HKUDS/LightRAG/blob/main/docs/LightRAG-API-Server.md#storage-types-supported), [mutation APIs](https://github.com/HKUDS/LightRAG/blob/main/lightrag/lightrag.py), and [Qdrant adapter](https://github.com/HKUDS/LightRAG/blob/main/lightrag/kg/qdrant_impl.py) for the integration boundary. Domain KG modes are `fast`, `deep`, `vector`, `lexical`, and `graph`.
- [Graphiti](https://github.com/getzep/graphiti) for temporal/hybrid KG construction and MCP reference implementation.
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/) for community construction and local/global retrieval.

These source URLs track evolving projects. Task 1 records exact dependency versions, and adapter work records the upstream version it supports.

## Local source paths reviewed

The original workspace is `/Users/zachary/Workspace/PhageAgent`. Review boundaries and code findings are in [Code review and reuse](code-reuse.md).

```text
codes/bioagent/tool_box/AGENTS.md
codes/bioagent/app/AGENTS.md
codes/bioagent/tool_box/tools_impl/graph_rag/graph_rag.py
codes/bioagent/tool_box/tools_impl/graph_rag/service.py
codes/bioagent/tool_box/tools_impl/graph_rag/GRAPH_RAG_USAGE.md
codes/bioagent/tool_box/tools_impl/lightrag_query.py
codes/bioagent/tool_box/tools_impl/document_reader.py
codes/bioagent/app/tests/tools/test_lightrag_query_tool.py
codes/phageagent_decomposition/test/tools/test_graph_rag_tool.py
codes/data2evidence/src/scientific-graph.ts
experiments/knowledge_graph/lightrag_vectorrag_eval/README.md
experiments/knowledge_graph/lightrag_vectorrag_eval/construction_scripts/scripts/rag_query_gateway.py
experiments/knowledge_graph/lightrag_vectorrag_eval/construction_scripts/scripts/prepare_new_md_deduped_to_shards.py
experiments/knowledge_graph/lightrag_vectorrag_eval/construction_scripts/scripts/build_global_dedup_manifest.py
experiments/knowledge_graph/lightrag_vectorrag_eval/construction_scripts/scripts/build_rag_lexical_index.py
```
