from __future__ import annotations

import pytest
from aiohttp.test_utils import make_mocked_request

from agentwire_cue.core.a2a_client import A2AClient, A2AListener


@pytest.mark.asyncio
async def test_send_message_normalizes_text_to_a2a_parts():
    client = A2AClient("http://127.0.0.1:18800", a2a_token="dummy")
    client.set_aliases({"main": {"uuid": "main-demo-uuid", "url": "http://127.0.0.1:18800"}})
    captured = {}

    class FakeResponse:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *exc): return False

    class FakePost:
        def __init__(self, url, json):
            captured["payload"] = json
        async def __aenter__(self): return FakeResponse()
        async def __aexit__(self, *exc): return False

    class FakeSession:
        def post(self, url, json): return FakePost(url, json)

    client._session = FakeSession()

    await client.send_message(
        "main",
        {"text": "hello"},
        metadata={"source_peer": "Pawly"},
    )

    message = captured["payload"]["params"]["message"]
    assert message["role"] == "user"
    assert message["parts"] == [{"type": "text", "text": "hello"}]
    assert message["metadata"] == {"source_peer": "Pawly"}


@pytest.mark.asyncio
async def test_agent_card_reports_current_version():
    listener = A2AListener(host="127.0.0.1", port=18801)
    listener.set_plugins_info([])
    request = make_mocked_request("GET", "/.well-known/agent.json")

    response = await listener._handle_agent_card(request)

    assert response.status == 200
    assert '"version": "1.5.5"' in response.text
