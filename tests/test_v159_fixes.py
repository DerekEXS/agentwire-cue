from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from agentwire_cue.core.loader import _validate_targets, load_plugin


def test_set_context_owner_alert_uses_correct_kv_structure():
    owner_alert = Path(__file__).resolve().parents[1] / "examples" / "owner-alert" / "cue.yaml"
    plugin = load_plugin(owner_alert)
    assert plugin is not None
    assert "key" not in plugin.spec.get("statechart", {}).get("context", {}), (
        "bug: 'key' should not appear in context root — the set_context action "
        "must use direct key-value pairs"
    )


def test_validate_targets_checks_resilience_on_exhaust():
    bad = {
        "apiVersion": "agentwire/v1.2",
        "kind": "plugin",
        "metadata": {"name": "bad-res", "version": "1.0.0"},
        "spec": {
            "triggers": [
                {"id": "t", "type": "cron", "config": {"expression": "0 0 * * *", "timezone": "UTC"}}
            ],
            "statechart": {
                "initial": "idle",
                "states": {"idle": {}},
            },
            "resilience": {
                "a2a_retries": 2,
                "backoff_ms": 500,
                "on_exhaust": "missing_state",
            },
            "secrets": [],
            "permissions": {
                "network": {"http_egress": [], "raw_socket": False},
                "filesystem": [],
                "subprocess": {"allow": []},
                "env": [],
                "peers": [],
                "timers": {"max_concurrent": 1, "min_interval_ms": 1000},
            },
        },
    }
    errors = _validate_targets(bad, path=Path("test.yaml"))
    assert any("on_exhaust" in e for e in errors), (
        f"expected on_exhaust validation error, got: {errors}"
    )


def test_validate_targets_accepts_valid_on_exhaust():
    good = {
        "apiVersion": "agentwire/v1.2",
        "kind": "plugin",
        "metadata": {"name": "good-res", "version": "1.0.0"},
        "spec": {
            "triggers": [
                {"id": "t", "type": "cron", "config": {"expression": "0 0 * * *", "timezone": "UTC"}}
            ],
            "statechart": {
                "initial": "idle",
                "states": {
                    "idle": {},
                    "idle_fallback": {},
                },
            },
            "resilience": {
                "a2a_retries": 2,
                "backoff_ms": 500,
                "on_exhaust": "idle",
            },
            "secrets": [],
            "permissions": {
                "network": {"http_egress": [], "raw_socket": False},
                "filesystem": [],
                "subprocess": {"allow": []},
                "env": [],
                "peers": [],
                "timers": {"max_concurrent": 1, "min_interval_ms": 1000},
            },
        },
    }
    errors = _validate_targets(good, path=Path("test.yaml"))
    resilience_errors = [e for e in errors if "on_exhaust" in e]
    assert resilience_errors == [], (
        f"valid on_exhaust should not produce errors, got: {resilience_errors}"
    )
