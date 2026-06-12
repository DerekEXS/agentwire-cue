"""v1.5.1 P1 RED tests: observability emit() wired into statechart.

Contract:
- ``_transition_locked`` issues ``cue.guard.evaluated`` once per matched
  transition that has a guard. Result is the truthiness of the guard.
- ``_dispatch_action`` issues ``cue.action.executed`` per action (skipping
  send_a2a, which has its own emit downstream).
- ``cue.error`` is emitted from the guard eval exception path.

All events should pick up whatever ``trace_id`` is currently active on
the context (set by the caller, e.g. admin handler).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from agentwire_cue.core import observability
from agentwire_cue.core.statechart import Event, StatechartEngine


def _read_events(caplog) -> list[dict]:
    return [
        json.loads(rec.message)
        for rec in caplog.records
        if rec.name == "agentwire_cue.events"
    ]


def _make_plugin(spec: dict) -> SimpleNamespace:
    return SimpleNamespace(
        name="t-plugin",
        version="1.0.0",
        meta={"name": "t-plugin", "version": "1.0.0"},
        spec=spec,
        resolved_persist_path=None,
        peers={},
    )


@pytest.mark.asyncio
async def test_guard_true_emits_guard_evaluated(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")
    plugin = _make_plugin({
        "statechart": {
            "initial": "s0",
            "states": {
                "s0": {"on": {"E": [{"target": "s1", "guard": "1 == 1"}]}},
                "s1": {},
            },
        }
    })
    eng = StatechartEngine(plugin)

    observability.set_trace_id("trace-X")
    try:
        result = await eng.transition(Event(type="E", payload={}))
    finally:
        observability.reset_trace_id()

    assert result.OK is True
    events = _read_events(caplog)
    guards = [e for e in events if e["event"] == "cue.guard.evaluated"]
    assert guards, f"expected cue.guard.evaluated, got {events}"
    assert guards[0]["plugin"] == "t-plugin"
    assert guards[0]["result"] is True
    assert guards[0]["guard_expression"] == "1 == 1"
    assert guards[0]["trace_id"] == "trace-X"


@pytest.mark.asyncio
async def test_guard_false_emits_guard_evaluated_with_reason(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")
    plugin = _make_plugin({
        "statechart": {
            "initial": "s0",
            "states": {
                "s0": {"on": {"E": [{"target": "s1", "guard": "1 == 2"}]}},
                "s1": {},
            },
        }
    })
    eng = StatechartEngine(plugin)
    result = await eng.transition(Event(type="E", payload={}))

    assert result.OK is False
    events = _read_events(caplog)
    guards = [e for e in events if e["event"] == "cue.guard.evaluated"]
    assert guards
    assert guards[0]["result"] is False
    assert guards[0]["reason"] == "guard_false"


@pytest.mark.asyncio
async def test_action_log_emits_action_executed(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")
    plugin = _make_plugin({
        "statechart": {
            "initial": "s0",
            "states": {
                "s0": {
                    "on": {
                        "E": [{
                            "target": "s0",
                            "actions": [
                                {"type": "log", "with": {"level": "info", "message": "hi"}},
                            ],
                        }],
                    },
                },
            },
        }
    })
    eng = StatechartEngine(plugin)
    await eng.transition(Event(type="E", payload={}))

    events = _read_events(caplog)
    acts = [e for e in events if e["event"] == "cue.action.executed"]
    assert acts, f"expected cue.action.executed, got {events}"
    assert acts[0]["action_type"] == "log"


@pytest.mark.asyncio
async def test_send_a2a_action_emits_action_executed_with_target(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")
    captured = []

    async def fake_send(peer, text, metadata=None):
        captured.append((peer, text, metadata))

    plugin = _make_plugin({
        "statechart": {
            "initial": "s0",
            "states": {
                "s0": {
                    "on": {
                        "E": [{
                            "target": "s0",
                            "actions": [
                                {"type": "send_a2a",
                                 "with": {"peer": "main", "message": {"type": "A2A_MESSAGE", "text": "hello"}}},
                            ],
                        }],
                    },
                },
            },
        }
    })
    eng = StatechartEngine(plugin, a2a_send=fake_send)
    await eng.transition(Event(type="E", payload={}))

    events = _read_events(caplog)
    acts = [e for e in events if e["event"] == "cue.action.executed"]
    assert any(a["action_type"] == "send_a2a" and a["target_peer"] == "main" for a in acts)


@pytest.mark.asyncio
async def test_guard_eval_error_emits_cue_error(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")
    plugin = _make_plugin({
        "statechart": {
            "initial": "s0",
            "states": {
                "s0": {"on": {"E": [{"target": "s1", "guard": "doesnt.exist()"}]}},
                "s1": {},
            },
        }
    })
    eng = StatechartEngine(plugin)
    await eng.transition(Event(type="E", payload={}))

    events = _read_events(caplog)
    errors = [e for e in events if e["event"] == "cue.error"]
    assert errors, f"expected cue.error, got {events}"
    assert errors[0]["plugin"] == "t-plugin"
    assert "guard" in errors[0].get("error_type", "")
