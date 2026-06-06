"""AgentWire-Cue v1.3 statechart engine.

Implements v1.3 §4 spec:
- 6-step transition algorithm (v1.2 spec §4.1)
- State metadata (entered_at_ms, duration_ms)
- Action dispatch (basic types; 3 new action types come in PR3 with permission)
- Template rendering via expression.render_template
- Context persistence: atomic tmp+rename, sensitive-field blacklist
- State restore from disk on init
- Concurrent safety: per-engine asyncio.Lock

This module has zero network/IO dependencies — those are injected via
`A2AClient` (PR3) and `PermissionEnforcer` (PR3) so that unit tests can
run with stubs.
"""
from __future__ import annotations

import asyncio
import fnmatch
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .expression import render_template
from .types import Plugin, StateMetadata
from .trigger import Trigger, TriggerEvent, TriggerScheduler, TriggerSetupError

log = logging.getLogger("agentwire_cue.statechart")


def now_ms() -> int:
    return int(time.time() * 1000)


class ActionError(Exception):
    """Raised by an action handler. Caller decides what to do."""


# ---------- Event & TransitionResult ----------

@dataclass(frozen=True)
class Event:
    """An incoming event (A2A message, cron tick, timer, etc.)."""
    type: str
    payload: dict = field(default_factory=dict)
    message_id: str | None = None  # for A2A reply
    source_peer: str | None = None  # for A2A from_fallback
    raw: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        # Variable resolution: event.X. Most useful keys: message.text,
        # type, payload.* — flatten payload into the top level for template
        # convenience while keeping the original .payload for structured access.
        merged = dict(self.payload)
        merged["type"] = self.type
        if self.message_id is not None:
            merged["message_id"] = self.message_id
        if self.source_peer is not None:
            merged["source_peer"] = self.source_peer
        return merged


@dataclass
class TransitionResult:
    OK: bool
    target: str | None = None
    no_transition: bool = False
    error: str | None = None

    @classmethod
    def matched(cls, target: str) -> "TransitionResult":
        return cls(OK=True, target=target)

    @classmethod
    def no_match(cls) -> "TransitionResult":
        return cls(OK=False, no_transition=True)

    @classmethod
    def error(cls, message: str) -> "TransitionResult":
        """v1.3.1 P0-2: explicit error result (e.g. invalid target).

        State is unchanged (transition aborted before mutating).
        """
        return cls(OK=False, error=message)


# ---------- Action handlers ----------

ActionHandler = Callable[[dict, "EvalEnv"], Awaitable[None]]


def _action_log(action: dict, env: "EvalEnv") -> Awaitable[None]:
    level = action["with"].get("level", "info").upper()
    message = render_template(action["with"]["message"], env.as_dict())
    getattr(log, level.lower(), log.info)("[%s] %s", env.plugin_name, message)
    return _noop()


def _action_set_context(action: dict, env: "EvalEnv") -> Awaitable[None]:
    for k, v in action["with"].items():
        env.context[k] = render_template(v, env.as_dict())
    return _noop()


def _action_increment_context(action: dict, env: "EvalEnv") -> Awaitable[None]:
    key = action["with"]["key"]
    by = int(action["with"].get("by", 1))
    env.context[key] = int(env.context.get(key, 0)) + by
    return _noop()


async def _noop() -> None:
    return None


# Registry: action_type -> handler. PR3 will register write_file/spawn_subprocess/
# http_request and replace the reply_a2a/send_a2a stubs.
ACTION_REGISTRY: dict[str, ActionHandler] = {
    "log": _action_log,
    "set_context": _action_set_context,
    "increment_context": _action_increment_context,
}


def register_action(name: str, handler: ActionHandler) -> None:
    """Public API for PR3 to add handlers (write_file, http_request, etc.)."""
    ACTION_REGISTRY[name] = handler


# ---------- EvalEnv (template guard context) ----------

