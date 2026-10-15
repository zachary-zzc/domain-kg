# Domain KG project instructions

Read `docs/design.md`, `docs/contracts.md`, and the relevant task in `docs/implementation-plan.md` before implementing a feature. The current repository contains planning documents only. Task checkboxes describe future work and must remain unchecked until the corresponding verification succeeds.

## Project constraints

- Implement a shared team service. Identity, permissions, and management of multiple KGs belong to the initial release.
- Keep domain logic independent of MCP, HTTP, and the PhageAgent application. MCP and HTTP call the same services and policy evaluator.
- Namespace every material, graph object, vector, job, cache entry, and citation by organization and KG. Validate every cross-document/store reference; compound indexes, scoped repositories, and reconciliation must prevent cross-KG graph edges.
- Verify access before retrieval and again before returning results. Apply visibility to every graph step and every evidence-derived field, including names, descriptions, counts, exports, and citations.
- Store original files and a source evidence ledger. Assertions require supporting spans, explicit manual curation provenance, or an imported-unverified state.
- Preserve independently supported assertions when removing a material. Replacing a version must not expose a partially constructed graph.
- Use external OIDC identity management. Derive identity from verified credentials; request arguments never establish the caller or their permissions.
- Verify current official MCP SDK APIs before implementing transport. Do not copy older FastMCP imports from a skill without checking the selected SDK version.
- Treat material text and model extraction output as untrusted data. Model output cannot choose database commands, permission grants, or tool actions.
- Use narrow typed interfaces and parameterized database operations. Do not expose raw SQL, Cypher, database DSNs, or arbitrary server file paths through MCP.
- Use LightRAG, MongoDB, Neo4j, Qdrant, and object storage as the initial stack. The service has no relational database or SQL persistence layer.
- MongoDB owns accepted materials, evidence, curation, and publication manifests. Neo4j/Qdrant are derived projections; do not claim a transaction across stores.
- Read `docs/revisions-and-publication.md` before implementing changes. Native LightRAG mutations must use isolated staging or scoped adapters and cannot modify accepted projections directly. Publish only after graph/vector readiness checks; never overwrite accepted projection records in place.
- Keep manual node/relation edits in durable curation history. Re-ingestion, source removal, and repair must preserve overrides and independent evidence ownership.
- `fast` retrieval performs filtered vector/text search without generative query LLMs or reranking; `deep` adds graph exploration. Query embeddings may call a provider.
- Use real MongoDB replica-set, Neo4j, and Qdrant integration tests for permissions, publication failures, concurrent revisions, and deletion. A workspace filter alone is insufficient.
- Keep secrets, original private materials, and generated indexes outside Git. Preserve attribution when adapting source code.
- Neo4j is the initial graph engine and LightRAG is the initial construction/retrieval integration. Additional graph engines require a measured need and a separate decision.

## Working method

Implement tasks in dependency order. For each behavior change, define the failure case, implement the smallest complete feature, and run the task's verification commands. Keep transport wrappers thin and focus tests on permission boundaries, lifecycle correctness, retrieval behavior, and failure recovery.

Use native execution in the current chat unless the user explicitly requests delegation. References to external planning skills are guidance; they do not authorize installations, new chats, external messaging, production deployment, or corpus migration.
