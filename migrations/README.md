# Initial namespace schema

Mongo migration `001_namespace.py` delegates to the same idempotent schema installer exercised by the real-store tests. It installs strict document shape validators, unique `(org_id, kg_id, id)` indexes, and scoped reference lookup indexes. Logical IDs can repeat across KGs; physical Mongo IDs include both namespace components.

Neo4j and Qdrant definitions document the schema applied by `ProjectionStore.initialize`. Qdrant dimensions come from the approved embedding profile, never model extraction output. The source-bound staging record has an immutable UUID derived from organization, KG, logical object, generation, and attempt. Graph writes use `ON CREATE`; conflicting content in the same attempt fails before vector writes. A failed graph/vector pair is not published.

MongoDB validators do not provide cross-document foreign keys. `NamespaceRepository` validates the namespace and existence of every typed reference before insert, follows source versions to their material owner, and offers no unscoped public query or deletion API. It is an internal integrity boundary; Task 4 adds current policy checks. Privileged direct database edits can bypass reference checks, so reconciliation checks canonical references and both directions of the graph/vector mapping. Publication readiness and accepted-manifest reconciliation are implemented in Task 10.

These migrations do not grant roles, accept a publication, or expose stored credentials. Tests use a disposable database, synthetic sources, a three-dimensional vector, and the existing resource-capped services.
