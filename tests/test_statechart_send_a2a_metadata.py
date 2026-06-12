"""v1.4.8 RED: send_a2a action forwards metadata and the YAML action schema
supports an optional `metadata` block.

Goals:
- statechart._dispatch_action forwards a render-template applied `metadata`
  payload (when present) to the host's _a2a_send wrapper.
- The host's _a2a_send wrapper passes metadata into a2a_client.send_message.
- Without metadata, behavior is unchanged (backward compatible).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agentwire_cue.core.statechart import Event, StatechartEngine
from agentwire_cue.core.types import Plugin


def _make_plugin(spec: dict) -> Plugin:
    return Plugin(
        name="t-meta", version="1.4.8", api_version="agentwire/v1.2",
        meta={"name": "t-meta", "version": "1.4.8"},
        spec=spec, resolved_persist_path=None,
        permissions={}, secrets={}, triggers=[],
    )


@pytest.mark.asyncio
async def test_send_a2a_action_forwards_metadata_to_a2a_send_helper():
    spec = {
        "statechart": {
            "initial": "send",
            "states": {
                "send": {
                    "on": {
                        "GO": {
                            "target": "send",
                            "actions": [
                                {
                                    "type": "send_a2a",
                                    "with": {
                                        "peer": "Pawly",
                                        "message": {"text": "hi"},
                                        "metadata": {
                                            "workflow_pointer": {
                                                "workflow_file": "wf.yaml",
                                                "current_step": "step_5",
                                            }
                                        },
                                    },
                                }
                            ],
                        }
                    }
                }
            },
        }
    }
    captured = {}

    async def fake_send(peer, text, metadata=None):
        captured["peer"] = peer
        captured["text"] = text
        captured["metadata"] = metadata

    engine = StatechartEngine(_make_plugin(spec), a2a_send=fake_send)
    await engine.transition(Event(type="GO"))

    assert captured["peer"] == "Pawly"
    assert captured["text"] == "hi"
    assert captured["metadata"] == {
        "workflow_pointer": {
            "workflow_file": "wf.yaml",
            "current_step": "step_5",
        }
    }


@pytest.mark.asyncio
async def test_send_a2a_action_without_metadata_passes_none():
    spec = {
        "statechart": {
            "initial": "send",
            "states": {
                "send": {
                    "on": {
                        "GO": {
                            "target": "send",
                            "actions": [
                                {
                                    "type": "send_a2a",
                                    "with": {
                                        "peer": "Pawly",
                                        "message": {"text": "hi"},
                                    },
                                }
                            ],
                        }
                    }
                }
            },
        }
    }
    captured = {}

    async def fake_send(peer, text, metadata=None):
        captured["metadata"] = metadata

    engine = StatechartEngine(_make_plugin(spec), a2a_send=fake_send)
    await engine.transition(Event(type="GO"))

    assert captured["metadata"] is None


@pytest.mark.asyncio
async def test_send_a2a_renders_template_inside_metadata():
    spec = {
        "statechart": {
            "initial": "send",
            "context": {"workflow": "v3.0"},
            "states": {
                "send": {
                    "on": {
                        "GO": {
                            "target": "send",
                            "actions": [
                                {
                                    "type": "send_a2a",
                                    "with": {
                                        "peer": "Pawly",
                                        "message": {"text": "hi"},
                                        "metadata": {
                                            "workflow_pointer": {
                                                "workflow_version": "{{context.workflow}}",
                                            }
                                        },
                                    },
                                }
                            ],
                        }
                    }
                }
            },
        }
    }
    captured = {}

    async def fake_send(peer, text, metadata=None):
        captured["metadata"] = metadata

    engine = StatechartEngine(_make_plugin(spec), a2a_send=fake_send)
    await engine.transition(Event(type="GO"))

    assert captured["metadata"] == {
        "workflow_pointer": {"workflow_version": "v3.0"}
    }
