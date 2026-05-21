"""Phoenix MCP client wrapper for Augur.

Spawns the @arizeai/phoenix-mcp server as a subprocess and communicates
over stdio via the Model Context Protocol. Provides high-level methods for
querying traces, projects, and annotations.

Usage:
    async with PhoenixMCPClient() as client:
        tools = await client.list_tools()
        traces = await client.get_traces(project_name="augur", limit=25)
"""

from __future__ import annotations

import json as json_module
import logging
import os
from dataclasses import dataclass
from typing import Any

from mcp import StdioServerParameters
from mcp.client.session import ClientSession

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "https://app.phoenix.arize.com"


# ---------------------------------------------------------------------------
# Async context-manager helpers for stdio_client (duck-typed)
# ---------------------------------------------------------------------------

async def _enter_ctx(ctx_mgr: Any) -> Any:
    """Enter a context manager that may be traditional (has __aenter__)
    or may be the newer stream-tuple-return style.  Returns the value
    yielded by *or* returned from the context manager."""
    if hasattr(ctx_mgr, "__aenter__"):
        return await ctx_mgr.__aenter__()
    # stdio_client used to yield the (read, write) tuple directly
    return ctx_mgr  # type: ignore[return-value]


async def _exit_ctx(
    val: Any,
    exc_type: type[BaseException] | None,
    exc_val: BaseException | None,
    exc_tb: Any,
) -> None:
    """Exit a context manager if it has __aexit__."""
    if hasattr(val, "__aexit__"):
        await val.__aexit__(exc_type, exc_val, exc_tb)


@dataclass
class PhoenixTrace:
    """Simplified trace record extracted from Phoenix MCP response."""

    trace_id: str
    project_name: str
    start_time: str
    end_time: str | None
    # Span-level fields are flattened for agentic consumption
    spans: list[dict[str, Any]]
    # Overall latency in ms
    latency_ms: float = 0.0

    @property
    def agent_reasoning(self) -> str:
        """Extract the agent's reasoning text from span attributes."""
        for span in self.spans:
            attrs = span.get("attributes", {})
            if "llm.output_messages" in attrs:
                # Typical ADK instrumentation stores output here
                msgs = attrs["llm.output_messages"]
                if isinstance(msgs, str):
                    return msgs
                elif isinstance(msgs, list) and msgs:
                    return str(msgs[0])
        return ""

    @property
    def model_input(self) -> str:
        """Extract the model input (prompt) from span attributes."""
        for span in self.spans:
            attrs = span.get("attributes", {})
            if "llm.input_messages" in attrs:
                msgs = attrs["llm.input_messages"]
                if isinstance(msgs, list) and msgs:
                    # Build a readable string from input messages
                    parts = []
                    for m in msgs:
                        role = m.get("role", "[unknown]")
                        content = m.get("content", "")
                        parts.append(f"{role}: {content}")
                    return "\n".join(parts)
                elif isinstance(msgs, str):
                    return msgs
        return ""


