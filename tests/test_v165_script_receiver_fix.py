"""v1.6.5 RED: script-receiver trigger fix + owner-alert alias resolution.

Two related bugs share the same root cause:

1. **script-receiver** uses ``a2a_content_match`` trigger type. That type
   registers a handler on the A2AListener (a passive HTTP server on 18801),
   but CORE never pushes inbound messages to CUE. Result: the trigger is
   registered but never fires.

2. **history_change** triggers (used by ``owner-alert`` since v1.4.3) match
   ``self.peer`` (e.g. ``"remote_peer_a"``) against CORE's ``messages/peers``
   return value's ``name`` field. CORE returns ``name`` as the first 8 chars
   of the peer uuid (e.g. ``"75755f13"``), NOT the alias. So
   ``peer: "remote_peer_a"`` never matches and the trigger is silent.

Both are fixed in v1.6.5:

- ``script-receiver/cue.yaml`` rewritten to use ``history_change`` + guard
  (``last_inbound_contains`` for keywords, ``last_inbound_text`` for content).
- ``trigger_impl.py::_poll_loop`` resolves alias → CORE uuid/name via
  ``_history_client._aliases`` before matching.
- ``history_proxy.py::_PeerHistoryProxy`` gains ``last_inbound_text()``
  so guard expressions can extract the full inbound payload.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))


# ===========================================================================
# Fixtures: minimal fake HistoryClient + Plugin for HistoryChangeTrigger
# ===========================================================================

class _FakeHistoryClient:
    """Minimal stub matching the HistoryClient surface used by
    HistoryChangeTrigger and _PeerHistoryProxy.

    - ``list_peers()`` → CORE-style snapshot (name = 8-char uuid prefix)
    - ``list_messages(peer, limit)`` → configurable message list
    - ``_aliases`` → alias table (alias_name → {"uuid": "...", ...})
    """

    def __init__(self, peers_snapshot: list[dict], messages_by_peer: dict[str, list[dict]] | None = None,
                 aliases: dict[str, dict] | None = None):
        self._snapshot = peers_snapshot
        self._messages_by_peer = messages_by_peer or {}
        self._aliases = aliases or {}
        self._list_peers_calls = 0
        self._list_messages_calls = 0

    def list_peers(self) -> list[dict]:
        self._list_peers_calls += 1
        return list(self._snapshot)

    def list_messages(self, peer: str, limit: int = 5) -> list[dict]:
        self._list_messages_calls += 1
        # Resolve alias if possible (mirror real HistoryClient._resolve_alias)
        uuid_or_name = peer
        for alias_name, alias_meta in self._aliases.items():
            if alias_name == peer:
                uuid_or_name = alias_meta.get("uuid") or peer
                break
        return list(self._messages_by_peer.get(uuid_or_name, []))


class _StubPlugin:
    """Minimal plugin stand-in for HistoryChangeTrigger."""

    def __init__(self, name: str = "test-plugin"):
        self.name = name
        self.context: dict = {}
        self.fired_events: list = []


# ===========================================================================
# Task 0a RED: history_change alias resolution
# ===========================================================================

@pytest.mark.asyncio
async def test_history_change_resolves_alias_to_core_uuid_name():
    """v1.6.5: ``peer: 'remote_peer_a'`` triggers when CORE returns
    ``name='75755f137e7451c0'[:8] = '75755f13'``."""
    from agentwire_cue.core.trigger_impl import HistoryChangeTrigger

    # CORE returns name as 8-char truncated uuid
    client = _FakeHistoryClient(
        peers_snapshot=[
            {"name": "75755f13", "uuid": "75755f137e7451c0", "last_round": 0, "total_rounds": 0},
        ],
        aliases={
            "remote_peer_a": {"uuid": "75755f137e7451c0", "url": "http://100.91.108.62:18800"},
        },
    )
    plugin = _StubPlugin()
    trigger = HistoryChangeTrigger(
        trigger_def={"id": "test", "type": "history_change", "config": {"peer": "remote_peer_a", "granularity": "round", "poll_interval_seconds": 1}},
        plugin=plugin,
        history_client=client,
    )

    matched = trigger._peer_matches("75755f13")
    assert matched is True, (
        "v1.6.5 BUG: alias 'remote_peer_a' must resolve to CORE name '75755f13'. "
        "Without the fix, _peer_matches returns False and the trigger never fires."
    )


@pytest.mark.asyncio
async def test_history_change_matches_when_peer_is_core_name():
    """Back-compat: explicit CORE name (8-char hex) still matches."""
    from agentwire_cue.core.trigger_impl import HistoryChangeTrigger

    client = _FakeHistoryClient(
        peers_snapshot=[{"name": "75755f13", "uuid": "75755f137e7451c0", "last_round": 0}],
    )
    plugin = _StubPlugin()
    trigger = HistoryChangeTrigger(
        trigger_def={"id": "test", "type": "history_change", "config": {"peer": "75755f13", "granularity": "round", "poll_interval_seconds": 1}},
        plugin=plugin,
        history_client=client,
    )

    assert trigger._peer_matches("75755f13") is True


@pytest.mark.asyncio
async def test_history_change_wildcard_matches_all():
    """Back-compat: ``peer: '*'`` matches any key."""
    from agentwire_cue.core.trigger_impl import HistoryChangeTrigger

    client = _FakeHistoryClient(peers_snapshot=[])
    plugin = _StubPlugin()
    trigger = HistoryChangeTrigger(
        trigger_def={"id": "test", "type": "history_change", "config": {"peer": "*"}},
        plugin=plugin,
        history_client=client,
    )

    assert trigger._peer_matches("75755f13") is True
    assert trigger._peer_matches("0592602b") is True


@pytest.mark.asyncio
async def test_history_change_does_not_match_unknown_alias():
    """Negative case: alias not in alias table → no match."""
    from agentwire_cue.core.trigger_impl import HistoryChangeTrigger

    client = _FakeHistoryClient(
        peers_snapshot=[{"name": "75755f13", "uuid": "75755f137e7451c0", "last_round": 0}],
        aliases={"different_alias": {"uuid": "deadbeef12345678"}},
    )
    plugin = _StubPlugin()
    trigger = HistoryChangeTrigger(
        trigger_def={"id": "test", "type": "history_change", "config": {"peer": "remote_peer_a"}},
        plugin=plugin,
        history_client=client,
    )

    # 'remote_peer_a' not in aliases; '75755f13' not the alias either
    assert trigger._peer_matches("75755f13") is False


# ===========================================================================
# Task 1 RED: last_inbound_text()
# ===========================================================================

def test_last_inbound_text_returns_concatenated_inbound():
    """v1.6.5: ``last_inbound_text()`` returns text from inbound messages only."""
    from agentwire_cue.core.history_proxy import _PeerHistoryProxy

    meta = {"name": "75755f13", "uuid": "75755f137e7451c0", "last_round": 1, "total_rounds": 1}
    messages = [
        {"role": "inbound", "parts": [{"type": "text", "text": "project: foo\nscenes:\n  - 1"}]},
    ]
    client = _FakeHistoryClient(
        peers_snapshot=[meta],
        messages_by_peer={"75755f137e7451c0": messages},
    )
    proxy = _PeerHistoryProxy(peer_meta=meta, client=client, requested_peer="remote_peer_a")

    text = proxy.last_inbound_text()
    assert "project: foo" in text
    assert "scenes:" in text


def test_last_inbound_text_skips_outbound():
    """v1.6.5: outbound messages (CORE auto-acks) are filtered out."""
    from agentwire_cue.core.history_proxy import _PeerHistoryProxy

    meta = {"name": "75755f13", "uuid": "75755f137e7451c0", "last_round": 2, "total_rounds": 2}
    messages = [
        {"role": "outbound", "parts": [{"type": "text", "text": "ACK from CORE"}]},
        {"role": "inbound", "parts": [{"type": "text", "text": "real script content"}]},
    ]
    client = _FakeHistoryClient(
        peers_snapshot=[meta],
        messages_by_peer={"75755f137e7451c0": messages},
    )
    proxy = _PeerHistoryProxy(peer_meta=meta, client=client, requested_peer="remote_peer_a")

    text = proxy.last_inbound_text()
    assert "real script content" in text
    assert "ACK from CORE" not in text


def test_last_inbound_text_concatenates_multiple_text_parts():
    """v1.6.5: multi-part messages join their text parts with newline."""
    from agentwire_cue.core.history_proxy import _PeerHistoryProxy

    meta = {"name": "75755f13", "uuid": "75755f137e7451c0", "last_round": 1, "total_rounds": 1}
    messages = [
        {
            "role": "inbound",
            "parts": [
                {"type": "text", "text": "project: foo"},
                {"type": "text", "text": "scenes:\n  - 1\n  - 2"},
            ],
        },
    ]
    client = _FakeHistoryClient(
        peers_snapshot=[meta],
        messages_by_peer={"75755f137e7451c0": messages},
    )
    proxy = _PeerHistoryProxy(peer_meta=meta, client=client, requested_peer="remote_peer_a")

    text = proxy.last_inbound_text()
    assert "project: foo" in text
    assert "scenes:" in text
    assert "scenes:\n  - 1\n  - 2" in text


def test_last_inbound_text_raises_history_empty_for_no_messages():
    """v1.6.5: empty history raises HistoryDiagnosticError (consistent with
    ``last_inbound_contains``)."""
    from agentwire_cue.core.history_proxy import _PeerHistoryProxy, HistoryDiagnosticError

    meta = {"name": "75755f13", "uuid": "75755f137e7451c0", "last_round": 0, "total_rounds": 0}
    client = _FakeHistoryClient(
        peers_snapshot=[meta],
        messages_by_peer={"75755f137e7451c0": []},
    )
    proxy = _PeerHistoryProxy(peer_meta=meta, client=client, requested_peer="remote_peer_a")

    with pytest.raises(HistoryDiagnosticError) as exc:
        proxy.last_inbound_text()
    assert exc.value.reason == "history_empty"


# ===========================================================================
# Task 3 RED: script-receiver trigger wiring
# ===========================================================================

@pytest.mark.asyncio
async def test_script_receiver_yaml_uses_history_change_not_a2a_content_match():
    """v1.6.5: script-receiver/cue.yaml must use history_change trigger type.

    ``a2a_content_match`` registers a handler on A2AListener (passive server)
    but no push path exists from CORE → trigger never fires. The fix is to
    switch to history_change + guard expression, matching the pattern used
    by owner-alert since v1.4.3.
    """
    import yaml
    cue_path = Path(__file__).resolve().parents[1] / "examples" / "script-receiver" / "cue.yaml"
    data = yaml.safe_load(cue_path.read_text(encoding="utf-8"))

    triggers = data["spec"]["triggers"]
    assert len(triggers) >= 1, "script-receiver must have at least one trigger"
    on_script = next((t for t in triggers if t["id"] == "on-script-received"), None)
    assert on_script is not None, "missing on-script-received trigger"
    assert on_script["type"] == "history_change", (
        f"v1.6.5 BUG: on-script-received must use history_change trigger type, "
        f"got {on_script['type']!r}. a2a_content_match requires a CORE→CUE push "
        f"channel that doesn't exist."
    )


@pytest.mark.asyncio
async def test_script_receiver_yaml_declares_remote_peer_a_alias():
    """v1.6.5: script-receiver/cue.yaml must declare ``peers.remote_peer_a``.

    Without this declaration, the alias resolution in guard expressions like
    ``peers.remote_peer_a.history.last_inbound_contains('project:')`` would
    return a proxy with empty metadata → ``HistoryDiagnosticError(peer_not_found)``.
    """
    import yaml
    cue_path = Path(__file__).resolve().parents[1] / "examples" / "script-receiver" / "cue.yaml"
    data = yaml.safe_load(cue_path.read_text(encoding="utf-8"))

    peers = data["spec"].get("peers", {})
    assert "remote_peer_a" in peers, (
        "v1.6.5 BUG: script-receiver/cue.yaml must declare peers.remote_peer_a "
        "for guard expressions to resolve. Without it, history lookups raise "
        "HistoryDiagnosticError(reason='peer_not_found')."
    )


@pytest.mark.asyncio
async def test_script_receiver_yaml_guard_checks_both_keywords():
    """v1.6.5: guard must require both 'project:' AND 'scenes:'.

    The original a2a_content_match trigger had ``min_match: 2`` with both
    keywords in ``contains``. The history_change replacement must preserve
    this dual-keyword requirement via guard expression.
    """
    import yaml
    cue_path = Path(__file__).resolve().parents[1] / "examples" / "script-receiver" / "cue.yaml"
    data = yaml.safe_load(cue_path.read_text(encoding="utf-8"))

    states = data["spec"]["statechart"]["states"]
    # Find the state that has the guard (v1.6.5 uses "watching" state with
    # self-transition on history_change)
    state_with_guard = next(
        (s for s in states.values() if "history_change" in s.get("on", {})),
        None,
    )
    assert state_with_guard is not None, f"no state with history_change 'on' handler, states: {list(states.keys())}"
    guard_text = state_with_guard["on"]["history_change"].get("guard", "")
    assert "project:" in guard_text, f"guard must check 'project:' keyword, got: {guard_text!r}"
    assert "scenes:" in guard_text, f"guard must check 'scenes:' keyword, got: {guard_text!r}"
    assert "last_inbound_contains" in guard_text, (
        f"guard must use last_inbound_contains() for keyword matching, got: {guard_text!r}"
    )