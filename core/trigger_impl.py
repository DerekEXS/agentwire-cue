"""AgentWire-Cue v1.4 §6: concrete Trigger implementations.

- CronTrigger: cron expression + IANA timezone, asyncio task
- A2ATrigger: matches inbound A2A messages by pattern

Both register via TriggerScheduler.register() (v1.3.1 patch 2 D2 awaits setup).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Callable, Optional

try:
    from croniter import croniter
except ImportError:
    croniter = None  # type: ignore

try:
    import pytz
except ImportError:
    pytz = None  # type: ignore

from .statechart import Event
from .trigger import Trigger, TriggerEvent, TriggerSetupError

log = logging.getLogger("agentwire_cue.trigger_impl")


# ---------- CronTrigger ----------

class CronTrigger(Trigger):
    """v1.4 §6.5: cron expression + IANA tz + asyncio task.

    MUST use IANA tz (not local) per v1.3 §6.5 MUST.
    MUST cancel task on teardown.
    """

    def __init__(self, trigger_def: dict, plugin):
        super().__init__(trigger_def, plugin)
        if croniter is None:
            raise TriggerSetupError(
                "croniter package required for cron trigger; pip install croniter"
            )
        if pytz is None:
            raise TriggerSetupError(
                "pytz package required for cron trigger; pip install pytz"
            )
        self.expression: str = self.config.get('expression', '')
        self.timezone_name: str = self.config.get('timezone', 'UTC')
        if not self.expression:
            raise TriggerSetupError(
                f"cron trigger {self.id!r}: expression is required"
            )
        # Validate expression by computing next fire once
        try:
            self.tz = pytz.timezone(self.timezone_name)
        except Exception as e:
            raise TriggerSetupError(
                f"cron trigger {self.id!r}: unknown timezone {self.timezone_name!r}: {e}"
            )
        try:
            self.next_fire_ms = self._compute_next()
        except Exception as e:
            raise TriggerSetupError(
                f"cron trigger {self.id!r}: invalid expression {self.expression!r}: {e}"
            )
        self._task: Optional[asyncio.Task] = None
        self._cancelled = False

    def _compute_next(self) -> int:
        now = datetime.now(self.tz)
        itr = croniter(self.expression, now)
        return int(itr.get_next(datetime).timestamp() * 1000)

    async def setup(self) -> None:
        # Validate we can schedule (basic check)
        if not self.expression.strip():
            raise TriggerSetupError(f"cron trigger {self.id!r}: empty expression")
        # Start the background task
        self._task = asyncio.create_task(self._loop())
        log.info("cron trigger %s registered (expr=%s tz=%s, next fire in %dms)",
                 self.id, self.expression, self.timezone_name,
                 self.next_fire_ms - int(time.time() * 1000))

    async def _loop(self) -> None:
        while not self._cancelled:
            now_ms = int(time.time() * 1000)
            if now_ms >= self.next_fire_ms:
                await self._fire()
                if self._cancelled:
                    return
                try:
                    self.next_fire_ms = self._compute_next()
                except Exception as e:
                    log.error("cron trigger %s: failed to compute next: %s", self.id, e)
                    return
            # Sleep until next fire (capped at 60s)
            sleep_ms = min(60_000, max(100, self.next_fire_ms - int(time.time() * 1000)))
            try:
                await asyncio.sleep(sleep_ms / 1000)
            except asyncio.CancelledError:
                return

    async def _fire(self) -> None:
        event = TriggerEvent(
            type='CRON_FIRED',
            payload={
                'trigger_id': self.id,
                'expression': self.expression,
            },
            plugin_name=self.plugin.name,
            received_at_ms=int(time.time() * 1000),
        )
        try:
            await self.plugin.statechart.transition(Event(
                type=event.type,
                payload=event.payload,
                message_id=None,
            ))
        except Exception as e:
            log.exception("cron trigger %s fire failed: %s", self.id, e)

    async def teardown(self) -> None:
        self._cancelled = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    def matches(self, payload: dict) -> bool:
        # Cron triggers don't match inbound payloads (they self-fire)
        return False


# ---------- A2ATrigger ----------

class A2ATrigger(Trigger):
    """v1.4 §6.4: A2A inbound message matcher.

    Pattern matching: '*' (any) or exact type match.
    """

    def __init__(self, trigger_def: dict, plugin, a2a_listener=None):
        super().__init__(trigger_def, plugin)
        self.match: str = self.config.get('match', '*')
        self._a2a_listener = a2a_listener  # Set by host when registering
        self._registered = False

    async def setup(self) -> None:
        if self._a2a_listener is None:
            raise TriggerSetupError(
                f"a2a_message_type trigger {self.id!r}: no a2a_listener provided. "
                f"Host must pass listener when constructing trigger."
            )
        self._a2a_listener.register_handler(self._on_message)
        self._registered = True
        log.info("a2a trigger %s registered (match=%s)", self.id, self.match)

    def matches(self, payload: dict) -> bool:
        if self.match == '*':
            return True
        msg_type = payload.get('type', '')
        return msg_type == self.match

    async def _on_message(self, message: dict) -> None:
        if not self.matches(message):
            return
        try:
            await self.plugin.statechart.transition(Event(
                type=message.get('type', 'A2A_MESSAGE'),
                payload={'message': message},
                message_id=message.get('message_id'),
            ))
        except Exception as e:
            log.exception("a2a trigger %s fire failed: %s", self.id, e)

    async def teardown(self) -> None:
        # A2AListener's handler list is drained on stop(); we don't unregister
        # individual handlers in v1.4 P0 #1 (deferred to P2 if needed).
        self._registered = False
