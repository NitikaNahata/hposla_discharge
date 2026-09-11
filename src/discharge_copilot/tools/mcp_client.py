"""MCP integration via langchain-mcp-adapters (AC-10).

Spawns `mcp_server/discharge_server.py` over stdio and loads its tools as LangChain `BaseTool`
instances the workers can call. Resources are fetched separately, since MCP resources are
addressable content rather than callable actions.

Two design points worth stating:

**The event loop.** `MultiServerMCPClient` is async, but LangGraph nodes here are synchronous.
Rather than colouring the whole graph async for one integration, this module owns a dedicated
background event loop and marshals calls onto it. That keeps the async boundary in one file.

**Failure is expected, not exceptional.** A subprocess that hangs or dies is a normal operating
condition for a tool server. `load_tools` returns an empty list rather than raising, and every
tool wrapper converts a failure into a structured error string the model can read and reason
about. This is what makes the `--fault-inject mcp_timeout` trace a genuine recovery rather than
a crash (AC-12, NFR-07).
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import sys
import threading
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel

from ..config import get_config

# The four tools and two resources the server publishes (AC-09).
EXPECTED_TOOLS = (
    "patient_lookup",
    "medication_interaction_check",
    "schedule_followup",
    "check_transport_availability",
)
EXPECTED_RESOURCES = ("discharge://protocol/{condition}", "formulary://medications")


class _NoArgs(BaseModel):
    """Fallback schema for an MCP tool that declares no arguments."""



# ---------------------------------------------------------------------------
# Background event loop
# ---------------------------------------------------------------------------


class _LoopRunner:
    """A dedicated event loop on a daemon thread, so sync nodes can await async MCP calls."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, daemon=True, name="mcp-loop"
                )
                self._thread.start()
            return self._loop

    def run(self, coro, timeout: float):
        return asyncio.run_coroutine_threadsafe(coro, self.loop()).result(timeout=timeout)

    def shutdown(self) -> None:
        with self._lock:
            if self._loop and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None


_RUNNER = _LoopRunner()
atexit.register(_RUNNER.shutdown)


def _is_adapter_error(text: str) -> bool:
    """Whether a returned string is actually an adapter-reported tool failure.

    `langchain-mcp-adapters` catches server-side exceptions and returns them as text. Without
    this check a failed call would be recorded as a successful one.
    """
    head = text.lstrip()[:80].lower()
    return head.startswith(("error executing tool", "error calling tool", "tool error"))


