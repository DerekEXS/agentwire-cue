"""AgentWire-Cue v1.4 §2: Host process with 10-step startup + shutdown drain.

P0 #1: minimal viable host with:
- loader.load_all
- trigger scheduler (await setup, asyncio.gather)
- a2a client + listener (18801)
- admin API (19000, Bearer token)
- 30s shutdown drain (P0-P4)
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional

from .a2a_client import A2AClient, A2AListener, FallbackDispatcher, PeerCardCache
from .loader import load_all
from .permission import PermissionEnforcer
from .statechart import now_ms as _now_ms
from .trigger import TriggerScheduler, TriggerSetupError
from .trigger_impl import A2ATrigger, CronTrigger

log = logging.getLogger("agentwire_cue.host")

# v1.5.2: capabilities the host can attest its companion CORE supports.
# Today CORE v1.4.8+ ships ``metadata`` end-to-end (message metadata is
# stored in history and exposed back via /messages/get). When CORE adds
# new capabilities, append the name here and bump cue's tested CORE pin.
KNOWN_CAPABILITIES = frozenset({"metadata"})


def now_ms() -> int:
    return _now_ms()


class Host:
    """v1.4 §2: host process glue.

    Attributes populated by start():
    - plugins: dict[plugin_name -> Plugin]
    - a2a_client: A2AClient
    - a2a_listener: A2AListener
    - admin_api: aiohttp.web.Application
    - scheduler: TriggerScheduler
    - enforcer: PermissionEnforcer
    """

    def __init__(
        self,
        plugin_dir: Path,
        a2a_url: str = 'http://127.0.0.1:18800',
        a2a_token: Optional[str] = None,
        admin_token: Optional[str] = None,
        admin_port: int = 19000,
        admin_host: str = '127.0.0.1',
        a2a_listener_port: int = 18801,
        a2a_listener_host: str = '127.0.0.1',
        persist_allow_parents: Optional[list[str]] = None,
        shutdown_drain_timeout_ms: int = 30_000,
    ):
        self.plugin_dir = plugin_dir
        self.a2a_url = a2a_url
        self.a2a_token = a2a_token
        self.admin_token = admin_token or os.environ.get('ADMIN_TOKEN')
        self.admin_port = admin_port
        self.admin_host = admin_host
        self.a2a_listener_port = a2a_listener_port
        self.a2a_listener_host = a2a_listener_host
        self.persist_allow_parents = persist_allow_parents or []
        self.shutdown_drain_timeout_ms = shutdown_drain_timeout_ms

        self.started_at_ms = now_ms()
        self.plugins: dict = {}
        self.enforcer: Optional[PermissionEnforcer] = None
        self.a2a_client: Optional[A2AClient] = None
        self.a2a_listener: Optional[A2AListener] = None
        self.scheduler: Optional[TriggerScheduler] = None
        self.fallback_dispatcher: Optional[FallbackDispatcher] = None
        self.admin_runner = None
        self.admin_site = None
        self.draining = False
        self.shutdown_event = asyncio.Event()
        self.in_flight_count = 0

    async def start(self) -> None:
        """v1.4 §2.1: 10-step startup + SLO monitoring."""
        # 3. Load plugins (accept either directory or single file)
        if self.plugin_dir.is_file():
            from .loader import load_plugin
            p = load_plugin(self.plugin_dir)
            if p is None:
                raise RuntimeError(f"failed to load plugin: {self.plugin_dir}")
            plugins_list = [p]
        else:
            plugins_list = load_all(self.plugin_dir)
        if not plugins_list:
            log.error("0 plugins successfully loaded from %s", self.plugin_dir)
            raise RuntimeError("no plugins loaded")
        self.plugins = {p.name: p for p in plugins_list}
        # Deduplicate by name (last loaded wins) — log a WARN
        if len(self.plugins) != len(plugins_list):
            log.warning("duplicate plugin names detected: %d loaded, %d unique",
                        len(plugins_list), len(self.plugins))
        log.info("loaded %d plugins: %s", len(self.plugins), list(self.plugins.keys()))

        # v1.5.2: cross-plugin dependency check (fail-soft per plugin).
        # Plugins missing dependencies are marked degraded and excluded
        # from trigger registration below.
        self._check_requires()
        degraded_names = [p.name for p in self.plugins.values() if getattr(p, 'degraded', False)]
        if degraded_names:
            log.error("%d plugins degraded (triggers will not be registered): %s",
                      len(degraded_names), sorted(degraded_names))

        # 4. Setup enforcer + wire a2a client + peer cache
        from .actions import install, install_actions
        self.enforcer = PermissionEnforcer()
        for p in self.plugins.values():
            self.enforcer.register(p.name, p.permissions)
        install_actions()
        install(self.enforcer, {p.name: p for p in self.plugins.values()})

        cache_dir = Path.home() / '.local/share/agentwire-cue/peers'
        peer_cache = PeerCardCache(cache_dir)
        self.a2a_client = A2AClient(
            a2a_url=self.a2a_url,
            a2a_token=self.a2a_token,
            peer_cache=peer_cache,
        )
        self.fallback_dispatcher = FallbackDispatcher(self.a2a_client)

        # Wire statecharts to use a2a_client + fallback_dispatcher
        from .statechart import StatechartEngine
        # v1.4.3: history client + proxy for expressions
        from .history_client import HistoryClient
        history_client = HistoryClient(
            a2a_url=self.a2a_url,
            token=self.a2a_token or '',
        )
        # v1.4.8: aggregate peer alias tables across all loaded plugins. The
        # aggregated map is installed on the shared HistoryClient. Plugins
        # without peers: still load (legacy path). When no peer entries are
        # aggregated at all, HistoryClient stays in the no-alias legacy mode.
        aggregated_aliases: dict[str, dict] = {}
        for p in self.plugins.values():
            for alias, meta in (p.peers or {}).items():
                aggregated_aliases[alias] = meta
        if aggregated_aliases:
            history_client.set_aliases(aggregated_aliases)
            self.a2a_client.set_aliases(aggregated_aliases)
            log.info("installed peer alias table: %s", sorted(aggregated_aliases.keys()))
        for p in self.plugins.values():
            if p.statechart is None:
                p.statechart = StatechartEngine(p, history_client=history_client)
            else:
                p.statechart.history_client = history_client
            p.statechart._a2a_reply = self._wrap_reply(p)
            p.statechart._a2a_send = self._wrap_send(p)

        # 4i. Setup triggers (await setup, asyncio.gather)
        self.scheduler = TriggerScheduler()
        if self.a2a_listener_host == '0.0.0.0':
            log.warning("A2A listener binding 0.0.0.0; protect 18801 with firewall/VPN and Bearer auth")
        self.a2a_listener = A2AListener(
            host=self.a2a_listener_host,
            port=self.a2a_listener_port,
            auth_token=self.admin_token,
        )
        await self._setup_triggers()
        log.info("triggers setup complete for %d plugins", len(self.plugins))

        # 5. Start A2A listener
        await self.a2a_listener.start()
        self.a2a_listener.set_plugins_info([
            {'name': p.name, 'version': p.version} for p in self.plugins.values()
        ])

        # 6. Start admin API (if token configured)
        if self.admin_token:
            await self._start_admin()

        log.info("host started: %d plugins, 18801 listener, %d admin port",
                 len(self.plugins), self.admin_port)

    def _wrap_reply(self, plugin):
        async def reply(message_id: str, text: str):
            log.info("[%s] reply to %s: %s", plugin.name, message_id, text[:80])
        return reply

    def _check_requires(self) -> None:
        """v1.5.2: validate cross-plugin dependencies and mark misses degraded.

        Aggregates the set of loaded plugin names and the union of all
        peer aliases declared in ``spec.peers`` blocks. For each plugin
        with a non-empty ``requires`` block, any missing entry flips
        ``degraded`` to True and writes a comma-joined reason. Multiple
        missing entries across different categories are reported in one
        ``degraded_reason`` so the operator can fix everything in one
        pass instead of restarting the host per error.
        """
        loaded_plugins = set(self.plugins.keys())
        loaded_peers: set[str] = set()
        for p in self.plugins.values():
            loaded_peers.update((getattr(p, 'peers', {}) or {}).keys())

        for p in self.plugins.values():
            req = getattr(p, 'requires', {}) or {}
            if not req:
                continue
            reasons: list[str] = []
            for dep in req.get('plugins', []) or []:
                if dep == p.name:
                    continue
                if dep not in loaded_plugins:
                    reasons.append(f"plugin {dep!r} not loaded")
            for peer in req.get('peers', []) or []:
                if peer not in loaded_peers:
                    reasons.append(f"peer alias {peer!r} not configured")
            for cap in req.get('capabilities', []) or []:
                if cap not in KNOWN_CAPABILITIES:
                    reasons.append(f"capability {cap!r} not supported by host")
            if reasons:
                p.degraded = True
                p.degraded_reason = "; ".join(reasons)
                log.error(
                    "plugin %s degraded: %s", p.name, p.degraded_reason,
                )

    def _wrap_send(self, plugin):
        async def send(peer: str, text: str, metadata=None):
            log.info("[%s] send_a2a to %s: %s (metadata_keys=%s)", plugin.name, peer, text[:80],
                     sorted(metadata.keys()) if isinstance(metadata, dict) else None)
            permission_check = None
            if self.enforcer is not None:
                peers = self.enforcer.get(plugin.name).get('peers', []) or []
                if peers:
                    permission_check = lambda: bool(self.enforcer.check_peer(plugin.name, peer, 'A2A_MESSAGE'))
            result = await self.a2a_client.send_message(
                peer, {'text': text}, metadata=metadata, permission_check=permission_check,
            )
            try:
                from . import observability
                observability.emit(
                    'cue.send_a2a.completed',
                    plugin=plugin.name,
                    target_peer=peer,
                    metadata_keys=sorted(metadata.keys()) if isinstance(metadata, dict) else [],
                    result=getattr(result, 'value', str(result)),
                )
            except Exception:
                pass
            if result.value == 'exhausted':
                log.warning("[%s] send_a2a exhausted, dispatching fallback", plugin.name)
                from .statechart import Event
                ev = Event(
                    type='A2A_EXHAUSTED',
                    payload={'target_peer': peer, 'text': text, 'metadata': metadata},
                )
                await self.fallback_dispatcher.dispatch(plugin, ev)
            return result
        return send

    async def _setup_triggers(self) -> None:
        """v1.3.1 patch 2 D2 + v1.4 §2.1.1: 启动期 <500ms SLO."""
        from .history_client import HistoryClient
        from .trigger_impl import HistoryChangeTrigger
        # v1.4.8: reuse the same HistoryClient (with peer alias table) that
        # was injected into statecharts so trigger polls resolve "Pawly" to
        # the configured uuid. A second client would lack the alias map and
        # emit spurious peer_not_found diagnostics.
        history_client = HistoryClient(
            a2a_url=self.a2a_url,
            token=self.a2a_token or '',
        )
        aggregated_aliases: dict[str, dict] = {}
        for p in self.plugins.values():
            for alias, meta in (p.peers or {}).items():
                aggregated_aliases[alias] = meta
        if aggregated_aliases:
            history_client.set_aliases(aggregated_aliases)
        all_triggers = []
        for p in self.plugins.values():
            if getattr(p, 'degraded', False):
                continue
            for t_def in p.triggers:
                ttype = t_def.type
                if ttype == 'cron':
                    all_triggers.append(CronTrigger(t_def.__dict__, p))
                elif ttype == 'a2a_message_type':
                    all_triggers.append(A2ATrigger(t_def.__dict__, p, a2a_listener=self.a2a_listener))
                elif ttype == 'history_change':  # v1.4.3
                    all_triggers.append(HistoryChangeTrigger(t_def.__dict__, p, history_client=history_client))
                else:
                    raise TriggerSetupError(f"unknown trigger type: {ttype}")
        t0 = time.perf_counter()
        await self.scheduler.register_all(all_triggers)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if elapsed_ms > 500:
            log.warning("startup slow: %.1fms for %d triggers", elapsed_ms, len(all_triggers))

    async def _start_admin(self) -> None:
        from aiohttp import web
        from .admin_api import create_admin_app
        app = create_admin_app(self)
        self.admin_runner = web.AppRunner(app)
        await self.admin_runner.setup()
        if self.admin_host == '0.0.0.0':
            log.warning("admin API binding 0.0.0.0; protect 19000 with firewall/VPN and Bearer auth")
        self.admin_site = web.TCPSite(self.admin_runner, self.admin_host, self.admin_port)
        await self.admin_site.start()

    async def run_forever(self) -> None:
        """v1.4 §2.1 step 8: 主事件循环."""
        await self.shutdown_event.wait()

    async def shutdown(self) -> None:
        """v1.4 §2.4: 30s 分层 drain."""
        if self.draining:
            return
        self.draining = True
        log.info("draining (timeout %dms)...", self.shutdown_drain_timeout_ms)
        total_ms = self.shutdown_drain_timeout_ms
        t0 = now_ms()

        # P0 拒新 (0-1s)
        if self.a2a_listener is not None:
            self.a2a_listener.reject_new = True
        await asyncio.sleep(min(1000, total_ms) / 1000)

        # P1 log
        log.info("draining: %d in-flight", self.in_flight_count)
        await asyncio.sleep(min(1000, max(0, total_ms - 1000)) / 1000)

        # P2 in-flight (wait for in_flight_count to drop to 0)
        deadline = t0 + 27_000
        while self.in_flight_count > 0 and now_ms() < deadline:
            await asyncio.sleep(0.1)

        # P3 persist (强制)
        for p in self.plugins.values():
            try:
                await p.statechart.persist()
            except Exception as e:
                log.warning("persist during shutdown failed for %s: %s", p.name, e)

        # P4 close
        if self.scheduler is not None:
            try:
                await self.scheduler.shutdown()
            except Exception as e:
                log.warning("scheduler shutdown: %s", e)
        if self.a2a_listener is not None:
            try:
                await self.a2a_listener.stop()
            except Exception as e:
                log.warning("a2a listener stop: %s", e)
        if self.a2a_client is not None:
            try:
                await self.a2a_client.close()
            except Exception as e:
                log.warning("a2a client close: %s", e)
        if self.admin_site is not None:
            try:
                await self.admin_site.stop()
            except Exception as e:
                log.warning("admin site stop: %s", e)
        if self.admin_runner is not None:
            try:
                await self.admin_runner.cleanup()
            except Exception as e:
                log.warning("admin runner cleanup: %s", e)
        log.info("drained in %dms", now_ms() - t0)
        self.shutdown_event.set()

    def request_shutdown(self) -> None:
        """Called by signal handler."""
        self.shutdown_event.set()
