"""/chat and /agent refuse a body without usable text with a 422, before any
client is made (found by bug-sweep-2). Synthetic targets only: no engine, no
model, no network."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from service import main
from service.config.endpoints import Endpoint, Target


def _remote_target():
    return Target("agent", Endpoint("example_cloud", "https://cloud.example.invalid/v1", "none",
                                     managed=False), "example-model")


@pytest.mark.parametrize("body", [{}, {"prompt": None}, {"prompt": 7}, {"messages": []}])
def test_chat_without_messages_or_prompt_is_422_and_makes_no_client(monkeypatch, body):
    monkeypatch.setattr(main, "role_target", lambda role: _remote_target())
    monkeypatch.setattr(main, "remote_only_profile", lambda: None)
    made = []
    monkeypatch.setattr(main, "OMLXClient", lambda **kwargs: made.append(kwargs))
    with pytest.raises(HTTPException) as caught:
        asyncio.run(main.chat(body))
    assert caught.value.status_code == 422
    assert made == [], "no client is created (or leaked) for a body that can't be answered"


@pytest.mark.parametrize("body", [{}, {"prompt": None}, {"prompt": ["a", "b"]}])
def test_agent_without_a_text_prompt_is_422(body):
    with pytest.raises(HTTPException) as caught:
        asyncio.run(main.agent(body))
    assert caught.value.status_code == 422
