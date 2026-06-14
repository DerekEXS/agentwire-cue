"""v1.4.8 RED: A2AClient send_message honors the peer alias table.

Goals:
- target_peer is an alias known to the alias table → use the configured url
  directly, no peer card fetch required.
- target_peer is not in the alias table → fall back to peer cache (legacy).
- target_peer == "self" still routes to own a2a_url (no change).
- The HTTP payload includes message.metadata when supplied via send_message.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agentwire_cue.core.a2a_client import A2AClient, SendResult


def _make_client(aliases=None):
    client = A2AClient(
        a2a_url="http://127.0.0.1:18800",
        a2a_token="dummy",
        peer_cache=None,
    )
    if aliases is not None:
        client.set_aliases(aliases)
    return client


@pytest.mark.asyncio
async def test_send_message_uses_alias_url_directly():
    client = _make_client(aliases={"Pawly": {"uuid": "u1", "url": "http://pawly.example.invalid:18800"}})
    captured = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakePost:
        def __init__(self, url, json, headers=None):
            captured["url"] = url
            captured["payload"] = json

        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *exc):
            return False

    class _FakeSession:
        def post(self, url, json, headers=None):
            return _FakePost(url, json, headers=headers)

    client._session = _FakeSession()

    result = await client.send_message("Pawly", {"text": "hi"})

    assert result == SendResult.SUCCESS
    assert captured["url"] == "http://pawly.example.invalid:18800/a2a/jsonrpc"
    assert captured["payload"]["method"] == "message/send"
    assert captured["payload"]["params"]["message"] == {
        "role": "user",
        "parts": [{"type": "text", "text": "hi"}],
    }


@pytest.mark.asyncio
async def test_send_message_metadata_passed_through_when_supplied():
    client = _make_client(aliases={"Pawly": {"uuid": "u1", "url": "http://pawly.example.invalid:18800"}})
    captured = {}

    class _FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class _FakePost:
        def __init__(self, url, json, headers=None):
            captured["payload"] = json

        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *exc):
            return False

    class _FakeSession:
        def post(self, url, json, headers=None):
            return _FakePost(url, json, headers=headers)

    client._session = _FakeSession()
    metadata = {"workflow_pointer": {"workflow_file": "wf.yaml", "current_step": "step_5"}}

    await client.send_message("Pawly", {"text": "hi"}, metadata=metadata)

    assert captured["payload"]["params"]["message"]["metadata"] == metadata


@pytest.mark.asyncio
async def test_send_message_without_alias_falls_back_to_peer_cache():
    client = _make_client(aliases={"Pawly": {"uuid": "u1", "url": "http://pawly.example.invalid:18800"}})
    # No cache, no alias for "Unknown" — should return FAILED, not crash
    result = await client.send_message("Unknown", {"text": "hi"})
    assert result == SendResult.FAILED


def test_send_message_signature_accepts_metadata_kwarg():
    """v1.4.8: send_message signature is keyword-friendly for metadata."""
    import inspect
    sig = inspect.signature(A2AClient.send_message)
    assert "metadata" in sig.parameters
    # Default is None (backward compatibility)
    assert sig.parameters["metadata"].default is None
