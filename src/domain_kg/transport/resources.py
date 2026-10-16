"""Authorized MCP metadata templates call the same services as tools and HTTP."""

from uuid import UUID

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError

from domain_kg.errors import DomainError
from domain_kg.graph.schema import schema_for_kg
from domain_kg.identity.models import Action
from domain_kg.transport.auth import verified_access
from domain_kg.transport.runtime import ServiceRuntime


def register_resources(server: MCPServer[None], runtime: ServiceRuntime | None) -> None:
    @server.resource(
        "domain-kg://{org_id}/{kg_id}/materials/{material_id}",
        name="domain_kg_material_metadata",
        mime_type="application/json",
        description="Read current authorized material metadata and accepted version summaries.",
    )
    async def material(org_id: str, kg_id: str, material_id: str) -> str:
        try:
            org, kg, identifier = UUID(org_id), UUID(kg_id), UUID(material_id)
            access = await verified_access(runtime, (kg,), Action.READ, org)
            assert runtime is not None
            return (
                await runtime.inspection.get(access, kg, identifier, include_unverified=True)
            ).model_dump_json()
        except DomainError as error:
            raise ResourceError(f"{error.code}: {error.message}") from None
        except ValueError:
            raise ResourceError("VALIDATION_ERROR: Invalid resource identifiers.") from None
        except Exception:
            raise ResourceError("BACKEND_UNAVAILABLE: Resource could not be read.") from None

    @server.resource(
        "domain-kg://{org_id}/{kg_id}/schema",
        name="domain_kg_schema",
        mime_type="application/json",
        description="Read the current authorized KG domain schema.",
    )
    async def schema(org_id: str, kg_id: str) -> str:
        try:
            org, kg = UUID(org_id), UUID(kg_id)
            access = await verified_access(runtime, (kg,), Action.READ, org)
            assert runtime is not None
            result = await schema_for_kg(runtime.policy, access, kg)
            await runtime.policy.revalidate(access, action=Action.READ)
            return result.model_dump_json()
        except DomainError as error:
            raise ResourceError(f"{error.code}: {error.message}") from None
        except ValueError:
            raise ResourceError("VALIDATION_ERROR: Invalid resource identifiers.") from None
        except Exception:
            raise ResourceError("BACKEND_UNAVAILABLE: Resource could not be read.") from None
