"""Shared typed tool registration, authenticated context and safe errors."""

import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec
from uuid import UUID

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel

from domain_kg.errors import DomainError, ErrorCode
from domain_kg.identity.models import AccessContext, Action
from domain_kg.transport.auth import verified_access
from domain_kg.transport.errors import tool_error
from domain_kg.transport.runtime import ServiceRuntime

P = ParamSpec("P")
logger = logging.getLogger(__name__)


def output(value: BaseModel, text: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=value.model_dump(mode="json"),
    )


class MCPTools:
    def __init__(self, server: MCPServer[None], runtime: ServiceRuntime | None) -> None:
        self.server, self.runtime = server, runtime

    def services(self) -> ServiceRuntime:
        if self.runtime is None:
            raise DomainError(
                ErrorCode.UNAUTHENTICATED, "A verified HTTP bearer credential is required."
            )
        return self.runtime

    async def access(
        self, kg_ids: tuple[UUID, ...], action: Action, org_id: UUID | None
    ) -> AccessContext:
        return await verified_access(self.runtime, kg_ids, action, org_id)

    def tool(
        self,
        name: str,
        *,
        read: bool = False,
        destructive: bool = False,
        external: bool = False,
        idempotent: bool = True,
    ) -> Callable[[Callable[P, Awaitable[CallToolResult]]], Callable[P, Awaitable[CallToolResult]]]:
        def decorate(
            function: Callable[P, Awaitable[CallToolResult]],
        ) -> Callable[P, Awaitable[CallToolResult]]:
            @wraps(function)
            async def guarded(*args: P.args, **kwargs: P.kwargs) -> CallToolResult:
                try:
                    return await function(*args, **kwargs)
                except DomainError as error:
                    return tool_error(error)
                except Exception as error:
                    logger.error("MCP tool failed: tool=%s kind=%s", name, type(error).__name__)
                    return tool_error(
                        DomainError(
                            ErrorCode.BACKEND_UNAVAILABLE,
                            "The requested service is unavailable.",
                            retryable=True,
                        )
                    )

            self.server.tool(
                name="domain_kg_" + name,
                annotations=ToolAnnotations(
                    read_only_hint=read,
                    destructive_hint=destructive,
                    idempotent_hint=idempotent,
                    open_world_hint=external,
                ),
            )(guarded)
            return guarded

        return decorate
