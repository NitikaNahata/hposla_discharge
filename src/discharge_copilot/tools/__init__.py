"""Tool assembly for the worker agents.

Two sources, deliberately kept distinct (see `docs/integration-decision.md`):

* **MCP tools** — capabilities the agent does not own, reached over stdio through
  `langchain-mcp-adapters` (AC-10).
* **In-process tools** — the agentic-RAG lookup, which searches the agent's own knowledge base
  and is part of its cognition rather than an external system (AC-11).

`build_toolbox` never raises. If the MCP server cannot start, the workers run with whatever
tools did load and the failure is recorded in the trace — degraded capability beats a dead run
(NFR-07).
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from .mcp_client import DischargeMCPClient
from .rag import make_rag_tool


def build_toolbox(
    tracer: Any = None,
    *,
    fault: str = "",
    include_mcp: bool = True,
    include_rag: bool = True,
) -> tuple[list[BaseTool], DischargeMCPClient | None]:
    """Assemble the worker toolbox.

    Args:
        tracer: receives connection, tool-call and failure events.
        fault: fault-injection mode passed through to the MCP server ('timeout' | 'error').
        include_mcp: load the custom MCP server's tools.
        include_rag: load the agentic-RAG guidance tool.

    Returns:
        The tools, and the MCP client (for resource reads and inventory) or None.
    """
    tools: list[BaseTool] = []
    client: DischargeMCPClient | None = None

    if include_mcp:
        client = DischargeMCPClient(tracer=tracer, fault=fault)
        mcp_tools = client.load_tools()
        tools.extend(mcp_tools)
        if not mcp_tools and tracer:
            tracer.emit(
                "toolbox_degraded",
                reason="mcp_unavailable",
                error=client.last_error[:300],
                note="Workers will proceed with local tools only.",
            )

    if include_rag:
        try:
            tools.append(make_rag_tool(tracer))
        except Exception as exc:
            if tracer:
                tracer.emit("toolbox_degraded", reason="rag_unavailable", error=str(exc)[:300])

    if tracer:
        tracer.emit(
            "toolbox_ready",
            tools=[t.name for t in tools],
            count=len(tools),
            mcp_connected=bool(client and client._connected),
        )

    return tools, client


__all__ = ["DischargeMCPClient", "build_toolbox", "make_rag_tool"]