class EvalEnv:
    """The 6-namespace + 1-function env passed to guard eval and templates.

    Holds a SHARED reference to the engine's context dict (not a copy) so that
    action side effects (set_context / increment_context / write_file) are
    visible to subsequent actions and the persist step.

    v1.4.3: includes `peers` and `history` namespaces (proxied via
    HistoryProxy), populated from a HistoryClient when available.
    """

    def __init__(
        self,
        plugin: Plugin,
        event: Event,
        current_state: str,
        entered_at_ms: int,
        context: dict,
        history_client=None,
    ) -> None:
        self.plugin = plugin
        self.event = event
        self.current_state = current_state
        self.entered_at_ms = entered_at_ms
        self.context = context  # SHARED reference — see class docstring
        self.history_client = history_client

    @property
    def plugin_name(self) -> str:
        return self.plugin.name

    def as_dict(self) -> dict:
        from .history_proxy import _HistoryNamespace, _PeersNamespace
        peers_ns = _PeersNamespace(self.history_client)
        history_ns = _HistoryNamespace(peers_ns)
        # Refresh snapshot from the (cached) client
        peers_ns.refresh()
        return {
            "event": self.event.as_dict(),
            "context": self.context,
            "state": {
                "id": self.current_state,
                "duration_ms": max(0, now_ms() - self.entered_at_ms),
                "entered_at_ms": self.entered_at_ms,
            },
            "meta": self.plugin.meta,
            "now": now_ms(),
            "peers": peers_ns,
            "history": history_ns,
        }


# ---------- Statechart engine ----------

_SENSITIVE_PATTERNS = ("*token*", "*secret*", "*password*", "*credential*")


