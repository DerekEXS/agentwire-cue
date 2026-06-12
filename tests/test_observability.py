"""v1.5.1 P1 RED tests for observability module.

Contract:
- emit(event, **fields) writes one JSON line per call into the
  ``agentwire_cue.events`` logger.
- The JSON record always contains ``event``, ``ts``, ``trace_id`` keys.
- ``trace_id`` reflects whatever has been stored via ``set_trace_id`` on
  the current asyncio task / contextvar (None when unset).
- The trace_id is unique per ``new_trace_id()`` call.
"""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from agentwire_cue.core import observability


def _read_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        json.loads(rec.message)
        for rec in caplog.records
        if rec.name == "agentwire_cue.events"
    ]


def test_emit_writes_event_with_required_keys(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")
    observability.reset_trace_id()

    observability.emit("cue.trigger.received", plugin="owner-alert", trigger_id="on-pawly-urgent")

    events = _read_events(caplog)
    assert len(events) == 1
    rec = events[0]
    assert rec["event"] == "cue.trigger.received"
    assert "ts" in rec
    assert isinstance(rec["ts"], (int, float))
    assert "trace_id" in rec  # None when unset
    assert rec["plugin"] == "owner-alert"
    assert rec["trigger_id"] == "on-pawly-urgent"


def test_emit_includes_active_trace_id(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.events")
    observability.reset_trace_id()
    tid = observability.new_trace_id()
    observability.set_trace_id(tid)
    try:
        observability.emit("cue.guard.evaluated", plugin="owner-alert", result=True)
    finally:
        observability.reset_trace_id()

    events = _read_events(caplog)
    assert events[-1]["trace_id"] == tid


def test_new_trace_id_is_unique():
    ids = {observability.new_trace_id() for _ in range(64)}
    assert len(ids) == 64


def test_trace_id_isolated_across_async_tasks():
    """contextvars must keep trace_id isolated between concurrent tasks."""
    observability.reset_trace_id()

    async def runner(tid: str) -> str | None:
        observability.set_trace_id(tid)
        await asyncio.sleep(0)
        return observability.get_trace_id()

    async def main() -> tuple[str | None, str | None]:
        a, b = await asyncio.gather(runner("AAA"), runner("BBB"))
        return a, b

    a, b = asyncio.run(main())
    assert a == "AAA"
    assert b == "BBB"
    assert observability.get_trace_id() is None
