"""Safe protocol errors never include rejected private input or backend exceptions."""

from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, InputRequiredResult, TextContent, Tool
from pydantic import ValidationError

from domain_kg.errors import DomainError, ErrorCode


def tool_error(error: DomainError) -> CallToolResult:
    return CallToolResult(
        is_error=True,
        content=[TextContent(type="text", text=f"{error.code}: {error.message}")],
        structured_content={
            "code": error.code.value,
            "message": error.message,
            "retryable": error.retryable,
            "request_id": str(error.request_id),
        },
    )


class SafeMCPServer(MCPServer[None]):
    async def list_tools(self) -> list[Tool]:
        return [
            tool.model_copy(
                update={"input_schema": tool.input_schema | {"additionalProperties": False}}
            )
            for tool in await super().list_tools()
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        context: Context[None, Any] | None = None,
    ) -> CallToolResult | InputRequiredResult:
        try:
            # The SDK's function-argument model ignores unknown top-level fields.
            # Enforce the advertised contract before it can discard those fields.
            tool = next((tool for tool in await self.list_tools() if tool.name == name), None)
            if tool is None or set(arguments) - set(tool.input_schema.get("properties", {})):
                return tool_error(
                    DomainError(
                        ErrorCode.VALIDATION_ERROR, "Unknown tool or unsupported argument fields."
                    )
                )
            return await super().call_tool(name, arguments, context)
        except ToolError as error:
            code = (
                ErrorCode.VALIDATION_ERROR
                if isinstance(error.__cause__, ValidationError)
                else ErrorCode.BACKEND_UNAVAILABLE
            )
            return tool_error(DomainError(code, "The tool request could not be completed."))