class StatechartEngine:
    """One engine per plugin. PR2: basic 6-step + persist + restore.

    PR3 will add: timer (after/deadline) management, fallback dispatch,
    3 new action types wired to permission enforcer.
    """

    def __init__(
        self,
        plugin: Plugin,
        a2a_reply: Callable[[str, str], Awaitable[None]] | None = None,
        a2a_send: Callable[[str, str], Awaitable[None]] | None = None,
        from_fallback: bool = False,
        history_client=None,
    ) -> None:
        self.plugin = plugin
        spec_sc = plugin.spec.get("statechart", {})
        self.states: dict[str, dict] = spec_sc.get("states", {})
        self.initial: str = spec_sc.get("initial", "")
        self.context: dict = dict(spec_sc.get("context", {}))
        self.persist_path: Path | None = plugin.resolved_persist_path
        self.persist_exclude: list[str] = spec_sc.get("persist", {}).get("exclude", []) \
            if isinstance(spec_sc.get("persist"), dict) else []
        self.history_client = history_client  # v1.4.3

        self.current_state: str = self.initial
        self.state_entered_at_ms: int = now_ms()
        self._lock = asyncio.Lock()
        self.from_fallback = from_fallback

        # Action helpers (PR3 may replace these with A2A client wrappers)
        self._a2a_reply = a2a_reply or (lambda mid, text: _noop())
        self._a2a_send = a2a_send or (lambda peer, text: _noop())

    # ----- public API -----

    def state_def(self, name: str) -> dict:
        return self.states.get(name, {})

    def is_final(self, name: str) -> bool:
        return self.state_def(name).get("type") == "final"

    async def transition(self, event: Event) -> TransitionResult:
        """v1.2 spec §4.1: 6-step transition. Held under asyncio.Lock."""
        async with self._lock:
            return await self._transition_locked(event)

    # ----- internal -----

    async def _transition_locked(self, event: Event) -> TransitionResult:
        from .expression import evaluate, parse as parse_expr

        state_def = self.state_def(self.current_state)
        on_handlers = state_def.get("on", {}) or {}
        transitions = on_handlers.get(event.type) or []
        if isinstance(transitions, dict):
            transitions = [transitions]

        env = EvalEnv(
            self.plugin, event,
            self.current_state, self.state_entered_at_ms,
            self.context,
            history_client=self.history_client,
        )

        for t in transitions:
            if not isinstance(t, dict):
                continue
            guard_str = t.get("guard")
            if guard_str:
                try:
                    ast = parse_expr(guard_str)
                    if not evaluate(ast, env.as_dict()):
                        continue
                except Exception as e:  # parse or eval
                    log.error("guard eval failed for %s: %s", self.plugin.name, e)
                    return TransitionResult.no_match()

            target = t.get("target")
            if not target:
                continue

            # v1.3.1 P0-2: runtime target check (loader validates at startup,
            # but the statechart may have been hot-reloaded or constructed
            # from a partial spec).
            if target not in self.states:
                log.error(
                    "plugin=%s target=%r not in states (available=%s)",
                    self.plugin.name, target, sorted(self.states.keys()),
                )
                return TransitionResult.error(
                    f"target {target!r} not in spec.states {sorted(self.states.keys())}"
                )

            # 2-4: actions (transition-level + entry actions)
            try:
                for action in t.get("actions", []) or []:
                    await self._dispatch_action(action, env)
                for action in self.state_def(target).get("actions", []) or []:
                    await self._dispatch_action(action, env)
            except ActionError as e:
                log.error(
                    "action failed in %s state=%s event=%s: %s",
                    self.plugin.name, self.current_state, event.type, e,
                )
                if self.from_fallback:
                    # spec §6.1.2: fallback inner send_a2a: log, don't recurse
                    return TransitionResult.no_match()
                return TransitionResult.no_match()  # state unchanged

            # 5: switch state + reset metadata
            self.current_state = target
            self.state_entered_at_ms = now_ms()
            log.info(
                "plugin=%s transitioned to %s on event %s",
                self.plugin.name, target, event.type,
            )

            # 6: persist
            await self.persist()

            return TransitionResult.matched(target)

        return TransitionResult.no_match()

    async def _dispatch_action(self, action: dict, env: EvalEnv) -> None:
        action_type = action.get("type")
        if not action_type:
            raise ActionError("action missing 'type'")
        # Special-case a2a actions first — they use the injected client,
        # not the generic registry.
        if action_type == "reply_a2a":
            if not env.event.message_id:
                raise ActionError("reply_a2a requires an event with message_id")
            text = render_template(action["with"]["template"], env.as_dict())
            await self._a2a_reply(env.event.message_id, text)
            return
        if action_type == "send_a2a":
            peer = action["with"]["peer"]
            text = render_template(action["with"]["message"], env.as_dict())
            await self._a2a_send(peer, text)
            return
        handler = ACTION_REGISTRY.get(action_type)
        if handler is None:
            raise ActionError(f"unknown action type: {action_type}")
        result = handler(action, env)
        if asyncio.iscoroutine(result):
            await result

    # ----- persistence (v1.2.1 spec §2/§5) -----

    async def persist(self) -> None:
        if not self.persist_path:
            return
        # Filter out excluded + sensitive keys (spec §4.5)
        persistable: dict[str, Any] = {}
        for k, v in self.context.items():
            if k in self.persist_exclude:
                continue
            lk = k.lower()
            if any(fnmatch.fnmatch(lk, p.lower()) for p in _SENSITIVE_PATTERNS):
                continue
            persistable[k] = v
        payload = {
            "schema_version": 1,
            "meta": {"name": self.plugin.name, "version": self.plugin.version},
            "context": persistable,
            "state_id": self.current_state,
            "state_entered_at_ms": self.state_entered_at_ms,
            "updated_at_ms": now_ms(),
        }
        # Atomic write: tmp + os.replace
        tmp_path = self.persist_path.with_suffix(self.persist_path.suffix + ".tmp")
        try:
            tmp_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            os.replace(tmp_path, self.persist_path)
        except OSError as e:
            log.error("persist failed for %s: %s", self.plugin.name, e)
            # v1.2.1 spec §2.2: persist failure is non-fatal; state still in memory

    def restore_from_persist(self, *, ignore_corrupt: bool = False) -> bool:
        """Return True if state was restored, False if no state.json or skipped."""
        if not self.persist_path or not self.persist_path.exists():
            return False
        try:
            data = json.loads(self.persist_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            if ignore_corrupt:
                self._backup_and_skip()
                return False
            raise
        if data.get("schema_version") != 1:
            if ignore_corrupt:
                self._backup_and_skip()
                return False
            raise ValueError(f"state.json schema_version mismatch: {data.get('schema_version')}")
        if data.get("meta", {}).get("name") != self.plugin.name:
            if ignore_corrupt:
                self._backup_and_skip()
                return False
            raise ValueError("state.json meta.name mismatch")
        self.context = dict(data.get("context", {}))
        self.current_state = data["state_id"]
        self.state_entered_at_ms = int(data.get("state_entered_at_ms", now_ms()))
        return True

    def _backup_and_skip(self) -> None:
        if not self.persist_path:
            return
        backup = self.persist_path.with_suffix(self.persist_path.suffix + ".corrupt")
        try:
            os.replace(self.persist_path, backup)
            log.warning("backed up corrupt state.json to %s", backup)
        except OSError as e:
            log.error("failed to back up corrupt state.json: %s", e)
