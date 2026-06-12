"""AgentWire-Cue v1.5.3 observability: structured event logging.

Two code paths share one public API:

- When ``structlog`` is importable (the Docker image installs it),
  ``emit`` uses ``structlog.get_logger("agentwire_cue.events")`` with a
  JSONRenderer processor configured below. The stdlib ``logging``
  handler underneath is what test harnesses + ``install_json_log_sink``
  capture, so the message body stays one JSON line per event.

- When structlog is not installed (the system Python the CUE host
  currently runs under, until v1.5.3 Docker), the same ``emit`` writes
  a JSON line directly via the stdlib ``logging`` module. This was the
  v1.5.1 baseline and remains the dev / pytest path.

In both paths the record carries ``event``, ``ts``, ``trace_id``, and
the caller-supplied fields. trace_id is held in a stdlib ContextVar so
concurrent asyncio tasks stay isolated regardless of backend.
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

_EVENTS_LOGGER_NAME = "agentwire_cue.events"
_EVENTS_LOGGER = logging.getLogger(_EVENTS_LOGGER_NAME)

# Detect structlog at import time so the per-event branch is a cheap
# attribute read instead of a try/except.
try:
    import structlog  # type: ignore
    _HAS_STRUCTLOG = True
except Exception:  # pragma: no cover - structlog absence is the default in tests
    structlog = None  # type: ignore[assignment]
    _HAS_STRUCTLOG = False

_STRUCTLOG_CONFIGURED = False


def _ensure_structlog_configured() -> None:
    """Configure structlog once with a JSON renderer over stdlib logging.

    Configuration is idempotent and lazy so that importing this module
    has no side effect on logging setup for callers that never emit.
    """
    global _STRUCTLOG_CONFIGURED
    if _STRUCTLOG_CONFIGURED or not _HAS_STRUCTLOG:
        return
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _STRUCTLOG_CONFIGURED = True


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_id(trace_id: str) -> None:
    _current_trace_id.set(trace_id)


def get_trace_id() -> str | None:
    return _current_trace_id.get()


def reset_trace_id() -> None:
    _current_trace_id.set(None)


def using_structlog() -> bool:
    """True if emit() goes through structlog (for diagnostics / tests)."""
    return _HAS_STRUCTLOG


def emit(event: str, **fields: Any) -> None:
    """Emit a structured event log line. Always includes event, ts, trace_id."""
    record: dict[str, Any] = {
        "event": event,
        "ts": time.time(),
        "trace_id": get_trace_id(),
        **fields,
    }
    if _HAS_STRUCTLOG:
        _ensure_structlog_configured()
        # structlog's JSONRenderer turns the kwargs back into a JSON line,
        # which the underlying stdlib logger emits as the record message.
        structlog.get_logger(_EVENTS_LOGGER_NAME).info(event, **{
            k: v for k, v in record.items() if k != "event"
        })
        return
    _EVENTS_LOGGER.info(json.dumps(record, ensure_ascii=False, default=str))


def install_json_log_sink(stream=None) -> None:
    """Mirror all structured events to a JSON-only log file (default stdout).

    Existing human-readable loggers stay unchanged.
    """
    sink = stream or sys.stdout
    handler = logging.StreamHandler(sink)
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(lambda record: record.name == _EVENTS_LOGGER_NAME)
    _EVENTS_LOGGER.addHandler(handler)
    _EVENTS_LOGGER.propagate = False
