"""Optional MCP connection lifecycle management."""

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any, Self

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

logger = logging.getLogger(__name__)


class MCPClient:
    """Manage one optional MCP session and its transport."""

    def __init__(self, *, url: str | None, connect_timeout: float) -> None:
        self.url = url
        self.connect_timeout = connect_timeout
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    async def __aenter__(self) -> Self:
        if not self.url:
            return self

        stack = AsyncExitStack()
        try:
            read_stream, write_stream = await stack.enter_async_context(
                streamable_http_client(self.url)
            )
            session = await stack.enter_async_context(
                ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=self.connect_timeout,
                )
            )
            server_info = await session.initialize()
        except (asyncio.CancelledError, Exception) as error:
            await self._close_failed_stack(stack)
            if isinstance(error, asyncio.CancelledError):
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
            logger.warning(
                "MCP connection failed; continuing with local tools: %s",
                type(error).__name__,
            )
            return self

        self._stack = stack
        self._session = session
        print(f"Connected MCP server: {server_info.server_info}")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def list_tools(self) -> list[Any]:
        """Return remote tool definitions, or none when MCP is unavailable."""
        if self._session is None:
            return []
        response = await self._session.list_tools()
        return response.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call one remote tool through the active session."""
        if self._session is None:
            raise RuntimeError("MCP client is not connected")
        return await self._session.call_tool(name=name, arguments=arguments)

    @staticmethod
    async def _close_failed_stack(stack: AsyncExitStack) -> None:
        try:
            await stack.aclose()
        except BaseException:
            logger.debug("MCP cleanup after connection failure failed", exc_info=True)
