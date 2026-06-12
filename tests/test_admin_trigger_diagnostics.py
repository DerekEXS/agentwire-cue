from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from agentwire_cue.core.admin_api import handle_trigger
from agentwire_cue.core.statechart import TransitionResult


class _BodyRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_admin_trigger_response_includes_reason_and_details(monkeypatch, caplog):
    class Statechart:
        current_state = "watching"

        async def transition(self, event):
            return TransitionResult.no_match(
                "peer_not_found",
                {"peer": "NonExistent"},
            )

    plugin = SimpleNamespace(
        spec={"statechart": {"states": {"watching": {"on": {"history_change": {}}}}}},
        statechart=Statechart(),
    )
    host = SimpleNamespace(plugins={"owner-alert": plugin})
    request = make_mocked_request(
        "POST",
        "/plugins/owner-alert/trigger",
        app={"host": host, "admin_token": "secret"},
        headers={"Authorization": "Bearer secret"},
        match_info={"name": "owner-alert"},
    )
    request.json = _BodyRequest({"type": "history_change"}).json

    response = await handle_trigger(request)

    assert response.status == 200
    assert response.text is not None
    assert '"matched": false' in response.text
    assert '"reason": "peer_not_found"' in response.text
    assert '"peer": "NonExistent"' in response.text