class PhoenixMCPClient:
    """Async context manager that wraps the Phoenix MCP server via stdio."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        self.base_url = base_url or os.getenv("PHOENIX_BASE_URL", _DEFAULT_BASE_URL)
        self.api_key = api_key or os.getenv("PHOENIX_API_KEY", "")
        if not self.api_key:
            raise RuntimeError(
                "PhoenixMCPClient requires PHOENIX_API_KEY env var or explicit api_key."
            )

        self._server_params = StdioServerParameters(
            command="npx",
            args=[
                "-y",
                "@arizeai/phoenix-mcp@latest",
                "--baseUrl",
                self.base_url,
                "--apiKey",
                self.api_key,
            ],
        )
        self._session: ClientSession | None = None
        self._stdio_transport = None

    async def __aenter__(self) -> "PhoenixMCPClient":
        from mcp.client.stdio import stdio_client

        logger.info("Starting Phoenix MCP server: npx @arizeai/phoenix-mcp")
        self._stdio_transport = await _enter_ctx(stdio_client(self._server_params))
        read_stream, write_stream = self._stdio_transport
        self._session = ClientSession(read_stream, write_stream)
        await self._session.initialize()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: Any = None,
    ) -> None:
        if self._stdio_transport is not None:
            await _exit_ctx(self._stdio_transport, exc_type, exc_val, exc_tb)
        self._session = None

    async def _call_tool(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        """Call an MCP tool and return parsed results."""
        if self._session is None:
            raise RuntimeError("MCP session not initialized. Use async with.")

        result = await self._session.call_tool(tool_name, arguments=arguments)
        # The MCP result.content may contain text fragments
        texts: list[str] = []
        for item in result.content:
            if hasattr(item, "text"):
                texts.append(item.text)
        raw = "".join(texts).strip()
        if not raw:
            return {}
        try:
            return json_module.loads(raw)
        except json_module.JSONDecodeError:
            return {"raw": raw}

    # ------------------------------------------------------------------
    # High-level wrappers
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[str]:
        """Return the names of available tools on the Phoenix MCP server."""
        if self._session is None:
            raise RuntimeError("MCP session not initialized.")
        tools = await self._session.list_tools()
        return [t.name for t in tools.tools] if tools and hasattr(tools, "tools") else []

    async def get_traces(
        self,
        project_name: str = "augur",
        limit: int = 25,
        start_time: str | None = None,
        end_time: str | None = None,
    ) -> list[PhoenixTrace]:
        """Query Phoenix for traces in a project.

        Args:
            project_name: Phoenix project name (default "augur").
            limit: Maximum traces to return.
            start_time: ISO-8601 string for trace start window (inclusive).
            end_time: ISO-8601 string for trace end window (inclusive).

        Returns:
            List of PhoenixTrace records.
        """
        args: dict[str, Any] = {"projectName": project_name, "limit": limit}
        if start_time:
            args["startTime"] = start_time
        if end_time:
            args["endTime"] = end_time

        data = await self._call_tool("get-traces", args)
        return self._parse_traces(data)

    async def get_spans(
        self,
        trace_id: str,
        project_name: str = "augur",
    ) -> list[dict[str, Any]]:
        """Query Phoenix for spans within a specific trace."""
        data = await self._call_tool(
            "get-spans",
            {"traceId": trace_id, "projectName": project_name},
        )
        return data if isinstance(data, list) else data.get("spans", [])

    async def get_trace_by_id(
        self,
        trace_id: str,
        project_name: str = "augur",
    ) -> PhoenixTrace | None:
        """Fetch a single trace by ID. Returns None if not found."""
        data = await self._call_tool(
            "get-traces",
            {"traceIds": [trace_id], "projectName": project_name, "limit": 1},
        )
        traces = self._parse_traces(data)
        return traces[0] if traces else None

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_traces(raw: Any) -> list[PhoenixTrace]:
        """Parse raw MCP response into PhoenixTrace objects."""
        traces = []
        if isinstance(raw, list):
            items = raw
        elif isinstance(raw, dict):
            items = raw.get("traces", raw.get("items", [raw]))
        else:
            items = []

        for item in items:
            if isinstance(item, PhoenixTrace):
                traces.append(item)
                continue
            spans = item.get("spans", item.get("spans", [])) if isinstance(item, dict) else []
            traces.append(
                PhoenixTrace(
                    trace_id=item.get("id", item.get("traceId", ""))
                    if isinstance(item, dict)
                    else str(item),
                    project_name=item.get("projectName", "augur") if isinstance(item, dict) else "augur",
                    start_time=item.get("startTime", "") if isinstance(item, dict) else "",
                    end_time=item.get("endTime", None) if isinstance(item, dict) else None,
                    spans=spans,
                    latency_ms=item.get("latencyMs", 0.0) if isinstance(item, dict) else 0.0,
                )
            )
        return traces
