"""v1.4.4 unit tests for the owner-alert killer example.

Validates v1.4.4 acceptance scope (master拍板: cue 单测 only):
  - yaml loads + schema validates
  - 2 history_change triggers registered
  - state machine: single state 'watching', self-loop on history_change
  - guard dedup via context.last_notified_round (避免 P0 spamming bug)
  - send_a2a dispatched to peer=main with correct payload
  - no transition when inbound does NOT contain 'urgent:'
  - no send when same round re-fires (dedup test)
  - multi-peer parallel triggers (Pawly + 初梦) don't collide
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agentwire_cue.core.loader import load_plugin
from agentwire_cue.core.types import Plugin
from agentwire_cue.core.statechart import (
    Event,
    StatechartEngine,
    EvalEnv,
)


@pytest.fixture(autouse=True)
def _restore_eval_env_as_dict():
    original = EvalEnv.as_dict
    yield
    EvalEnv.as_dict = original


# ---------- Fixtures ----------

OWNER_ALERT_YAML = Path(__file__).parent.parent / "examples" / "owner-alert" / "cue.yaml"


def _make_plugin() -> Plugin:
    """Mirror of owner-alert.yaml — single-state self-loop with context dedup."""
    return Plugin(
        name="owner-alert",
        version="1.4.4",
        api_version="agentwire/v1.2",
        meta={"name": "owner-alert", "version": "1.4.4"},
        spec={
            "triggers": [
                {"id": "on-pawly-urgent", "type": "history_change",
                 "config": {"peer": "Pawly", "granularity": "round"}},
                {"id": "on-chumeng-urgent", "type": "history_change",
                 "config": {"peer": "初梦", "granularity": "round"}},
            ],
            "statechart": {
                "initial": "watching",
                "context": {"last_notified_round": 0},
                "states": {
                    "watching": {
                        "on": {
                            "history_change": {
                                "target": "watching",
                                "guard": (
                                    "(peers.Pawly.history.last_inbound_contains('urgent:')"
                                    " || peers.初梦.history.last_inbound_contains('urgent:'))"
                                    " && event.new_round > context.last_notified_round"
                                ),
                                "actions": [
                                    {"type": "set_context", "with": {
                                        "last_notified_round": "{{event.new_round}}",
                                    }},
                                    {"type": "send_a2a", "with": {
                                        "peer": "main",
                                        "message": {
                                            "type": "A2A_MESSAGE",
                                            "text": "🚨 {{event.peer}} 紧急: round {{event.new_round}}",
                                        },
                                    }},
                                ],
                            }
                        }
                    },
                },
            },
        },
        resolved_persist_path=None,
        permissions={"peers": [{"id": "main", "allow_messages": ["*"]}]},
        secrets={},
        triggers=[],
    )


class _MockHistoryProxy:
    def __init__(self, text):
        self._text = text

    def last(self, n=5):
        if self._text is None:
            return []
        return [{
            "round": 1, "ts": "2026-06-07T00:00:00Z",
            "role": "inbound", "msg_id": "m1",
            "context_id": None, "peer_uuid": "uuid", "peer_name": "Pawly",
            "parts": [{"type": "text", "text": self._text}],
        }]

    def last_n_rounds(self, n=5): return self.last(n)
    def count(self): return 1
    def last_round(self): return 1
    def last_inbound_contains(self, needle):
        if self._text is None:
            return False
        return needle in self._text
    def last_outbound_contains(self, needle):
        return False


class _MockPeersNamespace:
    def __init__(self, pawly_text, chumeng_text):
        self._p = _MockHistoryProxy(pawly_text)
        self._c = _MockHistoryProxy(chumeng_text)

    def __getattr__(self, name):
        if name == "Pawly":
            class _P:
                history = self._p
                last_round = 1
                total_rounds = 1
            return _P()
        if name == "初梦":
            class _C:
                history = self._c
                last_round = 1
                total_rounds = 1
            return _C()
        raise AttributeError(name)

    def get(self, name, default=None):
        return getattr(self, name, default)

    def keys(self): return ["Pawly", "初梦"]
    def items(self): return [("Pawly", None), ("初梦", None)]


def _make_engine(plugin, pawly_text=None, chumeng_text=None, ctx_last_notified=0):
    eng = StatechartEngine(plugin)
    eng.history_client = MagicMock()
    # Seed context with last_notified_round
    eng.context["last_notified_round"] = ctx_last_notified
    import agentwire_cue.core.statechart as sc_mod
    from agentwire_cue.core.history_proxy import _HistoryNamespace
    peers = _MockPeersNamespace(pawly_text, chumeng_text)
    history = _HistoryNamespace(peers)
    original_as_dict = sc_mod.EvalEnv.as_dict
    def patched_as_dict(self):
        d = original_as_dict(self)
        d["peers"] = peers
        d["history"] = history
        return d
    sc_mod.EvalEnv.as_dict = patched_as_dict
    return eng


# ---------- Tests ----------

def test_yaml_load_and_schema_validate():
    p = load_plugin(OWNER_ALERT_YAML)
    assert p is not None, "owner-alert.yaml failed to load (schema mismatch?)"
    assert p.name == "owner-alert"
    # v1.6.4: version bumped to align with sanitization release
    assert p.version == "1.6.4"


def test_owner_alert_demo_has_main_peer_alias():
    p = load_plugin(OWNER_ALERT_YAML)
    assert p is not None
    assert "main" in p.peers
    assert p.peers["main"]["url"] == "http://127.0.0.1:18800"


def test_two_history_change_triggers_registered():
    p = load_plugin(OWNER_ALERT_YAML)
    assert p is not None
    assert len(p.triggers) == 2
    types = sorted(t.type for t in p.triggers)
    assert types == ["history_change", "history_change"]
    # v1.6.4: peer slot names sanitized from user-personal names
    # (Pawly / 初梦) to placeholder slot names (remote_peer_a / _b).
    peers = sorted(t.config["peer"] for t in p.triggers)
    assert peers == ["remote_peer_a", "remote_peer_b"]


def test_owner_alert_no_personal_agent_names_in_peers():
    """v1.6.4 regression guard: peer alias keys must not encode user names.

    The example plugin must remain anonymous; real peer config goes
    in *.local.yaml overlays (gitignored). If this fails, scrub the
    cue.yaml back to placeholder naming.
    """
    p = load_plugin(OWNER_ALERT_YAML)
    assert p is not None
    forbidden = {"pawly", "chumeng", "chúmèng", "初梦", "小爪"}
    for alias in p.peers:
        assert alias.lower() not in forbidden, (
            f"peer alias {alias!r} leaks user-personal naming; "
            f"use a placeholder like remote_peer_a"
        )


def test_state_machine_single_state_watching():
    p = load_plugin(OWNER_ALERT_YAML)
    assert p is not None
    sc = p.spec["statechart"]
    assert sc["initial"] == "watching"
    assert "watching" in sc["states"]
    # v1.4.4 fix: only one state, self-loop on history_change
    assert sc["states"]["watching"]["on"]["history_change"]["target"] == "watching"


def test_history_change_with_urgent_triggers_send_a2a():
    """v1.4.4: 含 urgent: → set_context + send_a2a, self-loop stays in watching."""
    plugin = _make_plugin()
    eng = _make_engine(plugin, pawly_text="urgent: 视频脚本出错", ctx_last_notified=0)

    captured = []
    async def fake_send_a2a(peer, text):
        captured.append({"peer": peer, "text": text})
    eng._a2a_send = fake_send_a2a

    async def run():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "Pawly", "prev_round": 0, "new_round": 1, "new_count": 1},
        ))
    result = asyncio.run(run())

    assert result.OK is True and result.target == "watching"
    assert eng.current_state == "watching"
    # set_context bumped last_notified_round
    assert eng.context["last_notified_round"] == 1
    assert len(captured) == 1
    assert captured[0]["peer"] == "main"
    assert "🚨" in captured[0]["text"]
    assert "Pawly" in captured[0]["text"]


def test_history_change_dedup_same_round_does_not_resend():
    """v1.4.4 P0 fix: same round re-fired → guard fails → no send_a2a (no spam)."""
    plugin = _make_plugin()
    # Pretend we already notified on round 5
    eng = _make_engine(plugin, pawly_text="urgent: still urgent", ctx_last_notified=5)

    captured = []
    async def fake_send_a2a(peer, text):
        captured.append({"peer": peer, "text": text})
    eng._a2a_send = fake_send_a2a

    # Re-fire with same round (history_change poll sees no increment,
    # but if forced, guard should block)
    async def run():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "Pawly", "prev_round": 4, "new_round": 5, "new_count": 1},
        ))
    result = asyncio.run(run())

    assert result.no_transition is True
    assert eng.current_state == "watching"
    # last_notified_round should NOT have advanced (5 == 5, guard blocks set_context)
    assert eng.context["last_notified_round"] == 5
    assert captured == [], "P0 BUG: same round re-triggered send_a2a — would spam main agent"


def test_history_change_without_urgent_does_not_match():
    plugin = _make_plugin()
    eng = _make_engine(plugin, pawly_text="普通消息不紧急", ctx_last_notified=0)

    captured = []
    async def fake_send_a2a(peer, text):
        captured.append({"peer": peer, "text": text})
    eng._a2a_send = fake_send_a2a

    async def run():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "Pawly", "prev_round": 0, "new_round": 1, "new_count": 1},
        ))
    result = asyncio.run(run())

    assert result.no_transition is True
    assert eng.current_state == "watching"
    assert captured == []
    # last_notified_round stays 0
    assert eng.context["last_notified_round"] == 0


def test_multi_peer_parallel_no_collision():
    """v1.4.4: Pawly + 初梦 都触发 history_change → 任一含 urgent: 都应触发 notify."""
    plugin = _make_plugin()
    eng = _make_engine(
        plugin,
        pawly_text="urgent: pawly 紧急",
        chumeng_text="普通消息",
        ctx_last_notified=0,
    )

    captured = []
    async def fake_send_a2a(peer, text):
        captured.append({"peer": peer, "text": text})
    eng._a2a_send = fake_send_a2a

    # Pawly 触发 (round 1)
    async def run_pawly():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "Pawly", "prev_round": 0, "new_round": 1, "new_count": 1},
        ))
    r1 = asyncio.run(run_pawly())
    assert r1.OK is True and r1.target == "watching"
    assert eng.context["last_notified_round"] == 1
    assert len(captured) == 1

    # 初梦 触发 (round 1, ctx already 1, so guard should block)
    async def run_chumeng():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "初梦", "prev_round": 0, "new_round": 1, "new_count": 1},
        ))
    r2 = asyncio.run(run_chumeng())
    # Round 1 already notified via Pawly → 初梦 same round should dedup
    assert r2.no_transition is True
    assert len(captured) == 1  # only Pawly triggered send


def test_no_send_a2a_when_no_history_yet():
    """v1.4.4: 历史里没消息时 last_inbound_contains 返回 false → guard 失败."""
    plugin = _make_plugin()
    eng = _make_engine(plugin, pawly_text=None, chumeng_text=None, ctx_last_notified=0)

    captured = []
    async def fake_send_a2a(peer, text):
        captured.append({"peer": peer, "text": text})
    eng._a2a_send = fake_send_a2a

    async def run():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "Pawly", "prev_round": 0, "new_round": 1, "new_count": 1},
        ))
    r = asyncio.run(run())
    assert r.no_transition is True
    assert captured == []


def test_new_round_dedup_via_context():
    """v1.4.4: 不同 round 都应该告警一次 (not just once per session)."""
    plugin = _make_plugin()
    eng = _make_engine(plugin, pawly_text="urgent: foo", ctx_last_notified=0)

    captured = []
    async def fake_send_a2a(peer, text):
        captured.append({"peer": peer, "text": text})
    eng._a2a_send = fake_send_a2a

    # Round 1: should send
    async def r1():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "Pawly", "prev_round": 0, "new_round": 1, "new_count": 1},
        ))
    asyncio.run(r1())
    assert len(captured) == 1
    assert eng.context["last_notified_round"] == 1

    # Round 1 again (duplicate poll): should NOT send
    async def r1_dup():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "Pawly", "prev_round": 0, "new_round": 1, "new_count": 1},
        ))
    asyncio.run(r1_dup())
    assert len(captured) == 1  # dedup

    # Round 2: should send (new round)
    async def r2():
        return await eng.transition(Event(
            type="history_change",
            payload={"peer": "Pawly", "prev_round": 1, "new_round": 2, "new_count": 1},
        ))
    asyncio.run(r2())
    assert len(captured) == 2
    assert eng.context["last_notified_round"] == 2
