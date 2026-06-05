"""Test suite for statechart engine — v1.3 §4 + v1.2 spec.md §4."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from agentwire_cue.core.statechart import (
    ActionError,
    Event,
    StatechartEngine,
    TransitionResult,
    register_action,
)
from agentwire_cue.core.types import Plugin


# ---------- Fixtures ----------

def _make_plugin(
    spec: dict,
    persist_path: Path | None = None,
    name: str = "test",
) -> Plugin:
    return Plugin(
        name=name,
        version="0.1.0",
        api_version="agentwire/v1.2",
        meta={"name": name, "version": "0.1.0"},
        spec=spec,
        resolved_persist_path=persist_path,
        permissions={},
        secrets={},
        triggers=[],
    )


def _simple_spec() -> dict:
    return {
        "triggers": [{"id": "t", "type": "a2a_message_type", "config": {}}],
        "statechart": {
            "initial": "idle",
            "context": {"n": 0},
            "states": {
                "idle": {
                    "on": {
                        "GO": {
                            "target": "active",
                            "actions": [
                                {"type": "increment_context", "with": {"key": "n", "by": 1}}
                            ],
                        }
                    }
                },
                "active": {"type": "final"},
            },
        },
        "secrets": [],
        "permissions": {},
    }


# ---------- TransitionResult ----------

class TestTransitionResult:
    def test_matched(self):
        r = TransitionResult.matched("active")
        assert r.OK is True and r.target == "active" and r.no_transition is False

    def test_no_match(self):
        r = TransitionResult.no_match()
        assert r.OK is False and r.target is None and r.no_transition is True


# ---------- Event ----------

class TestEvent:
    def test_as_dict_merges_payload_and_type(self):
        e = Event(type="X", payload={"foo": 1}, message_id="m1", source_peer="p1")
        d = e.as_dict()
        assert d["type"] == "X"
        assert d["foo"] == 1
        assert d["message_id"] == "m1"
        assert d["source_peer"] == "p1"

    def test_default_payload_is_empty(self):
        e = Event(type="X")
        assert e.as_dict() == {"type": "X"}


# ---------- 6-step algorithm ----------

class TestSixStepAlgorithm:
    @pytest.mark.asyncio
    async def test_no_matching_event_returns_no_transition(self):
        eng = StatechartEngine(_make_plugin(_simple_spec()))
        result = await eng.transition(Event(type="UNKNOWN"))
        assert result.no_transition is True
        assert eng.current_state == "idle"

    @pytest.mark.asyncio
    async def test_matching_event_transitions_and_runs_actions(self):
        eng = StatechartEngine(_make_plugin(_simple_spec()))
        result = await eng.transition(Event(type="GO"))
        assert result.OK is True and result.target == "active"
        assert eng.current_state == "active"
        assert eng.context["n"] == 1

    @pytest.mark.asyncio
    async def test_guard_rejects_transition(self):
        spec = _simple_spec()
        spec["statechart"]["states"]["idle"]["on"]["GO"] = {
            "target": "active",
            "guard": "context.n > 5",
            "actions": [],
        }
        eng = StatechartEngine(_make_plugin(spec))
        result = await eng.transition(Event(type="GO"))
        assert result.no_transition is True
        assert eng.current_state == "idle"

    @pytest.mark.asyncio
    async def test_guard_passes_and_transitions(self):
        spec = _simple_spec()
        spec["statechart"]["context"] = {"n": 10}
        spec["statechart"]["states"]["idle"]["on"]["GO"] = {
            "target": "active",
            "guard": "context.n > 5",
            "actions": [],
        }
        eng = StatechartEngine(_make_plugin(spec))
        result = await eng.transition(Event(type="GO"))
        assert result.OK is True

    @pytest.mark.asyncio
    async def test_guard_eval_error_returns_no_transition(self):
        spec = _simple_spec()
        spec["statechart"]["states"]["idle"]["on"]["GO"] = {
            "target": "active",
            "guard": "context.n > (",  # syntax error
            "actions": [],
        }
        eng = StatechartEngine(_make_plugin(spec))
        result = await eng.transition(Event(type="GO"))
        assert result.no_transition is True
        assert eng.current_state == "idle"

    @pytest.mark.asyncio
    async def test_state_metadata_reset_on_transition(self):
        eng = StatechartEngine(_make_plugin(_simple_spec()))
        original_entered = eng.state_entered_at_ms
        await eng.transition(Event(type="GO"))
        assert eng.state_entered_at_ms >= original_entered

    @pytest.mark.asyncio
    async def test_entry_actions_run_on_target_state(self):
        spec = _simple_spec()
        spec["statechart"]["states"]["active"]["actions"] = [
            {"type": "set_context", "with": {"flag": "entered"}}
        ]
        eng = StatechartEngine(_make_plugin(spec))
        await eng.transition(Event(type="GO"))
        assert eng.context["flag"] == "entered"


# ---------- Action dispatch ----------

class TestActionDispatch:
    @pytest.mark.asyncio
    async def test_log_action(self, caplog):
        import logging
        caplog.set_level(logging.INFO, logger="agentwire_cue.statechart")
        spec = _simple_spec()
        spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "log", "with": {"level": "info", "message": "hi from {{meta.name}}"}}
        ]
        eng = StatechartEngine(_make_plugin(spec))
        await eng.transition(Event(type="GO"))
        assert any("hi from test" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_set_context(self):
        spec = _simple_spec()
        spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "set_context", "with": {"last": "{{event.text}}"}}
        ]
        eng = StatechartEngine(_make_plugin(spec))
        await eng.transition(Event(type="GO", payload={"text": "hello"}))
        assert eng.context["last"] == "hello"

    @pytest.mark.asyncio
    async def test_increment_context_default_by_1(self):
        spec = _simple_spec()
        spec["statechart"]["context"] = {"n": 5}
        eng = StatechartEngine(_make_plugin(spec))
        await eng.transition(Event(type="GO"))
        assert eng.context["n"] == 6

    @pytest.mark.asyncio
    async def test_unknown_action_raises_action_error(self):
        spec = _simple_spec()
        spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
            {"type": "no_such_action", "with": {}}
        ]
        eng = StatechartEngine(_make_plugin(spec))
        result = await eng.transition(Event(type="GO"))
        # action failure → state unchanged, no_transition
        assert result.no_transition is True
        assert eng.current_state == "idle"

    @pytest.mark.asyncio
    async def test_custom_action_registration(self):
        called: list[str] = []

        async def my_handler(action: dict, env) -> None:
            called.append(env.plugin_name)

        register_action("my_custom", my_handler)
        try:
            spec = _simple_spec()
            spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = [
                {"type": "my_custom", "with": {}}
            ]
            eng = StatechartEngine(_make_plugin(spec))
            await eng.transition(Event(type="GO"))
            assert called == ["test"]
        finally:
            # cleanup so other tests aren't affected
            from agentwire_cue.core.statechart import ACTION_REGISTRY
            ACTION_REGISTRY.pop("my_custom", None)

    @pytest.mark.asyncio
    async def test_reply_a2a_uses_injected_helper(self):
        replies: list[tuple[str, str]] = []

        async def reply(mid: str, text: str) -> None:
            replies.append((mid, text))

        spec = _simple_spec()
        spec["statechart"]["states"]["active"]["actions"] = [
            {"type": "reply_a2a", "with": {"template": "ack: {{event.text}}"}}
        ]
        eng = StatechartEngine(_make_plugin(spec), a2a_reply=reply)
        await eng.transition(Event(type="GO", payload={"text": "hi"}, message_id="m1"))
        assert replies == [("m1", "ack: hi")]

    @pytest.mark.asyncio
    async def test_reply_a2a_without_message_id_raises(self):
        spec = _simple_spec()
        spec["statechart"]["states"]["active"]["actions"] = [
            {"type": "reply_a2a", "with": {"template": "x"}}
        ]
        eng = StatechartEngine(_make_plugin(spec))
        result = await eng.transition(Event(type="GO"))  # no message_id
        assert result.no_transition is True

    @pytest.mark.asyncio
    async def test_send_a2a_uses_injected_helper(self):
        sends: list[tuple[str, str]] = []

        async def send(peer: str, text: str) -> None:
            sends.append((peer, text))

        spec = _simple_spec()
        spec["statechart"]["states"]["active"]["actions"] = [
            {"type": "send_a2a", "with": {"peer": "peer-1", "message": "{{event.text}}"}}
        ]
        eng = StatechartEngine(_make_plugin(spec), a2a_send=send)
        await eng.transition(Event(type="GO", payload={"text": "ping"}))
        assert sends == [("peer-1", "ping")]


# ---------- Persistence ----------

class TestPersistence:
    @pytest.mark.asyncio
    async def test_persist_writes_atomic_file(self, tmp_path: Path):
        target = tmp_path / "state.json"
        spec = _simple_spec()
        spec["statechart"]["persist"] = {"path": str(target)}
        eng = StatechartEngine(_make_plugin(spec, persist_path=target))
        await eng.transition(Event(type="GO"))
        assert target.exists()
        data = json.loads(target.read_text())
        assert data["schema_version"] == 1
        assert data["state_id"] == "active"
        assert data["meta"]["name"] == "test"
        assert data["context"]["n"] == 1

    @pytest.mark.asyncio
    async def test_persist_excludes_sensitive_keys(self, tmp_path: Path):
        target = tmp_path / "state.json"
        spec = _simple_spec()
        # Drop the increment_context action; we want to test persistence filtering,
        # not the increment behavior.
        spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = []
        spec["statechart"]["context"] = {"n": 1, "auth_token": "secret", "user_password": "x", "name": "ok"}
        spec["statechart"]["persist"] = {"path": str(target)}
        eng = StatechartEngine(_make_plugin(spec, persist_path=target))
        await eng.transition(Event(type="GO"))
        data = json.loads(target.read_text())
        assert "auth_token" not in data["context"]
        assert "user_password" not in data["context"]
        assert data["context"]["n"] == 1
        assert data["context"]["name"] == "ok"

    @pytest.mark.asyncio
    async def test_persist_respects_exclude_list(self, tmp_path: Path):
        target = tmp_path / "state.json"
        spec = _simple_spec()
        spec["statechart"]["states"]["idle"]["on"]["GO"]["actions"] = []
        spec["statechart"]["context"] = {"n": 1, "ephemeral": "x"}
        spec["statechart"]["persist"] = {"path": str(target), "exclude": ["ephemeral"]}
        eng = StatechartEngine(_make_plugin(spec, persist_path=target))
        await eng.transition(Event(type="GO"))
        data = json.loads(target.read_text())
        assert "ephemeral" not in data["context"]
        assert data["context"]["n"] == 1

    @pytest.mark.asyncio
    async def test_persist_uses_atomic_rename(self, tmp_path: Path):
        target = tmp_path / "state.json"
        spec = _simple_spec()
        spec["statechart"]["persist"] = {"path": str(target)}
        eng = StatechartEngine(_make_plugin(spec, persist_path=target))
        await eng.transition(Event(type="GO"))
        # tmp file should not be left behind
        leftover = list(tmp_path.glob("*.tmp"))
        assert leftover == []


class TestRestore:
    def test_restore_no_state_file_returns_false(self, tmp_path: Path):
        target = tmp_path / "state.json"
        eng = StatechartEngine(_make_plugin(_simple_spec(), persist_path=target))
        assert eng.restore_from_persist() is False

    def test_restore_loads_state_and_context(self, tmp_path: Path):
        target = tmp_path / "state.json"
        target.write_text(json.dumps({
            "schema_version": 1,
            "meta": {"name": "test", "version": "0.1.0"},
            "context": {"n": 42, "last": "hello"},
            "state_id": "active",
            "state_entered_at_ms": 1234567890,
            "updated_at_ms": 1234567899,
        }), encoding="utf-8")
        eng = StatechartEngine(_make_plugin(_simple_spec(), persist_path=target))
        assert eng.restore_from_persist() is True
        assert eng.current_state == "active"
        assert eng.context == {"n": 42, "last": "hello"}
        assert eng.state_entered_at_ms == 1234567890

    def test_restore_schema_mismatch_raises(self, tmp_path: Path):
        target = tmp_path / "state.json"
        target.write_text(json.dumps({
            "schema_version": 999,
            "meta": {"name": "test"},
            "context": {},
            "state_id": "x",
            "state_entered_at_ms": 0,
        }), encoding="utf-8")
        eng = StatechartEngine(_make_plugin(_simple_spec(), persist_path=target))
        with pytest.raises(ValueError, match="schema_version"):
            eng.restore_from_persist()

    def test_restore_schema_mismatch_ignored_with_flag(self, tmp_path: Path):
        target = tmp_path / "state.json"
        target.write_text(json.dumps({
            "schema_version": 999,
            "meta": {"name": "test"},
            "context": {},
            "state_id": "x",
            "state_entered_at_ms": 0,
        }), encoding="utf-8")
        eng = StatechartEngine(_make_plugin(_simple_spec(), persist_path=target))
        assert eng.restore_from_persist(ignore_corrupt=True) is False
        # file should be backed up
        assert (tmp_path / "state.json.corrupt").exists()

    def test_restore_meta_name_mismatch_raises(self, tmp_path: Path):
        target = tmp_path / "state.json"
        target.write_text(json.dumps({
            "schema_version": 1,
            "meta": {"name": "wrong-name", "version": "0.1.0"},
            "context": {},
            "state_id": "active",
            "state_entered_at_ms": 0,
        }), encoding="utf-8")
        eng = StatechartEngine(_make_plugin(_simple_spec(), persist_path=target))
        with pytest.raises(ValueError, match="meta.name"):
            eng.restore_from_persist()

    def test_restore_corrupt_json_raises(self, tmp_path: Path):
        target = tmp_path / "state.json"
        target.write_text("{not json", encoding="utf-8")
        eng = StatechartEngine(_make_plugin(_simple_spec(), persist_path=target))
        with pytest.raises(json.JSONDecodeError):
            eng.restore_from_persist()

    def test_restore_corrupt_json_ignored_with_flag(self, tmp_path: Path):
        target = tmp_path / "state.json"
        target.write_text("{not json", encoding="utf-8")
        eng = StatechartEngine(_make_plugin(_simple_spec(), persist_path=target))
        assert eng.restore_from_persist(ignore_corrupt=True) is False
        assert (tmp_path / "state.json.corrupt").exists()


# ---------- Concurrency ----------

class TestConcurrency:
    @pytest.mark.asyncio
    async def test_lock_prevents_interleaved_state_mutations(self):
        # Two actions both yield, but with the lock held throughout transition,
        # the engine's state after both transitions is internally consistent
        # (no half-applied state).
        seq: list[str] = []

        async def slow_handler(action, env):
            seq.append("start")
            await asyncio.sleep(0)  # yield — would interleave without the lock
            seq.append("end")

        from agentwire_cue.core.statechart import ACTION_REGISTRY
        register_action("slow", slow_handler)
        try:
            spec = _simple_spec()
            # Two distinct events; both go to "active" via a "wait" middleman
            spec["statechart"]["states"]["wait"] = {
                "on": {"PING2": {"target": "active", "actions": []}}
            }
            spec["statechart"]["states"]["idle"]["on"]["PING1"] = {
                "target": "wait", "actions": [{"type": "slow", "with": {}}]
            }
            eng = StatechartEngine(_make_plugin(spec))
            r1, r2 = await asyncio.gather(
                eng.transition(Event(type="PING1")),
                eng.transition(Event(type="PING2")),
            )
            # After both, the engine is in "active" and the seq is monotonic
            # (start,end,start,end) — no interleaving across the two transitions.
            assert eng.current_state == "active"
            # Lock guarantees per-transition atomicity:
            # either seq == ['start','end','start','end'] (PING1 ran first, PING2
            # waited for lock) or ['start','end'] (PING2 no_match because already
            # moved to wait by PING1) — but never ['start','start','end','end']
            # which would mean the actions interleaved.
            assert "start,end,start,end" == ",".join(seq) or seq == ["start", "end"]
        finally:
            ACTION_REGISTRY.pop("slow", None)
