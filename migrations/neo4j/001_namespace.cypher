// Attempt-specific staging, distinct from any accepted publication manifest.
CREATE CONSTRAINT scoped_projection_identity IF NOT EXISTS
FOR (n:ScopedProjection)
REQUIRE (n.org_id, n.kg_id, n.projection_id) IS UNIQUE;
