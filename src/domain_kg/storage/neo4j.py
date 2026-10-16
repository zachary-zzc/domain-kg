"""Immutable attempt nodes and source-scoped support edges, using fixed Cypher only."""

from typing import TYPE_CHECKING, Any

from neo4j import AsyncManagedTransaction

from domain_kg.errors import DomainError, ErrorCode, inaccessible_reference

if TYPE_CHECKING:
    from domain_kg.storage.router import GraphBinding


def inconsistent() -> DomainError:
    return DomainError(
        ErrorCode.PROJECTION_INCONSISTENT,
        "A projection does not match its sealed manifest.",
        retryable=True,
    )


class GraphProjection:
    def __init__(self, binding: "GraphBinding") -> None:
        self.binding = binding

    def _scope(self, rows: list[dict[str, Any]]) -> None:
        if any(
            (row["org_id"], row["kg_id"])
            != (str(self.binding.namespace.org_id), str(self.binding.namespace.kg_id))
            for row in rows
        ):
            raise inaccessible_reference()

    async def initialize(self) -> None:
        for statement in (
            "CREATE CONSTRAINT scoped_entity_anchor IF NOT EXISTS FOR (n:EntityAnchor) "
            "REQUIRE (n.org_id,n.kg_id,n.entity_id) IS UNIQUE",
            "CREATE FULLTEXT INDEX scoped_evidence_text IF NOT EXISTS FOR (n:ScopedProjection) "
            "ON EACH [n.text,n.lexical_key] OPTIONS "
            "{indexConfig:{`fulltext.eventually_consistent`:false}}",
        ):
            await self.binding.driver.execute_query(statement)
        await self.binding.driver.execute_query("CALL db.awaitIndexes(30)")

    async def write(self, rows: list[dict[str, Any]]) -> None:
        self._scope(rows)
        await self.initialize()
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]

            async def write(
                transaction: AsyncManagedTransaction, batch: list[dict[str, Any]]
            ) -> None:
                cursor = await transaction.run(
                    "UNWIND $rows AS row MERGE (n:ScopedProjection "
                    "{org_id:row.org_id,kg_id:row.kg_id,projection_id:row.projection_id}) "
                    "ON CREATE SET n += row RETURN properties(n) AS value",
                    rows=batch,
                )
                actual = {item["value"]["projection_id"]: item["value"] async for item in cursor}
                if any(actual.get(row["projection_id"]) != row for row in batch):
                    raise inconsistent()
                # Anchor nodes have no name, description or other derived fields.
                for key, relationship in (
                    ("entity_id", "OBSERVES"),
                    ("subject_id", "SUBJECT"),
                    ("target_id", "TARGET"),
                ):
                    # Relationship names are fixed server constants, never material/model data.
                    cursor = await transaction.run(
                        "UNWIND $rows AS row WITH row WHERE row." + key + " IS NOT NULL "
                        "MATCH (n:ScopedProjection {org_id:row.org_id,kg_id:row.kg_id,"
                        "projection_id:row.projection_id}) "
                        "MERGE (a:EntityAnchor {org_id:row.org_id,kg_id:row.kg_id,entity_id:row."
                        + key
                        + "}) MERGE (n)-[:"
                        + relationship
                        + "]->(a)",
                        rows=batch,
                    )
                    await cursor.consume()

            async with self.binding.driver.session() as session:
                await session.execute_write(write, batch)

    async def verify(self, rows: list[dict[str, Any]], *, lexical: bool = False) -> bool:
        self._scope(rows)
        for start in range(0, len(rows), 100):
            batch = rows[start : start + 100]
            actual, _, _ = await self.binding.driver.execute_query(
                "UNWIND $rows AS row MATCH (n:ScopedProjection "
                "{org_id:row.org_id,kg_id:row.kg_id,projection_id:row.projection_id}) "
                "RETURN properties(n) AS value, "
                "[(n)-[:OBSERVES]->(a) | [a.org_id,a.kg_id,a.entity_id]] AS observations, "
                "[(n)-[:SUBJECT]->(a) | [a.org_id,a.kg_id,a.entity_id]] AS subjects, "
                "[(n)-[:TARGET]->(a) | [a.org_id,a.kg_id,a.entity_id]] AS targets",
                rows=batch,
            )
            by_id = {item["value"]["projection_id"]: item for item in actual}
            for row in batch:
                item = by_id.get(row["projection_id"])
                if item is None or item["value"] != row:
                    return False
                for key, result in (
                    ("entity_id", "observations"),
                    ("subject_id", "subjects"),
                    ("target_id", "targets"),
                ):
                    expected = [[row["org_id"], row["kg_id"], row[key]]] if key in row else []
                    if item[result] != expected:
                        return False
            if lexical:
                found, _, _ = await self.binding.driver.execute_query(
                    "UNWIND $rows AS row CALL (row) { "
                    "CALL db.index.fulltext.queryNodes('scoped_evidence_text',"
                    "'lexical_key:' + row.lexical_key) YIELD node "
                    "WHERE node.org_id=row.org_id AND node.kg_id=row.kg_id "
                    "AND node.projection_id=row.projection_id RETURN count(node) AS hits } "
                    "RETURN sum(hits) AS total",
                    rows=batch,
                )
                if not found or found[0]["total"] != len(batch):
                    return False
        return True
