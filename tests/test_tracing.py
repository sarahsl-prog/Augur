"""Tests for the tracing module."""

import os
from unittest.mock import MagicMock, patch

from augur.tracing import _NoOpSpan, trace_span


class TestNoOpSpan:
    def test_trace_id_is_empty_string(self):
        span = _NoOpSpan()
        assert span.trace_id == ""

    def test_set_attribute_is_noop(self):
        span = _NoOpSpan()
        span.set_attribute("key", "value")

    def test_is_recording_returns_false(self):
        span = _NoOpSpan()
        assert span.is_recording() is False

    def test_context_manager(self):
        span = _NoOpSpan()
        with span as s:
            assert s is span


class TestTraceSpan:
    def test_yields_noop_when_disabled(self, monkeypatch):
        monkeypatch.setenv("AUGUR_TRACING_DISABLED", "1")
        with trace_span("test.span", foo="bar") as span:
            assert isinstance(span, _NoOpSpan)
            assert span.trace_id == ""

    def test_yields_noop_when_not_initialized(self, monkeypatch):
        monkeypatch.delenv("AUGUR_TRACING_DISABLED", raising=False)
        import augur.tracing
        original = augur.tracing._initialized
        augur.tracing._initialized = False
        try:
            with trace_span("test.span") as span:
                assert isinstance(span, _NoOpSpan)
        finally:
            augur.tracing._initialized = original
