"""Tests for triage agent helpers."""

from augur.agents.triage import _parse_agent_response, build_triage_agent


class TestParseAgentResponse:
    def test_plain_json(self):
        raw = '{"disposition": "False Positive"}'
        result = _parse_agent_response(raw)
        assert result["disposition"] == "False Positive"

    def test_json_with_markdown_fence(self):
        raw = "```json\n{\"disposition\": \"True Positive - Critical\"}\n```"
        result = _parse_agent_response(raw)
        assert result["disposition"] == "True Positive - Critical"

    def test_json_with_generic_fence(self):
        raw = "```\n{\"disposition\": \"Benign Positive\"}\n```"
        result = _parse_agent_response(raw)
        assert result["disposition"] == "Benign Positive"


class TestBuildTriageAgent:
    def test_returns_agent_instance(self):
        agent = build_triage_agent()
        assert agent is not None
        from google.adk.agents import Agent
        assert isinstance(agent, Agent)
