"""AgentWire-Cue v1.3.1 patch 2: Trigger abstraction + await setup (D2).

v1.3 §6.3 had Trigger abstract class with fire() / setup() / teardown().
v1.3.1 patch 2 D2 makes register() async and awaits setup() so fire-before-ready
race is eliminated.

Note: v1.4 P0 #1 implements the full scheduler (cron + a2a_message_type
dispatch). This file provides the contract + exception class.
"""
from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("agentwire_cue.trigger")


class TriggerSetupError(Exception):
    """v1.3.1 patch 2 D2: raised when trigger.setup() fails at host startup.

    Triggered when:
    - cron expression is invalid
    - timezone cannot be parsed
    - a2a_message_type pattern conflicts with another plugin
    - any other setup-time failure

    Connected to LoaderError semantics: 1 trigger fail → entire plugin fails
    to load (not "partial loading" — spec §6.7).
    """


@dataclass
class TriggerEvent:
    """Event passed from trigger to statechart engine."""
    type: str
    payload: dict
    plugin_name: str
    received_at_ms: int


class Trigger(ABC):
    """Abstract base. Concrete: A2ATrigger, CronTrigger (v1.4 P0 #1)."""

    def __init__(self, trigger_def: dict, plugin):
        self.id = trigger_def['id']
        self.type = trigger_def['type']
        self.config = trigger_def.get('config', {})
        self.plugin = plugin

    @abstractmethod
    async def setup(self) -> None:
        """v1.3.1 patch 2 D2: async, may raise TriggerSetupError."""

    @abstractmethod
    async def teardown(self) -> None:
        """Async cleanup (cancel tasks, unregister handlers)."""

    @abstractmethod
    def matches(self, payload: dict) -> bool:
        """True if this trigger should fire for the given payload."""


class TriggerScheduler:
    """v1.3.1 patch 2 D2: register() async, awaits setup().

    P0 #1 (v1.4) wires concrete trigger types (cron / a2a_message_type) and
    the actual A2A listener integration. This base class provides the
    registration contract + concurrency primitive.
    """

    def __init__(self) -> None:
        self._triggers: dict[str, Trigger] = {}  # trigger_id -> Trigger
        self._by_plugin: dict[str, list[str]] = {}  # plugin_name -> [trigger_id]

    async def register(self, trigger: Trigger) -> None:
        """Register a trigger. MUST await setup() to prevent fire-before-ready.

        Raises TriggerSetupError if setup() fails — caller (loader) propagates
        as LoaderError so 1 trigger fail → entire plugin fails to load.
        """
        try:
            await trigger.setup()
        except TriggerSetupError:
            raise
        except Exception as e:
            raise TriggerSetupError(
                f"trigger {trigger.id!r} setup failed for plugin {trigger.plugin.name!r}: {e}"
            ) from e
        self._triggers[trigger.id] = trigger
        self._by_plugin.setdefault(trigger.plugin.name, []).append(trigger.id)
        log.info("registered trigger %s (type=%s) for plugin %s",
                 trigger.id, trigger.type, trigger.plugin.name)

    async def register_all(self, triggers: list[Trigger]) -> None:
        """v1.3.1 patch 2 D2 + v1.4 §2.1.1: concurrent registration for SLO.

        Used at host startup. asyncio.gather runs all setup() in parallel —
        total time bounded by slowest trigger, not sum.
        """
        await asyncio.gather(*(self.register(t) for t in triggers))

    async def unregister_plugin(self, plugin_name: str) -> None:
        """Tear down all triggers for a plugin (used on shutdown / unload)."""
        for tid in self._by_plugin.get(plugin_name, []):
            trigger = self._triggers.get(tid)
            if trigger is not None:
                try:
                    await trigger.teardown()
                except Exception as e:
                    log.warning("teardown of %s failed: %s", tid, e)
                del self._triggers[tid]
        self._by_plugin.pop(plugin_name, None)

    async def shutdown(self) -> None:
        """v1.4 §2.4 P4: tear down all triggers (called from host shutdown)."""
        for plugin_name in list(self._by_plugin.keys()):
            await self.unregister_plugin(plugin_name)

    def get(self, trigger_id: str) -> Trigger | None:
        return self._triggers.get(trigger_id)

    def by_plugin(self, plugin_name: str) -> list[Trigger]:
        return [self._triggers[tid] for tid in self._by_plugin.get(plugin_name, [])]

    def all_triggers(self) -> list[Trigger]:
        return list(self._triggers.values())
