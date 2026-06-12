from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import make_mocked_request

from agentwire_cue.core.admin_api import handle_trigger
from agentwire_cue.core.statechart import TransitionResult
from agentwire_cue.core.types import Trigger


class _BodyRequest:
    def __init__(self, body: dict):
        self._body = body

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_admin_history_change_trigger_injects_peer_from_trigger_config():
    captured = {}

    class Statechart:
        current_state = "watching"

        async def transition(self, event):
            captured["payload"] = event.payload
            return TransitionResult.matched("watching")

    plugin = SimpleNamespace(
        spec={"statechart": {"states": {"watching": {"on": {"history_change": {}}}}}},
        triggers=[Trigger(id="on-pawly-urgent", type="history_change", config={"peer": "Pawly"})],
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
    request.json = _BodyRequest({"type": "history_change", "payload": {"new_round": 2}}).json

    response = await handle_trigger(request)

    assert response.status == 200
    assert json.loads(response.text)["matched"] is True
    assert captured["payload"]["peer"] == "Pawly"
    assert captured["payload"]["new_round"] == 2


@pytest.mark.asyncio
async def test_admin_history_change_payload_peer_wins_over_trigger_config():
    captured = {}

    class Statechart:
        current_state = "watching"

        async def transition(self, event):
            captured["payload"] = event.payload
            return TransitionResult.matched("watching")

    plugin = SimpleNamespace(
        spec={"statechart": {"states": {"watching": {"on": {"history_change": {}}}}}},
        triggers=[Trigger(id="on-pawly-urgent", type="history_change", config={"peer": "Pawly"})],
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
    request.json = _BodyRequest({"type": "history_change", "payload": {"peer": "初梦", "new_round": 2}}).json

    response = await handle_trigger(request)

    assert response.status == 200
    assert captured["payload"]["peer"] == "初梦"
