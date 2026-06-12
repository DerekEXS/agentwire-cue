"""v1.5.1 P1 RED tests: observability emit() wired into admin trigger path.

Contract:
- ``handle_trigger`` issues ``cue.trigger.received`` at entry, including
  plugin, trigger event_type, and the resolved peer (after injection).
- After ``transition`` returns, it issues ``cue.trigger.evaluated`` with
  ``matched`` and optional ``reason``.
- All events share the same ``trace_id`` and that ``trace_id`` is also
  present in the JSON response (``trace_id`` field).
"""
from __future__ import annotations

import json
import logging
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


def _read_events(caplog) -> list[dict]:
    return [
        json.loads(rec.message)
        for rec in caplog.records
        if rec.name == "agentwire_cue.events"
    ]


@pytest.mark.asyncio
async def test_admin_trigger_emits_received_and_evaluated_events(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")

    class Statechart:
        current_state = "watching"

        async def transition(self, event):
            return TransitionResult.matched("watching")

    plugin = SimpleNamespace(
        name="owner-alert",
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
    body = json.loads(response.text)

    events = _read_events(caplog)
    received = [e for e in events if e["event"] == "cue.trigger.received"]
    evaluated = [e for e in events if e["event"] == "cue.trigger.evaluated"]
    assert received, f"expected cue.trigger.received in events, got {events}"
    assert evaluated, f"expected cue.trigger.evaluated in events, got {events}"

    assert received[0]["plugin"] == "owner-alert"
    assert received[0]["event_type"] == "history_change"
    assert received[0]["peer"] == "Pawly"

    assert evaluated[0]["matched"] is True
    assert evaluated[0]["plugin"] == "owner-alert"

    # trace_id present and shared across both events
    trace_id = received[0]["trace_id"]
    assert trace_id is not None
    assert evaluated[0]["trace_id"] == trace_id
    assert body.get("trace_id") == trace_id


@pytest.mark.asyncio
async def test_admin_trigger_emits_unmatched_event_with_reason(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")

    class Statechart:
        current_state = "watching"

        async def transition(self, event):
            return TransitionResult.no_match("guard_false", {"guard_expression": "x==1"})

    plugin = SimpleNamespace(
        name="owner-alert",
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
    request.json = _BodyRequest({"type": "history_change"}).json

    await handle_trigger(request)

    events = _read_events(caplog)
    evaluated = [e for e in events if e["event"] == "cue.trigger.evaluated"]
    assert evaluated
    assert evaluated[0]["matched"] is False
    assert evaluated[0]["reason"] == "guard_false"
