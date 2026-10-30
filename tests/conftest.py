"""Force the entire pytest process to stay offline and use deterministic LLMs."""

from __future__ import annotations

import os
import socket

import pytest


def _force_mock_environment() -> None:
    os.environ["LLM_MODE"] = "mock"
    os.environ.pop("LLM_API_KEY", None)
    os.environ.pop("NEBIUS_API_KEY", None)


_force_mock_environment()


def _disable_dotenv(*args, **kwargs):
    """Do not allow dotenv helpers or plugins to populate test settings."""
    return False


try:
    import dotenv
    import dotenv.main

    dotenv.load_dotenv = _disable_dotenv
    dotenv.main.load_dotenv = _disable_dotenv
except ImportError:
    pass


@pytest.hookimpl(tryfirst=True)
def pytest_sessionstart(session) -> None:
    # Reassert after pytest plugins have initialized, in case one loaded a .env.
    _force_mock_environment()


@pytest.fixture(autouse=True)
def enforce_mock_and_block_network(monkeypatch):
    _force_mock_environment()

    from app import llm

    real_sdk = llm.AsyncOpenAI
    original_factory = llm._client_for_live

    def reject_unmocked_live_client():
        if llm.AsyncOpenAI is not real_sdk:
            # Unit tests may replace the SDK with a fake client; socket calls stay blocked.
            return original_factory()
        raise AssertionError("Tests must not make a non-mock LLM request.")

    def reject_network(*args, **kwargs):
        raise AssertionError("Network access is disabled during tests.")

    monkeypatch.setattr(llm, "_client_for_live", reject_unmocked_live_client)
    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket.socket, "connect_ex", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)

