"""v1.4.8 RED: cue.yaml peers: configuration is loaded and exposed on Plugin."""
from __future__ import annotations

from pathlib import Path

import pytest

from agentwire_cue.core.loader import load_plugin


def test_loader_collects_peers_aliases(tmp_path: Path):
    yaml_text = (
        "apiVersion: agentwire/v1.2\n"
        "kind: plugin\n"
        "metadata:\n"
        "  name: owner-alert\n"
        "  version: 1.4.8\n"
        "spec:\n"
        "  peers:\n"
        "    Pawly:\n"
        "      uuid: pawly-demo-uuid\n"
        "      url: http://pawly.example.invalid:18800\n"
        "      description: 小爪 - QwenPaw @ 阿里云\n"
        "    初梦:\n"
        "      uuid: chumeng-demo-uuid\n"
        "      url: http://127.0.0.1:18800\n"
        "  triggers:\n"
        "    - id: t1\n"
        "      type: history_change\n"
        "      config:\n"
        "        peer: Pawly\n"
        "        granularity: round\n"
        "  statechart:\n"
        "    initial: watching\n"
        "    states: { watching: { on: { history_change: { target: watching } } } }\n"
        "  secrets: []\n"
        "  permissions: {}\n"
    )
    yaml_path = tmp_path / "owner-alert.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")

    plugin = load_plugin(yaml_path)
    assert plugin is not None, "owner-alert.yaml failed to load (peers: schema mismatch?)"
    assert plugin.peers == {
        "Pawly": {
            "uuid": "pawly-demo-uuid",
            "url": "http://pawly.example.invalid:18800",
            "description": "小爪 - QwenPaw @ 阿里云",
        },
        "初梦": {
            "uuid": "chumeng-demo-uuid",
            "url": "http://127.0.0.1:18800",
        },
    }


def test_loader_without_peers_returns_empty_mapping(tmp_path: Path):
    yaml_text = (
        "apiVersion: agentwire/v1.2\n"
        "kind: plugin\n"
        "metadata:\n"
        "  name: owner-alert\n"
        "  version: 1.4.8\n"
        "spec:\n"
        "  triggers:\n"
        "    - id: t1\n"
        "      type: history_change\n"
        "      config:\n"
        "        peer: Pawly\n"
        "        granularity: round\n"
        "  statechart:\n"
        "    initial: watching\n"
        "    states: { watching: { on: { history_change: { target: watching } } } }\n"
        "  secrets: []\n"
        "  permissions: {}\n"
    )
    yaml_path = tmp_path / "owner-alert.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")

    plugin = load_plugin(yaml_path)
    assert plugin is not None
    assert plugin.peers == {}


def test_loader_rejects_malformed_peers_missing_required_field(tmp_path: Path):
    yaml_text = (
        "apiVersion: agentwire/v1.2\n"
        "kind: plugin\n"
        "metadata:\n"
        "  name: owner-alert\n"
        "  version: 1.4.8\n"
        "spec:\n"
        "  peers:\n"
        "    Pawly:\n"
        "      uuid: pawly-demo-uuid\n"
        "  triggers:\n"
        "    - id: t1\n"
        "      type: history_change\n"
        "      config: { peer: Pawly, granularity: round }\n"
        "  statechart:\n"
        "    initial: watching\n"
        "    states: { watching: { on: { history_change: { target: watching } } } }\n"
        "  secrets: []\n"
        "  permissions: {}\n"
    )
    yaml_path = tmp_path / "owner-alert.yaml"
    yaml_path.write_text(yaml_text, encoding="utf-8")

    plugin = load_plugin(yaml_path)
    assert plugin is None, "peers entry missing required 'url' must fail schema validation"