def _unwrap_content(result: Any) -> str:
    """Flatten an MCP tool result into plain text.

    MCP returns a list of typed content blocks, e.g. `[{"type": "text", "text": "..."}]`.
    Handing that raw to the model wraps every payload in a layer of protocol noise it then has
    to parse, so the text is extracted here at the boundary.
    """
    if isinstance(result, str):
        return result
    if isinstance(result, list):
        parts: list[str] = []
        for block in result:
            if isinstance(block, dict) and "text" in block:
                parts.append(str(block["text"]))
            elif hasattr(block, "text"):
                parts.append(str(block.text))
            else:
                parts.append(json.dumps(block, default=str))
        return "\n".join(parts)
    if isinstance(result, dict) and "text" in result:
        return str(result["text"])
    return json.dumps(result, default=str)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class DischargeMCPClient:
    """Manages the MCP server subprocess and exposes its tools and resources."""

    def __init__(self, *, tracer: Any = None, fault: str = "") -> None:
        cfg = get_config()
        self.tracer = tracer
        self.timeout = float(cfg.mcp_timeout_seconds)
        self.server_script = cfg.mcp_server_script
        self.fault = fault
        self._client: Any = None
        self._tools: list[BaseTool] = []
        self._connected = False
        self.last_error = ""

    # -- connection ---------------------------------------------------------

    def _connection(self) -> dict[str, Any]:
        env = dict(os.environ)
        if self.fault:
            # Fault injection is passed to the server process, so the failure happens
            # in the real transport rather than being faked at the client.
            env["DISCHARGE_MCP_FAULT"] = self.fault
        return {
            "discharge": {
                "command": sys.executable,
                "args": [str(self.server_script)],
                "transport": "stdio",
                "env": env,
            }
        }

    def connect(self) -> bool:
        """Start the server and load its tools. Returns False on failure — never raises."""
        if self._connected:
            return True

        from langchain_mcp_adapters.client import MultiServerMCPClient

        try:
            self._client = MultiServerMCPClient(self._connection())
            self._tools = _RUNNER.run(self._client.get_tools(), timeout=self.timeout)
            self._connected = True
            if self.tracer:
                self.tracer.emit(
                    "mcp_connected",
                    server="discharge-planning",
                    transport="stdio",
                    script=str(self.server_script),
                    tools=[t.name for t in self._tools],
                    tool_count=len(self._tools),
                    adapter="langchain-mcp-adapters",
                )
            return True
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self.tracer:
                self.tracer.emit(
                    "mcp_connect_failed", error=self.last_error[:400], fault=self.fault
                )
            return False

    # -- tools --------------------------------------------------------------

    def load_tools(self) -> list[BaseTool]:
        """Return the MCP tools, wrapped so failures are traced and returned as data."""
        if not self.connect():
            return []
        return [self._wrap(tool) for tool in self._tools]

    def _wrap(self, tool: BaseTool) -> BaseTool:
        """Wrap an MCP tool so every invocation is traced and every failure is survivable."""
        tracer = self.tracer
        timeout = self.timeout

        def _invoke(**kwargs: Any) -> str:
            try:
                result = _RUNNER.run(tool.ainvoke(kwargs), timeout=timeout)
                text = _unwrap_content(result)

                # The adapter runs with `handle_tool_errors=True`, so a server-side exception
                # comes back as an ordinary string rather than raising here. Left undetected it
                # would be traced as a success and the self-healing loop would never fire, so
                # the error shape is recognised explicitly.
                if _is_adapter_error(text):
                    message = (
                        f"TOOL ERROR: '{tool.name}' failed upstream. {text} "
                        "Proceed without it and record that the check could not be completed."
                    )
                    if tracer:
                        tracer.tool_call(tool.name, "mcp", kwargs, message, ok=False)
                        tracer.emit(
                            "tool_failure",
                            tool=tool.name,
                            source="mcp",
                            error=text[:300],
                            detected_at="adapter_error_string",
                            recovery="degraded_to_local_context",
                        )
                    return message

                if tracer:
                    tracer.tool_call(tool.name, "mcp", kwargs, text, ok=True)
                return text
            except (TimeoutError, FutureTimeout):
                message = (
                    f"TOOL TIMEOUT: '{tool.name}' did not respond within {timeout:.0f}s. "
                    "The hospital system may be unavailable. Proceed using the case data you "
                    "already have and record that this check could not be completed."
                )
                if tracer:
                    tracer.tool_call(tool.name, "mcp", kwargs, message, ok=False)
                    tracer.emit(
                        "tool_failure",
                        tool=tool.name,
                        source="mcp",
                        error="timeout",
                        timeout_seconds=timeout,
                        recovery="degraded_to_local_context",
                    )
                return message
            except Exception as exc:
                message = (
                    f"TOOL ERROR: '{tool.name}' failed ({type(exc).__name__}: {exc}). "
                    "Proceed without it and record that the check could not be completed."
                )
                if tracer:
                    tracer.tool_call(tool.name, "mcp", kwargs, message, ok=False)
                    tracer.emit(
                        "tool_failure",
                        tool=tool.name,
                        source="mcp",
                        error=str(exc)[:300],
                        recovery="degraded_to_local_context",
                    )
                return message

        # A tool that advertises no argument schema would crash StructuredTool. Falling
        # back to an empty model keeps one malformed tool from taking down the toolbox.
        args_schema = tool.args_schema if tool.args_schema is not None else _NoArgs

        return StructuredTool(
            name=tool.name,
            description=tool.description,
            args_schema=args_schema,
            func=_invoke,
        )

    # -- resources ----------------------------------------------------------

    def read_resource(self, uri: str) -> str:
        """Read an MCP resource by URI (AC-09).

        Resources are addressable content, not actions, so they are fetched directly rather
        than exposed to the model as callable tools.
        """
        if not self.connect():
            return json.dumps({"error": "MCP server unavailable", "detail": self.last_error})
        try:
            blobs = _RUNNER.run(
                self._client.get_resources("discharge", uris=[uri]), timeout=self.timeout
            )
            text = "\n".join(
                b.data if isinstance(getattr(b, "data", None), str) else str(b) for b in blobs
            ).strip()
            if self.tracer:
                self.tracer.emit(
                    "mcp_resource_read", uri=uri, chars=len(text), source="mcp"
                )
            return text
        except Exception as exc:
            message = f"RESOURCE ERROR: could not read '{uri}' ({type(exc).__name__}: {exc})"
            if self.tracer:
                self.tracer.emit("mcp_resource_failed", uri=uri, error=str(exc)[:300])
            return message

    def list_inventory(self) -> dict[str, Any]:
        """Inventory of what the server publishes — the AC-09 evidence artifact.

        MCP lists static resources and *templated* resources separately, so both are
        enumerated here. `discharge://protocol/{condition}` is a template — it would be
        invisible in a plain `resources/list`, and an inventory that missed it would
        under-report what the server actually exposes.
        """
        if not self.connect():
            return {"connected": False, "error": self.last_error}

        static_uris: list[str] = []
        template_uris: list[str] = []
        try:
            async def _enumerate() -> tuple[list[str], list[str]]:
                async with self._client.session("discharge") as session:
                    listed = await session.list_resources()
                    templated = await session.list_resource_templates()
                    return (
                        [str(r.uri) for r in listed.resources],
                        [str(t.uriTemplate) for t in templated.resourceTemplates],
                    )

            static_uris, template_uris = _RUNNER.run(_enumerate(), timeout=self.timeout)
        except Exception as exc:
            if self.tracer:
                self.tracer.emit("mcp_inventory_partial", error=str(exc)[:200])

        return {
            "connected": True,
            "server": "discharge-planning",
            "transport": "stdio",
            "adapter": "langchain-mcp-adapters",
            "tools": [
                {"name": t.name, "description": (t.description or "").split("\n")[0]}
                for t in self._tools
            ],
            "tool_count": len(self._tools),
            "resources": static_uris,
            "resource_templates": template_uris,
            "resource_count": len(static_uris) + len(template_uris),
        }

    def close(self) -> None:
        self._connected = False
        self._client = None
        self._tools = []


def load_mcp_tools(
    tracer: Any = None, fault: str = ""
) -> tuple[list[BaseTool], DischargeMCPClient]:
    """Convenience: build a client and load its tools."""
    client = DischargeMCPClient(tracer=tracer, fault=fault)
    return client.load_tools(), client
