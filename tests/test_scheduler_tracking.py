"""v1.5.3 RED: scheduler-fired transitions update Plugin bookkeeping.

The v1.5.1 admin trigger handler writes ``last_trigger_at`` etc. so
``/admin/status`` can answer "did the last fire match?". Scheduler-fired
triggers (cron, history_change, a2a_message) take a different code path
and previously bypassed that bookkeeping, so an automatic fire never
surfaced on the diagnostics endpoint.

v1.5.3 adds a small helper that the scheduler triggers + the admin
handler both call. This test pins the helper contract.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from agentwire_cue.core import observability
from agentwire_cue.core.statechart import (
    Event,
    TransitionResult,
    run_tracked_transition,
)


def _make_plugin(*, name="owner-alert", current_state="watching"):
    return SimpleNamespace(
        name=name,
        statechart=SimpleNamespace(current_state=current_state),
        last_trigger_at=None,
        last_match=None,
        last_reason=None,
        last_details=None,
    )


@pytest.mark.asyncio
async def test_run_tracked_transition_updates_bookkeeping_on_match():
    plugin = _make_plugin()

    async def transition(event):
        return TransitionResult.matched("watching")

    plugin.statechart.transition = transition

    result = await run_tracked_transition(
        plugin, Event(type="history_change", payload={"peer": "Pawly"}),
        source="scheduler",
    )

    assert result.OK is True
    assert plugin.last_trigger_at is not None and plugin.last_trigger_at > 0
    assert plugin.last_match is True
    assert plugin.last_reason is None


@pytest.mark.asyncio
async def test_run_tracked_transition_records_reason_on_miss():
    plugin = _make_plugin()

    async def transition(event):
        return TransitionResult.no_match("guard_false", {"why": "x"})

    plugin.statechart.transition = transition

    result = await run_tracked_transition(
        plugin, Event(type="history_change", payload={}),
        source="scheduler",
    )

    assert result.OK is False
    assert plugin.last_match is False
    assert plugin.last_reason == "guard_false"
    assert plugin.last_details == {"why": "x"}


@pytest.mark.asyncio
async def test_run_tracked_transition_emits_trigger_events(caplog):
    import json
    import logging
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")
    plugin = _make_plugin()

    async def transition(event):
        return TransitionResult.matched("watching")
    plugin.statechart.transition = transition

    await run_tracked_transition(
        plugin, Event(type="history_change", payload={"peer": "Pawly"}),
        source="scheduler",
    )

    events = [
        json.loads(r.message) for r in caplog.records
        if r.name == "agentwire_cue.events"
    ]
    received = [e for e in events if e["event"] == "cue.trigger.received"]
    evaluated = [e for e in events if e["event"] == "cue.trigger.evaluated"]
    assert received and evaluated
    assert received[0]["source"] == "scheduler"
    assert received[0]["peer"] == "Pawly"
    # trace_id is shared and non-null
    tid = received[0]["trace_id"]
    assert tid

@pytest.mark.asyncio
async def test_run_tracked_transition_invalidates_history_cache_before_transition():
    history_client = SimpleNamespace(invalidated=False)

    def invalidate():
        history_client.invalidated = True

    history_client.invalidate = invalidate
    plugin = _make_plugin()
    plugin.statechart.history_client = history_client

    async def transition(event):
        assert history_client.invalidated is True
        return TransitionResult.matched("watching")

    plugin.statechart.transition = transition

    result = await run_tracked_transition(
        plugin, Event(type="history_change", payload={"peer": "Pawly"}),
        source="admin_api",
    )

    assert result.OK is True
