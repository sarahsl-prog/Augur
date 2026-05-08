"""Shared pytest fixtures for the Augur test suite."""

import os

import pytest


@pytest.fixture(autouse=True)
def _disable_phoenix_in_tests(monkeypatch):
    """Prevent unit tests from emitting Phoenix traces over the network.

    Tests that explicitly want tracing (the manual smoke test in Task 6)
    bypass this by spawning a separate process with PHOENIX_API_KEY set.
    """
    monkeypatch.setenv("AUGUR_TRACING_DISABLED", "1")
