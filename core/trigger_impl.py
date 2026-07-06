"""AgentWire-Cue v1.4 §6: concrete Trigger implementations.

- CronTrigger: cron expression + IANA timezone, asyncio task
- A2ATrigger: matches inbound A2A messages by pattern
- HistoryChangeTrigger (v1.4.3): polls CORE /messages/peers, fires
  on round completion or message arrival

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
            from .statechart import Event, run_tracked_transition
            await run_tracked_transition(
                self.plugin,
                Event(type=event.type, payload=event.payload, message_id=None),
                source='cron',
            )
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
            from .statechart import Event, run_tracked_transition
            await run_tracked_transition(
                self.plugin,
                Event(
                    type=message.get('type', 'A2A_MESSAGE'),
                    payload={'message': message},
                    message_id=message.get('messageId') or message.get('message_id'),
                ),
                source='a2a',
            )
        except Exception as e:
            log.exception("a2a trigger %s fire failed: %s", self.id, e)

    async def teardown(self) -> None:
        # A2AListener's handler list is drained on stop(); we don't unregister
        # individual handlers in v1.4 P0 #1 (deferred to P2 if needed).
        self._registered = False


# ---------- HistoryChangeTrigger (v1.4.3) ----------

class HistoryChangeTrigger(Trigger):
    """v1.4.3: fires when a peer's history changes.

    granularity:
      - "round"   (default): fire once per new round completed
      - "message": fire on every new message
      - "manual":  only fires on cue trigger ... manual command

    poll_interval_seconds: how often to check /messages/peers (default 30)
    peer: which peer to watch; "*" = all peers
    """

    def __init__(self, trigger_def: dict, plugin, history_client=None):
        super().__init__(trigger_def, plugin)
        self.granularity: str = self.config.get('granularity', 'round')
        self.peer: str = self.config.get('peer', '*')
        self.poll_interval: int = int(self.config.get('poll_interval_seconds', 30))
        self._history_client = history_client
        self._last_snapshot: dict = {}  # peer_name → last_round
        self._task: Optional[asyncio.Task] = None
        self._running = False

    async def setup(self) -> None:
        if self._history_client is None:
            raise TriggerSetupError(
                f"history_change trigger {self.id!r}: no history_client provided. "
                f"Host must pass HistoryClient when constructing trigger."
            )
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name=f"hist-chg-{self.id}")
        log.info("history_change trigger %s registered (granularity=%s, peer=%s, poll=%ds)",
                 self.id, self.granularity, self.peer, self.poll_interval)

    def _peer_matches(self, key: str) -> bool:
        """v1.6.5: resolve alias → CORE uuid/name before matching.

        Without this, ``peer: 'remote_peer_a'`` (alias) never matches
        CORE's ``name`` field (which is the first 8 chars of the uuid,
        e.g. ``'75755f13'``), so the trigger is registered but never
        fires. Both owner-alert (v1.4.3+) and script-receiver (v1.6.1+)
        were affected by this bug.
        """
        if self.peer == "*":
            return True
        if key == self.peer:
            return True
        # v1.6.5: alias → uuid resolution via HistoryClient._aliases
        aliases = getattr(self._history_client, "_aliases", None) or {}
        for alias_name, alias_meta in aliases.items():
            if alias_name != self.peer:
                continue
            alias_uuid = (alias_meta or {}).get("uuid") or ""
            if not alias_uuid:
                continue
            if key == alias_uuid or key == alias_uuid[:8]:
                return True
        return False

    async def _poll_loop(self) -> None:
        # Initial snapshot
        try:
            peers = self._history_client.list_peers()
            for p in peers:
                key = p.get("name") or p.get("uuid")
                if not self._peer_matches(key):
                    continue
                self._last_snapshot[key] = int(p.get("last_round", 0))
        except Exception as e:
            log.warning("history_change %s initial poll failed: %s", self.id, e)

        while self._running:
            await asyncio.sleep(self.poll_interval)
            try:
                peers = self._history_client.list_peers()
            except Exception as e:
                log.warning("history_change %s poll failed: %s", self.id, e)
                continue

            for p in peers:
                key = p.get("name") or p.get("uuid")
                if not self._peer_matches(key):
                    continue
                last = int(p.get("last_round", 0))
                prev = self._last_snapshot.get(key, 0)
                if last > prev:
                    new_rounds = last - prev
                    log.info("history_change %s: peer=%s new_rounds=%d (was %d → %d)",
                             self.id, key, new_rounds, prev, last)
                    await self._fire(key, prev, last, new_rounds)
                    self._last_snapshot[key] = last

    async def _fire(self, peer: str, prev_round: int, new_round: int, count: int) -> None:
        try:
            from .statechart import Event, run_tracked_transition
            await run_tracked_transition(
                self.plugin,
                Event(
                    type='history_change',
                    payload={
                        'peer': peer,
                        'prev_round': prev_round,
                        'new_round': new_round,
                        'new_count': count,
                        'granularity': self.granularity,
                    },
                ),
                source='history_change',
            )
        except Exception as e:
            log.exception("history_change trigger %s fire failed: %s", self.id, e)

    def matches(self, payload: dict) -> bool:
        # History triggers don't match inbound payloads (they self-fire on poll)
        return False

    async def teardown(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


# ---------- ContentMatchTrigger (v1.6.1) ----------

class ContentMatchTrigger(Trigger):
    """v1.6.1: matches inbound A2A messages by text content.

    Checks whether the message text (all text parts concatenated) contains
    enough keywords from ``contains``. Optionally filters to a specific peer.

    Config keys:
      - contains (list[str]): keywords to search for in message text
      - min_match (int, default 1): minimum keywords that must match
      - peer (str, optional): restrict to messages from this peer alias
    """

    def __init__(self, trigger_def: dict, plugin, a2a_listener=None):
        super().__init__(trigger_def, plugin)
        self.contains: list[str] = self.config.get('contains', [])
        self.min_match: int = int(self.config.get('min_match', 1))
        self.peer_filter: str | None = self.config.get('peer') or None
        self._a2a_listener = a2a_listener
        self._registered = False

    async def setup(self) -> None:
        if self._a2a_listener is None:
            raise TriggerSetupError(
                f"a2a_content_match trigger {self.id!r}: no a2a_listener provided. "
                f"Host must pass listener when constructing trigger."
            )
        if not self.contains:
            raise TriggerSetupError(
                f"a2a_content_match trigger {self.id!r}: 'contains' list is required and must not be empty"
            )
        self._a2a_listener.register_handler(self._on_message)
        self._registered = True
        log.info("a2a_content_match trigger %s registered (contains=%s, min_match=%d, peer=%s)",
                 self.id, self.contains, self.min_match, self.peer_filter or '*')

    def matches(self, payload: dict) -> bool:
        # v1.6.5 fix: A2AListener calls this as a pre-filter before invoking
        # the handler. If we always return False, _on_message is never called
        # and the trigger is dead (even with CORE's push channel working).
        text = self._extract_message_text(payload)
        if not text:
            return False
        return sum(1 for kw in self.contains if kw in text) >= self.min_match

    def _extract_message_text(self, message: dict) -> str:
        """Extract all text from message parts."""
        parts = message.get('parts', [])
        texts = []
        for part in parts:
            if isinstance(part, dict) and part.get('type') == 'text':
                t = part.get('text', '')
                if t:
                    texts.append(t)
            elif isinstance(part, str):
                texts.append(part)
        return ' '.join(texts)

    async def _on_message(self, message: dict) -> None:
        # Peer filter
        if self.peer_filter:
            msg_peer = message.get('peer') or message.get('metadata', {}).get('source_peer', '')
            if msg_peer != self.peer_filter:
                return

        text = self._extract_message_text(message)
        if not text:
            return

        matched_kws = [kw for kw in self.contains if kw in text]
        if len(matched_kws) < self.min_match:
            return

        log.info("a2a_content_match trigger %s fired: matched %d/%d keywords",
                 self.id, len(matched_kws), len(self.contains))

        try:
            from .statechart import Event, run_tracked_transition
            await run_tracked_transition(
                self.plugin,
                Event(
                    type='content_match',
                    payload={
                        'peer': self.peer_filter or message.get('peer', 'unknown'),
                        'peer_uuid': message.get('peer_uuid', ''),
                        'text': text,
                        'parts': message.get('parts', []),
                        'metadata': message.get('metadata', {}),
                        'matched_keywords': matched_kws,  # v1.6.2: list of actually-matched keyword strings
                        'contains_count': len(self.contains),  # v1.6.2: total keywords searched
                    },
                    message_id=message.get('messageId') or message.get('message_id'),
                ),
                source='content_match',
            )
        except Exception as e:
            log.exception("a2a_content_match trigger %s fire failed: %s", self.id, e)

    async def teardown(self) -> None:
        self._registered = False
