import asyncio
import os
import socket
from dataclasses import replace

import pytest

from app import llm


def test_pytest_forces_mock_mode_removes_keys_and_blocks_live_or_network(monkeypatch):
    assert os.environ.get("LLM_MODE") == "mock"
    assert "LLM_API_KEY" not in os.environ
    assert "NEBIUS_API_KEY" not in os.environ

    monkeypatch.setattr(llm, "settings", replace(llm.settings, mode="live", api_key="fake-key"))
    with pytest.raises(AssertionError, match="non-mock LLM request"):
        asyncio.run(llm.call("reduce", [{"role": "user", "content": "test"}]))

    with pytest.raises(AssertionError, match="Network access is disabled"):
        socket.create_connection(("127.0.0.1", 9))
