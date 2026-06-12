"""AgentWire-Cue v1.5.1 observability: structured event logging.

Minimal stdlib-only module that emits key event records as JSON lines and
exposes a trace_id generator. Replaces ad-hoc log.info calls for the
trigger → guard → action → send_a2a flow. Side-effect free at import
time; configure once at host startup.

A future v1.5.3 release may replace this with `structlog` if/when the
dependency is acceptable; the public API here is intentionally compatible
(each `emit(event, **fields)` records an event with a `trace_id`).
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

_current_trace_id: ContextVar[str | None] = ContextVar("cue_trace_id", default=None)

_EVENTS_LOGGER = logging.getLogger("agentwire_cue.events")


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_id(trace_id: str) -> None:
    _current_trace_id.set(trace_id)


def get_trace_id() -> str | None:
    return _current_trace_id.get()


def reset_trace_id() -> None:
    _current_trace_id.set(None)


def emit(event: str, **fields: Any) -> None:
    """Emit a structured event log line. Always includes event, ts, trace_id."""
    record = {
        "event": event,
        "ts": time.time(),
        "trace_id": get_trace_id(),
        **fields,
    }
    _EVENTS_LOGGER.info(json.dumps(record, ensure_ascii=False, default=str))


def install_json_log_sink(stream=None) -> None:
    """Mirror all structured events to a JSON-only log file (default stdout).

    Existing human-readable loggers stay unchanged.
    """
    sink = stream or sys.stdout
    handler = logging.StreamHandler(sink)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(lambda record: record.name == _EVENTS_LOGGER.name)
    _EVENTS_LOGGER.addHandler(handler)
    _EVENTS_LOGGER.propagate = False
