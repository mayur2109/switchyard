"""MCP stdio bridge; model weights remain in the shared runtime process."""

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .client import AsyncClient
from .contracts import DecisionRequest, ItemsRequest


def create_server(client: AsyncClient | None = None) -> FastMCP:
    server = FastMCP("Local Decision Kit")
    client = client or AsyncClient()
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @server.tool(annotations=annotations)
    async def decide(request: DecisionRequest) -> dict:
        """Evaluate supplied state against explicit choice, score, or noul questions.

        Returns probabilistic judgments, not permissions or verified facts.
        Does not retrieve content, execute actions, or generate text.
        """
        return await client.decide(request)

    @server.tool(annotations=annotations)
    async def evaluate_items(request: ItemsRequest) -> dict:
        """Classify supplied passages or logs, or suggest caller-defined routes.

        Preserves IDs and citations. Uncertain items and mandatory content are kept.
        No filesystem or network knowledge retrieval occurs.
        """
        return await client.evaluate_items(request)

    @server.tool(annotations=annotations)
    async def capabilities() -> dict:
        """Inspect runtime readiness, loaded checkpoints, limits, and aggregate counts."""
        return await client.call("capabilities")

    return server
