"""v1.5.2 RED tests: spec.requires block + Host degraded marking.

Contract:
- Plugins MAY declare ``spec.requires`` with optional ``plugins``,
  ``peers``, and ``capabilities`` lists.
- After all plugins load, ``Host._check_requires`` validates dependencies
  against the aggregated set of loaded plugins / peer aliases / known
  capabilities. Plugins missing dependencies are marked ``degraded`` with
  a human-readable reason; they remain loaded but the host MUST refuse to
  register their triggers.
- Plugins without a ``requires`` block (or with an empty one) are not
  degraded.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from agentwire_cue.core.host import Host
from agentwire_cue.core.loader import load_plugin


def _write_plugin(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / f"{name}.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_plugin_extracts_requires_block(tmp_path):
    body = """
apiVersion: agentwire/v1.2
kind: plugin
metadata:
  name: video-publish
  version: 1.0.0
spec:
  requires:
    plugins: [owner-alert]
    peers: [main]
    capabilities: [metadata]
  triggers:
    - id: t-tick
      type: cron
      config:
        expression: "0 0 * * *"
        timezone: "UTC"
  statechart:
    initial: s0
    states:
      s0: {}
  secrets: []
  permissions:
    network: { http_egress: [], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers: []
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
"""
    p = _write_plugin(tmp_path, "video-publish", body)
    plugin = load_plugin(p)
    assert plugin is not None
    assert plugin.requires == {
        "plugins": ["owner-alert"],
        "peers": ["main"],
        "capabilities": ["metadata"],
    }


def test_load_plugin_without_requires_returns_empty_requires(tmp_path):
    body = """
apiVersion: agentwire/v1.2
kind: plugin
metadata:
  name: no-deps
  version: 1.0.0
spec:
  triggers:
    - id: t-tick
      type: cron
      config:
        expression: "0 0 * * *"
        timezone: "UTC"
  statechart:
    initial: s0
    states:
      s0: {}
  secrets: []
  permissions:
    network: { http_egress: [], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers: []
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
"""
    p = _write_plugin(tmp_path, "no-deps", body)
    plugin = load_plugin(p)
    assert plugin is not None
    assert plugin.requires == {}
    assert plugin.degraded is False


def test_host_check_requires_marks_plugin_degraded_when_plugin_dep_missing(tmp_path):
    body = """
apiVersion: agentwire/v1.2
kind: plugin
metadata:
  name: video-publish
  version: 1.0.0
spec:
  requires:
    plugins: [does-not-exist]
  triggers:
    - id: t-tick
      type: cron
      config:
        expression: "0 0 * * *"
        timezone: "UTC"
  statechart:
    initial: s0
    states:
      s0: {}
  secrets: []
  permissions:
    network: { http_egress: [], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers: []
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
"""
    p = _write_plugin(tmp_path, "video-publish", body)
    plugin = load_plugin(p)
    host = Host(plugin_dir=tmp_path)
    host.plugins = {plugin.name: plugin}

    host._check_requires()

    assert plugin.degraded is True
    assert "does-not-exist" in (plugin.degraded_reason or "")


def test_host_check_requires_marks_plugin_degraded_when_peer_alias_missing(tmp_path):
    body = """
apiVersion: agentwire/v1.2
kind: plugin
metadata:
  name: needs-peer
  version: 1.0.0
spec:
  requires:
    peers: [GhostPeer]
  triggers:
    - id: t-tick
      type: cron
      config:
        expression: "0 0 * * *"
        timezone: "UTC"
  statechart:
    initial: s0
    states:
      s0: {}
  secrets: []
  permissions:
    network: { http_egress: [], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers: []
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
"""
    p = _write_plugin(tmp_path, "needs-peer", body)
    plugin = load_plugin(p)
    host = Host(plugin_dir=tmp_path)
    host.plugins = {plugin.name: plugin}

    host._check_requires()

    assert plugin.degraded is True
    assert "GhostPeer" in (plugin.degraded_reason or "")


def test_host_check_requires_passes_with_satisfied_deps(tmp_path):
    # owner-alert provides the peer aliases; video-publish requires the
    # owner-alert plugin AND the "main" peer it exposes.
    owner_body = """
apiVersion: agentwire/v1.2
kind: plugin
metadata:
  name: owner-alert
  version: 1.5.1
spec:
  peers:
    main: { uuid: "demo", url: "http://demo.invalid" }
  triggers:
    - id: t-tick
      type: cron
      config:
        expression: "0 0 * * *"
        timezone: "UTC"
  statechart:
    initial: s0
    states:
      s0: {}
  secrets: []
  permissions:
    network: { http_egress: [], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers: []
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
"""
    video_body = """
apiVersion: agentwire/v1.2
kind: plugin
metadata:
  name: video-publish
  version: 1.0.0
spec:
  requires:
    plugins: [owner-alert]
    peers: [main]
    capabilities: [metadata]
  triggers:
    - id: t-tick
      type: cron
      config:
        expression: "0 0 * * *"
        timezone: "UTC"
  statechart:
    initial: s0
    states:
      s0: {}
  secrets: []
  permissions:
    network: { http_egress: [], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers: []
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
"""
    owner_p = _write_plugin(tmp_path, "owner-alert", owner_body)
    video_p = _write_plugin(tmp_path, "video-publish", video_body)
    owner = load_plugin(owner_p)
    video = load_plugin(video_p)
    host = Host(plugin_dir=tmp_path)
    host.plugins = {owner.name: owner, video.name: video}

    host._check_requires()

    assert owner.degraded is False
    assert video.degraded is False


def test_host_check_requires_marks_unknown_capability(tmp_path):
    body = """
apiVersion: agentwire/v1.2
kind: plugin
metadata:
  name: needs-unknown
  version: 1.0.0
spec:
  requires:
    capabilities: [time-travel]
  triggers:
    - id: t-tick
      type: cron
      config:
        expression: "0 0 * * *"
        timezone: "UTC"
  statechart:
    initial: s0
    states:
      s0: {}
  secrets: []
  permissions:
    network: { http_egress: [], raw_socket: false }
    filesystem: []
    subprocess: { allow: [] }
    env: []
    peers: []
    timers: { max_concurrent: 1, min_interval_ms: 1000 }
"""
    p = _write_plugin(tmp_path, "needs-unknown", body)
    plugin = load_plugin(p)
    host = Host(plugin_dir=tmp_path)
    host.plugins = {plugin.name: plugin}

    host._check_requires()

    assert plugin.degraded is True
    assert "time-travel" in (plugin.degraded_reason or "")
