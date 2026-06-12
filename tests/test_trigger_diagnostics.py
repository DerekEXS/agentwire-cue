from __future__ import annotations

import logging

import pytest

from agentwire_cue.core.statechart import Event, StatechartEngine
from agentwire_cue.core.types import Plugin


def _make_plugin(spec: dict, name: str = "diagnostic-test") -> Plugin:
    return Plugin(
        name=name,
        version="0.1.0",
        api_version="agentwire/v1.2",
        meta={"name": name, "version": "0.1.0"},
        spec=spec,
        resolved_persist_path=None,
        permissions={},
        secrets={},
        triggers=[],
    )


@pytest.mark.asyncio
async def test_no_transition_reports_guard_false_reason(caplog):
    caplog.set_level(logging.INFO, logger="agentwire_cue.statechart")
    spec = {
        "statechart": {
            "initial": "watching",
            "context": {"last_notified_round": 3},
            "states": {
                "watching": {
                    "on": {
                        "history_change": {
                            "target": "watching",
                            "guard": "event.new_round > context.last_notified_round",
                        }
                    }
                }
            },
        }
    }
    engine = StatechartEngine(_make_plugin(spec))

    result = await engine.transition(Event(type="history_change", payload={"new_round": 2}))

    assert result.no_transition is True
    assert result.reason == "guard_false"
    assert result.details["guard_expression"] == "event.new_round > context.last_notified_round"
    assert result.details["actual_value"] is False
    assert any("guard_false" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_no_transition_reports_guard_eval_error_reason():
    spec = {
        "statechart": {
            "initial": "watching",
            "states": {
                "watching": {
                    "on": {
                        "history_change": {
                            "target": "watching",
                            "guard": "context.missing > (",
                        }
                    }
                }
            },
        }
    }
    engine = StatechartEngine(_make_plugin(spec))

    result = await engine.transition(Event(type="history_change"))

    assert result.no_transition is True
    assert result.reason == "guard_eval_error"
    assert result.details["guard_expression"] == "context.missing > ("
    assert "error" in result.details


@pytest.mark.asyncio
async def test_no_transition_reports_history_empty_from_history_guard():
    class EmptyHistoryClient:
        def list_peers(self):
            return [{"name": "Pawly", "uuid": "pawly-uuid", "total_rounds": 0}]

        def list_messages(self, peer, limit=5, since_round=0):
            return []

    spec = {
        "statechart": {
            "initial": "watching",
            "states": {
                "watching": {
                    "on": {
                        "history_change": {
                            "target": "watching",
                            "guard": "peers.Pawly.history.last_inbound_contains('urgent:')",
                        }
                    }
                }
            },
        }
    }
    engine = StatechartEngine(_make_plugin(spec), history_client=EmptyHistoryClient())

    result = await engine.transition(Event(type="history_change"))

    assert result.no_transition is True
    assert result.reason == "history_empty"
    assert result.details["peer"] == "Pawly"


@pytest.mark.asyncio
async def test_no_transition_reports_peer_not_found_from_history_guard():
    class EmptyHistoryClient:
        def list_peers(self):
            return []

        def list_messages(self, peer, limit=5, since_round=0):
            return []

    spec = {
        "statechart": {
            "initial": "watching",
            "states": {
                "watching": {
                    "on": {
                        "history_change": {
                            "target": "watching",
                            "guard": "peers.NonExistent.history.last_inbound_contains('urgent:')",
                        }
                    }
                }
            },
        }
    }
    engine = StatechartEngine(_make_plugin(spec), history_client=EmptyHistoryClient())

    result = await engine.transition(Event(type="history_change"))

    assert result.no_transition is True
    assert result.reason == "peer_not_found"
    assert result.details["peer"] == "NonExistent"
