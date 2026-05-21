"""Tests for the Phoenix MCP client wrapper.

Uses a mock stdio client so no actual npx / Phoenix server is required."""

import json as json_module
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from augur.phoenix_mcp_client import PhoenixMCPClient, PhoenixTrace


@pytest.fixture
def mock_read_write():
    """Fake async-read / async-write streams for the stdio client."""
    read_stream = AsyncMock()
    write_stream = AsyncMock()
    return read_stream, write_stream


@pytest.fixture
def mock_session(mock_read_write):
    """A mocked MCP ClientSession with a fake tool list."""
    read, write = mock_read_write
    session = MagicMock()
    session.initialize = AsyncMock()
    session.close = AsyncMock()
    tool1, tool2, tool3 = MagicMock(), MagicMock(), MagicMock()
    tool1.name = "get-traces"
    tool2.name = "get-spans"
    tool3.name = "get-projects"
    session.list_tools = AsyncMock(return_value=MagicMock(tools=[tool1, tool2, tool3]))
    return session


@pytest.fixture
def mock_stdio_client(mock_read_write):
    """Patch mcp.client.stdio.stdio_client to yield our mock streams."""
    read, write = mock_read_write

    @asynccontextmanager
    async def _stub(params):
        yield (read, write)

    with patch("mcp.client.stdio.stdio_client", side_effect=_stub):
        yield


@pytest.fixture
def mock_client_session(mock_session):
    """Patch ClientSession instantiation inside augur.phoenix_mcp_client."""
    with patch("augur.phoenix_mcp_client.ClientSession", return_value=mock_session):
        yield


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

def test_client_requires_api_key(monkeypatch):
    """Must raise when PHOENIX_API_KEY and explicit key are both absent."""
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="PHOENIX_API_KEY"):
        PhoenixMCPClient()


def test_client_accepts_explicit_key(monkeypatch):
    """Explicit api_key should bypass env-var check."""
    monkeypatch.delenv("PHOENIX_API_KEY", raising=False)
    client = PhoenixMCPClient(api_key="test-key")
    assert client.api_key == "test-key"


# ---------------------------------------------------------------------------
# list_tools
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_tools(mock_stdio_client, mock_client_session):
    """list_tools should return tool names after init."""
    client = PhoenixMCPClient(api_key="fake")
    async with client:
        tools = await client.list_tools()

    assert "get-traces" in tools
    assert "get-spans" in tools
    assert "get-projects" in tools


# ---------------------------------------------------------------------------
# get_traces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_traces(mock_stdio_client, mock_client_session):
    """get_traces should parse the MCP JSON response into PhoenixTrace objects."""
    client = PhoenixMCPClient(api_key="fake")

    # Simulate an MCP tool response: two traces with text content
    fake_traces = {
        "traces": [
            {
                "id": "t-001",
                "projectName": "augur",
                "startTime": "2026-05-20T12:00:00Z",
                "endTime": "2026-05-20T12:00:05Z",
                "spans": [
                    {"attributes": {"disposition": "True Positive - Critical"}}
                ],
            },
            {
                "id": "t-002",
                "projectName": "augur",
                "startTime": "2026-05-20T12:01:00Z",
                "spans": [],
            },
        ]
    }
    tool_result = MagicMock()
    tool_result.content = [MagicMock(text=json_module.dumps(fake_traces))]

    # Wire the mock session's call_tool to return our fake result
    # Need fresh mock_session per test, so re-patch ClientSession here
    with patch("augur.phoenix_mcp_client.ClientSession") as MockSession:
        session = MagicMock()
        session.initialize = AsyncMock()
        session.call_tool = AsyncMock(return_value=tool_result)
        MockSession.return_value = session
        async with client:
            traces = await client.get_traces(project_name="augur", limit=10)

    assert len(traces) == 2
    assert traces[0].trace_id == "t-001"
    assert traces[0].spans[0]["attributes"]["disposition"] == "True Positive - Critical"
    assert traces[1].trace_id == "t-002"


# ---------------------------------------------------------------------------
# PhoenixTrace helpers
# ---------------------------------------------------------------------------

def test_phoenix_trace_agent_reasoning_empty():
    """agent_reasoning should return empty string when no output spans."""
    trace = PhoenixTrace(trace_id="t-1", project_name="augur", start_time="", end_time=None, spans=[])
    assert trace.agent_reasoning == ""


def test_phoenix_trace_model_input_from_messages():
    """model_input should concatenate input message roles and contents."""
    trace = PhoenixTrace(
        trace_id="t-1",
        project_name="augur",
        start_time="",
        end_time=None,
        spans=[
            {
                "attributes": {
                    "llm.input_messages": [
                        {"role": "system", "content": "You are a triage agent."},
                        {"role": "user", "content": '{"src_ip": "10.0.0.1"}'},
                    ]
                }
            }
        ],
    )
    text = trace.model_input
    assert "system: You are a triage agent." in text
    assert 'user: {"src_ip": "10.0.0.1"}' in text
